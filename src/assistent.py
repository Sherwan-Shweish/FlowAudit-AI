"""
FlowAudit-AI · KI-Assistent & Aktions-Generator (Human-in-the-Loop)
===================================================================

Verbindet die deterministische Prüfkette mit einer abgesicherten Erklärungs-
und Aktionsschicht:

    Datei (XML/PDF) → Parser → 3-Way-Matching → BPMN-Routing
        → KI-Prüfvermerk (LLM mit Guardrails, sonst Regelwerk)
        → verständliche Begründung
        → SAP-Tabellenabgleich (über den Read-Only-SQL-Guardrail)
        → Aktionsentwürfe (E-Mail an Lieferant, Notiz an Einkauf/Fachbereich/Logistik)

**Human-in-the-Loop:** Jeder Aktionsentwurf hat den Status ``ENTWURF``. Nichts
wird automatisch versendet oder gebucht; ein Mensch gibt frei oder verwirft –
mit Name, Zeitstempel und optionalem Kommentar.

**Datensparsamkeit:** Das LLM sieht ausschließlich ``AuditFakten`` – keine
Roh-Freitexte (BT-22/BT-20), keine Bankdaten (``LFBK``/IBAN). Begründung und
Aktionsentwürfe werden deterministisch erzeugt und zusätzlich der
Zahlen-Faktenprüfung unterzogen.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .agent import (
    URTEIL_JE_SCHRITT,
    AuditErgebnis,
    AuditFakten,
    Einstellungen,
    FaktencheckErgebnis,
    FaktenPruefer,
    FlowAuditAgent,
    Gesamturteil,
    Pruefergebnis,
    PruefCode,
    Pruefstatus,
    ReadOnlySQLGuard,
    SQLGuardrailVerletzung,
    WorkflowSchritt,
    ermittle_soll_route,
    fmt_datum,
    fmt_de,
)
from .bpmn_tracker import prozessdiagramm
from .einvoice_parser import EInvoiceFehler, GeparsteRechnung, XMLSicherheitsFehler, lese_rechnung
from .matching_engine import MatchingEngine

WS = WorkflowSchritt

__all__ = ["AktionsEntwurf", "SapAbgleich", "Vorgang", "KIAssistent", "begruende", "erzeuge_aktionen", "sap_abgleich"]

FORMALE_CODES = {PruefCode.PFLICHTFELD_FEHLT, PruefCode.USTID_UNGUELTIG, PruefCode.LEITWEG_ID_FEHLT,
                 PruefCode.STEUERSATZ_UNZULAESSIG, PruefCode.SUMMENFEHLER}
_SICHERE_ID = re.compile(r"[A-Za-z0-9\-]{1,20}")
#: Feste SAP-Schlüssel, die in Texten vorkommen dürfen: RBKP-Status 5, Bewegungsarten 101/122, Werk/Buchungskreis 1000
SAP_KONSTANTEN = [Decimal(5), Decimal(101), Decimal(122), Decimal(1000)]


# ═════════════════════════════════════════════════════════════════════════════
# 1 · SAP-Tabellenabgleich – ausschließlich über den Read-Only-SQL-Guardrail
# ═════════════════════════════════════════════════════════════════════════════

class SapPosition(BaseModel):
    ebelp: int
    matnr: Optional[str]
    text: Optional[str]
    einheit: Optional[str]
    bestellmenge: Decimal
    bestellpreis: Decimal
    we_menge: Decimal
    bereits_berechnet: Decimal
    rechnungsmenge: Optional[Decimal] = None
    rechnungspreis: Optional[Decimal] = None
    status: str = "OK"


class SapAbgleich(BaseModel):
    ebeln: Optional[str]
    kopf: Optional[dict] = None
    positionen: list[SapPosition] = Field(default_factory=list)
    materialbelege: list[dict] = Field(default_factory=list)
    rechnungsbelege: list[dict] = Field(default_factory=list)
    abfragen: list[dict] = Field(default_factory=list)   # SQL + Guardrail-Protokoll

    @property
    def belegbezuege(self) -> list[str]:
        refs: list[str] = []
        if self.kopf:
            refs.append(f"EKKO {self.ebeln} (Bestelldatum {fmt_datum(date.fromisoformat(self.kopf['BEDAT']))}, "
                        f"Einkäufergruppe {self.kopf['EKGRP']})")
        refs += [f"EKPO {self.ebeln}/{p.ebelp:05d} – {p.text}" for p in self.positionen]
        refs += [f"MATDOC {m['MBLNR']}/{m['MJAHR']} (BWA {m['BWART']}, gebucht {fmt_datum(date.fromisoformat(m['BUDAT']))})"
                 for m in self.materialbelege]
        refs += [f"RBKP {r['BELNR']}/{r['GJAHR']} – Rechnung {r['XBLNR']}" for r in self.rechnungsbelege]
        return refs


def _d(x) -> Decimal:
    if x is None:
        return Decimal("0")
    wert = Decimal(str(round(float(x), 6)))
    return wert.quantize(Decimal(1)) if wert == wert.to_integral_value() else wert.normalize()


def sap_abgleich(db_pfad: Path | str, fakten: AuditFakten) -> SapAbgleich:
    """Liest Bestellung, Wareneingänge und Vorrechnungen – jede Abfrage läuft durch den 4-Schichten-Guardrail."""
    ebeln = fakten.rechnung.bestellnummer
    if not ebeln or not _SICHERE_ID.fullmatch(ebeln):
        return SapAbgleich(ebeln=ebeln)  # keine/ungewöhnliche Bestellnummer → keine SQL-Abfrage
    guard = ReadOnlySQLGuard(db_pfad, max_zeilen=200, timeout_sekunden=2.0)
    abfragen = {
        "kopf": "SELECT k.EBELN, k.BEDAT, k.EKGRP, k.LIFNR, l.NAME1 FROM EKKO k "
                f"JOIN LFA1 l ON l.LIFNR = k.LIFNR WHERE k.EBELN = '{ebeln}'",
        "positionen": (
            "SELECT p.EBELP, p.MATNR, p.TXZ01, p.MEINS, p.MENGE AS BESTELLMENGE, round(p.NETPR / p.PEINH, 2) AS NETPR, "
            "coalesce((SELECT sum(CASE m.BWART WHEN '101' THEN m.MENGE WHEN '122' THEN -m.MENGE ELSE 0 END) "
            "FROM MATDOC m WHERE m.EBELN = p.EBELN AND m.EBELP = p.EBELP), 0) AS WE_MENGE, "
            "coalesce((SELECT sum(s.MENGE) FROM RSEG s JOIN RBKP r ON r.BELNR = s.BELNR AND r.GJAHR = s.GJAHR "
            "WHERE s.EBELN = p.EBELN AND s.EBELP = p.EBELP AND r.STBLG IS NULL), 0) AS BERECHNET "
            f"FROM EKPO p WHERE p.EBELN = '{ebeln}' ORDER BY p.EBELP"
        ),
        "materialbelege": f"SELECT DISTINCT MBLNR, MJAHR, BWART, BUDAT FROM MATDOC WHERE EBELN = '{ebeln}' ORDER BY BUDAT, MBLNR",
        "rechnungsbelege": ("SELECT BELNR, GJAHR, XBLNR, BLDAT, RMWWR, ZZ_WF_SCHRITT FROM RBKP "
                            f"WHERE ZZ_EBELN = '{ebeln}' AND STBLG IS NULL ORDER BY BELNR"),
    }
    ergebnis: dict[str, list[dict]] = {}
    for name, sql in abfragen.items():
        try:
            ergebnis[name], _ = guard.ausfuehren(sql)
        except SQLGuardrailVerletzung:
            ergebnis[name] = []
    protokoll = [{"abfrage": name, "sql": sql, "erlaubt": e.erlaubt, "schicht": e.schicht, "sql_hash": e.sql_hash}
                 for (name, sql), e in zip(abfragen.items(), guard.protokoll)]

    befund_je_pos: dict[int, list[str]] = {}
    for p in fakten.pruefungen:
        if p.status is Pruefstatus.FEHLER and p.referenz and (m := re.search(r"EKPO \S+/(\d{5})", p.referenz)):
            befund_je_pos.setdefault(int(m.group(1)), []).append(p.code.value)
    rechnung_je_pos = {pos.bestellposition: pos for pos in fakten.rechnung.positionen if pos.bestellposition}
    positionen = []
    for z in ergebnis["positionen"]:
        rp = rechnung_je_pos.get(z["EBELP"])
        positionen.append(SapPosition(
            ebelp=z["EBELP"], matnr=z["MATNR"], text=z["TXZ01"], einheit=z["MEINS"],
            bestellmenge=_d(z["BESTELLMENGE"]), bestellpreis=_d(z["NETPR"]),
            we_menge=_d(z["WE_MENGE"]), bereits_berechnet=_d(z["BERECHNET"]),
            rechnungsmenge=rp.menge if rp else None, rechnungspreis=rp.einzelpreis_netto if rp else None,
            status=", ".join(befund_je_pos.get(z["EBELP"], [])) or ("OK" if rp else "nicht berechnet"),
        ))
    return SapAbgleich(
        ebeln=ebeln,
        kopf=ergebnis["kopf"][0] if ergebnis["kopf"] else None,
        positionen=positionen,
        materialbelege=ergebnis["materialbelege"],
        rechnungsbelege=ergebnis["rechnungsbelege"],
        abfragen=protokoll,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2 · Verständliche Begründung (deterministisch)
# ═════════════════════════════════════════════════════════════════════════════

def _eur(x: Optional[Decimal]) -> str:
    return f"{fmt_de(x)} EUR" if x is not None else "n. a."


def _menge(x: Optional[Decimal]) -> str:
    if x is None:
        return "n. a."
    return fmt_de(x, 0) if x == x.to_integral_value() else fmt_de(x, 3)


def _satz(p: Pruefergebnis, toleranz: Decimal) -> str:
    ref = f" ({p.referenz})" if p.referenz else ""
    c = p.code
    if c is PruefCode.PREISABWEICHUNG:
        richtung = "über" if (p.abweichung_prozent or 0) > 0 else "unter"
        return (f"Einzelpreis {_eur(p.ist)} liegt {fmt_de(abs(p.abweichung_prozent or Decimal(0)))} % {richtung} dem "
                f"Bestellpreis von {_eur(p.soll)} (Toleranz ±{fmt_de(toleranz, 1)} %); finanzielle Auswirkung "
                f"{_eur(p.abweichung)}{ref}.")
    if c is PruefCode.KEIN_WARENEINGANG:
        return f"Für die berechnete Menge von {_menge(p.ist)} ist kein Wareneingang gebucht{ref}."
    if c is PruefCode.WARENEINGANG_UNVOLLSTAENDIG:
        return (f"Berechnet {_menge(p.ist)}, geliefert und noch nicht berechnet {_menge(p.soll)} – "
                f"es fehlen {_menge(p.abweichung)} im Wareneingang{ref}.")
    if c is PruefCode.MENGENABWEICHUNG:
        return (f"Berechnet {_menge(p.ist)}, offene Bestellmenge {_menge(p.soll)} – "
                f"Mehrmenge {_menge(p.abweichung)}{ref}.")
    if c is PruefCode.SUMMENFEHLER and p.soll is not None:
        return (f"{p.meldung} Ausgewiesen {_eur(p.ist)}, rechnerisch korrekt {_eur(p.soll)} "
                f"(Differenz {_eur(p.abweichung)}){ref}.")
    if c is PruefCode.SKONTO_FRIST:
        return f"{p.meldung} Skontobetrag {_eur(p.ist)}."
    return f"{p.meldung}{ref}"


_EINLEITUNG = {
    WS.TOUCHLESS_FREIGABE: "Die Rechnung ist formal korrekt und stimmt mit Bestellung und Wareneingang überein – "
                           "sie kann ohne manuellen Eingriff gebucht und bezahlt werden (Dunkelverarbeitung).",
    WS.SPERRE_DUBLETTE: "Die Rechnung wurde gesperrt, weil sie bereits gebucht ist – eine Zahlung würde doppelt erfolgen.",
    WS.KLAERUNG_LIEFERANT_FORMFEHLER: "Die Rechnung erfüllt die formalen Anforderungen (§ 14 UStG / EN 16931) nicht "
                                      "und muss vom Lieferanten berichtigt werden; ein Vorsteuerabzug ist so nicht gesichert.",
    WS.COMPLIANCE_MAVERICK_BUYING: "Zur Rechnung gibt es keine gültige Bestellung – der Einkaufsprozess wurde umgangen "
                                   "(Maverick Buying). Der Fachbereich muss den Bedarf bestätigen.",
    WS.SPERRE_FEHLENDER_WARENEINGANG: "Die Rechnung wurde zur Zahlung gesperrt, weil die berechnete Ware (noch) nicht "
                                      "vollständig als Wareneingang gebucht ist.",
    WS.KLAERUNG_EINKAUF_PREIS: "Der berechnete Preis weicht außerhalb der Toleranz vom Bestellpreis ab – der Einkauf "
                               "muss klären, ob eine Preisänderung vereinbart wurde.",
    WS.KLAERUNG_EINKAUF_MENGE: "Es wurde mehr berechnet als bestellt – der Einkauf muss die Mehrmenge klären.",
}


def begruende(fakten: AuditFakten) -> list[str]:
    """Verständliche Begründung in Sätzen: Entscheidung zuerst, dann jeder Befund."""
    schritt, _ = ermittle_soll_route(fakten)
    saetze = [_EINLEITUNG[schritt]]
    saetze += [_satz(p, fakten.preis_toleranz_prozent) for p in fakten.fehler]
    saetze += ["Hinweis: " + _satz(p, fakten.preis_toleranz_prozent) for p in fakten.pruefungen
               if p.status is Pruefstatus.WARNUNG]
    return saetze


# ═════════════════════════════════════════════════════════════════════════════
# 3 · Aktionsentwürfe (Human-in-the-Loop)
# ═════════════════════════════════════════════════════════════════════════════

Aktionstyp = Literal["EMAIL_LIEFERANT", "NOTIZ_EINKAUF", "NOTIZ_FACHBEREICH", "NOTIZ_LOGISTIK",
                     "VERMERK_BUCHHALTUNG", "MELDUNG_COMPLIANCE"]


class AktionsEntwurf(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    typ: Aktionstyp
    kanal: Literal["E-Mail", "Interne Notiz"]
    empfaenger: str
    prioritaet: Literal["HOCH", "NORMAL"] = "NORMAL"
    betreff: str
    text: str
    felder: list[str] = Field(default_factory=list, description="Beanstandete Felder mit BT-Nummer")
    belegbezuege: list[str] = Field(default_factory=list)
    status: Literal["ENTWURF", "FREIGEGEBEN", "VERWORFEN"] = "ENTWURF"
    bearbeitet_von: Optional[str] = None
    bearbeitet_am: Optional[datetime] = None
    kommentar: Optional[str] = None

    def _entscheide(self, status: str, bearbeiter: str, kommentar: Optional[str]) -> "AktionsEntwurf":
        if not bearbeiter or not bearbeiter.strip():
            raise ValueError("Freigabe/Verwerfen erfordert den Namen der bearbeitenden Person (Human-in-the-Loop).")
        if self.status != "ENTWURF":
            raise ValueError(f"Aktion ist bereits {self.status.lower()} und kann nicht erneut entschieden werden.")
        return self.model_copy(update={"status": status, "bearbeitet_von": bearbeiter.strip(),
                                       "bearbeitet_am": datetime.now(timezone.utc), "kommentar": kommentar})

    def freigeben(self, bearbeiter: str, kommentar: Optional[str] = None) -> "AktionsEntwurf":
        """Mensch gibt den Entwurf frei. Versand/Buchung bleibt bewusst ein manueller Schritt."""
        return self._entscheide("FREIGEGEBEN", bearbeiter, kommentar)

    def verwerfen(self, bearbeiter: str, kommentar: Optional[str] = None) -> "AktionsEntwurf":
        return self._entscheide("VERWORFEN", bearbeiter, kommentar)

    def als_text(self) -> str:
        kopf = f"An: {self.empfaenger}\nBetreff: {self.betreff}\n"
        bezug = ("\n\nBelegbezüge:\n" + "\n".join(f"- {b}" for b in self.belegbezuege)) if self.belegbezuege else ""
        return kopf + "\n" + self.text + bezug + "\n"


def _feldbeschreibung(p: Pruefergebnis) -> str:
    ref = p.referenz or "–"
    if p.code is PruefCode.SUMMENFEHLER and p.soll is not None:
        return f"{ref}: {p.meldung} Ausgewiesen {_eur(p.ist)}, korrekt {_eur(p.soll)} (Differenz {_eur(p.abweichung)})."
    if p.code is PruefCode.STEUERSATZ_UNZULAESSIG and p.ist is not None:
        return f"{ref}: {p.meldung} Angegeben {fmt_de(p.ist, 0)} %."
    return f"{ref}: {p.meldung}"


def erzeuge_aktionen(
    fakten: AuditFakten,
    sap: Optional[SapAbgleich] = None,
    lieferant_email: Optional[str] = None,
) -> list[AktionsEntwurf]:
    """Erzeugt Aktionsentwürfe passend zur deterministischen Route (Status: ENTWURF)."""
    r = fakten.rechnung
    schritt, _ = ermittle_soll_route(fakten)
    sap = sap or SapAbgleich(ebeln=r.bestellnummer)
    refs = sap.belegbezuege
    datum = fmt_datum(r.rechnungsdatum) if r.rechnungsdatum else "ohne Datum"
    bezug = f"Rechnung {r.rechnungsnummer} vom {datum} über {_eur(r.summe_brutto)}"
    skonto = [p for p in fakten.pruefungen if p.code is PruefCode.SKONTO_FRIST and p.status is Pruefstatus.WARNUNG]
    prio = "HOCH" if skonto or URTEIL_JE_SCHRITT[schritt] is Gesamturteil.SPERRE else "NORMAL"
    skonto_satz = (f"\n\nDringlichkeit: Skontofrist {fmt_datum(r.skonto_frist)} – {_eur(skonto[0].ist)} Skonto "
                   f"sind gefährdet." if skonto and r.skonto_frist else "")
    gruss = "\n\nMit freundlichen Grüßen\n[Name], Kreditorenbuchhaltung\nRheinMain Industrietechnik GmbH"
    aktionen: list[AktionsEntwurf] = []
    befunde = fakten.fehler

    if schritt is WS.KLAERUNG_LIEFERANT_FORMFEHLER:
        felder = [_feldbeschreibung(p) for p in befunde if p.code in FORMALE_CODES]
        aktionen.append(AktionsEntwurf(
            typ="EMAIL_LIEFERANT", kanal="E-Mail", empfaenger=lieferant_email or f"{r.lieferant_name} (Debitorenbuchhaltung)",
            betreff=f"Bitte um Rechnungsberichtigung – {r.rechnungsnummer}",
            text=(f"Sehr geehrte Damen und Herren,\n\nIhre {bezug} können wir in der vorliegenden Form nicht "
                  f"verarbeiten. Bei der Prüfung nach § 14 UStG und EN 16931 sind folgende Punkte aufgefallen:\n\n"
                  + "\n".join(f"- {f}" for f in felder)
                  + "\n\nBitte senden Sie uns eine berichtigte E-Rechnung (XRechnung oder ZUGFeRD) mit Bezug auf die "
                  "ursprüngliche Rechnungsnummer (Berichtigung nach § 31 Abs. 5 UStDV). Die Zahlung erfolgt nach "
                  "Eingang der korrigierten Rechnung." + gruss),
            felder=felder, belegbezuege=refs, prioritaet=prio,
        ))
    elif schritt is WS.SPERRE_DUBLETTE:
        originale = [p.referenz for p in befunde if p.code is PruefCode.DUBLETTE and p.referenz]
        aktionen.append(AktionsEntwurf(
            typ="VERMERK_BUCHHALTUNG", kanal="Interne Notiz", empfaenger="Kreditorenbuchhaltung",
            betreff=f"Zahlsperre wegen Dublette – {r.rechnungsnummer}",
            text=(f"Die {bezug} wurde mit Zahlsperre R erfasst und nicht zur Zahlung freigegeben. "
                  f"Bereits gebuchter Vergleichsbeleg: {', '.join(originale)}. Bitte Erstbeleg prüfen und die Dublette "
                  f"nach Bestätigung durch den Lieferanten ausbuchen."),
            belegbezuege=originale + refs, prioritaet="HOCH",
        ))
        aktionen.append(AktionsEntwurf(
            typ="EMAIL_LIEFERANT", kanal="E-Mail", empfaenger=lieferant_email or r.lieferant_name,
            betreff=f"Doppelt eingereichte Rechnung {r.rechnungsnummer}",
            text=(f"Sehr geehrte Damen und Herren,\n\nIhre {bezug} liegt uns nach unserer Prüfung bereits vor bzw. "
                  "entspricht einer bereits gebuchten Rechnung (gleicher Betrag und gleiches Rechnungsdatum). Bitte "
                  "bestätigen Sie uns, dass es sich um eine erneute Zusendung handelt – andernfalls senden Sie uns "
                  "bitte eine Erläuterung." + gruss),
            belegbezuege=[], prioritaet="NORMAL",
        ))
    elif schritt is WS.COMPLIANCE_MAVERICK_BUYING:
        aktionen.append(AktionsEntwurf(
            typ="NOTIZ_FACHBEREICH", kanal="Interne Notiz", empfaenger="Bedarfsträger / Kostenstellenverantwortliche",
            betreff=f"Rechnung ohne Bestellung – Bedarfsbestätigung {r.rechnungsnummer}",
            text=(f"Für die {bezug} von {r.lieferant_name} existiert keine gültige Bestellung in EKKO/EKPO. "
                  "Bitte bestätigen Sie den Bedarf und den Leistungsempfang und legen Sie eine nachträgliche "
                  "Bestellanforderung (BANF) an. Bis zur Klärung bleibt die Rechnung geparkt (RBKP, Zahlsperre R).\n\n"
                  "Positionen laut Rechnung:\n" + "\n".join(
                      f"- {p.beschreibung}: {_menge(p.menge)} {p.einheit} × {_eur(p.einzelpreis_netto)}"
                      for p in r.positionen)),
            belegbezuege=["EKKO – keine Bestellung vorhanden", "RBKP – Rechnung geparkt (Zahlsperre R)"],
            prioritaet=prio,
        ))
        aktionen.append(AktionsEntwurf(
            typ="NOTIZ_EINKAUF", kanal="Interne Notiz", empfaenger="Einkauf (Compliance-Kennzahl)",
            betreff=f"Maverick-Buying-Fall {r.lieferant_name}",
            text=(f"Die {bezug} wurde ohne Bestellung ausgelöst. Bitte prüfen, ob der Lieferant {r.lieferant_name} "
                  "in einen Rahmenvertrag bzw. Katalog aufgenommen werden soll."),
            belegbezuege=["LFA1 " + r.lieferant_id], prioritaet="NORMAL",
        ))
    elif schritt in (WS.KLAERUNG_EINKAUF_PREIS, WS.KLAERUNG_EINKAUF_MENGE):
        preis = schritt is WS.KLAERUNG_EINKAUF_PREIS
        relevante = [p for p in befunde if p.code in (PruefCode.PREISABWEICHUNG, PruefCode.MENGENABWEICHUNG)]
        einkaeufer = f"Einkauf, Einkäufergruppe {sap.kopf['EKGRP']}" if sap.kopf else "Einkauf"
        optionen = ("Bitte klären Sie mit dem Lieferanten, ob die Preisänderung vereinbart war:\n"
                    "a) ja → Bestellpreis in der Bestellung anpassen (ME22N), danach Freigabe der Rechnung;\n"
                    "b) nein → Gutschrift über den Mehrbetrag anfordern.") if preis else (
                    "Bitte klären Sie die Mehrmenge:\n"
                    "a) Mehrlieferung akzeptiert → Bestellmenge erhöhen (ME22N);\n"
                    "b) nicht akzeptiert → Rücksendung (BWA 122) und Gutschrift beim Lieferanten anfordern.")
        aktionen.append(AktionsEntwurf(
            typ="NOTIZ_EINKAUF", kanal="Interne Notiz", empfaenger=einkaeufer,
            betreff=f"{'Preisklärung' if preis else 'Mengenklärung'} {r.rechnungsnummer} / Bestellung {r.bestellnummer}",
            text=(f"Die {bezug} von {r.lieferant_name} ist geparkt (RBKP, Zahlsperre R). Abweichung gegenüber der "
                  f"Bestellung {r.bestellnummer}:\n\n" + "\n".join(f"- {_satz(p, fakten.preis_toleranz_prozent)}" for p in relevante)
                  + "\n\n" + optionen + skonto_satz),
            belegbezuege=refs, prioritaet=prio,
        ))
    elif schritt is WS.SPERRE_FEHLENDER_WARENEINGANG:
        relevante = [p for p in befunde if p.code in (PruefCode.KEIN_WARENEINGANG, PruefCode.WARENEINGANG_UNVOLLSTAENDIG)]
        aktionen.append(AktionsEntwurf(
            typ="NOTIZ_LOGISTIK", kanal="Interne Notiz", empfaenger="Logistik / Wareneingang Werk 1000",
            betreff=f"Wareneingang fehlt – Bestellung {r.bestellnummer} ({r.rechnungsnummer})",
            text=(f"Zur {bezug} ist der Wareneingang nicht vollständig gebucht:\n\n"
                  + "\n".join(f"- {_satz(p, fakten.preis_toleranz_prozent)}" for p in relevante)
                  + "\n\nBitte prüfen Sie den physischen Eingang und buchen Sie den Wareneingang (MIGO, BWA 101) "
                  "bzw. melden Sie eine Teil- oder Nichtlieferung an den Einkauf. Die Rechnung bleibt bis dahin "
                  "mit Zahlsperre R geparkt." + skonto_satz),
            belegbezuege=refs, prioritaet=prio,
        ))
    else:  # TOUCHLESS_FREIGABE
        aktionen.append(AktionsEntwurf(
            typ="VERMERK_BUCHHALTUNG", kanal="Interne Notiz", empfaenger="Kreditorenbuchhaltung",
            betreff=f"Dunkelverarbeitung – {r.rechnungsnummer}",
            text=(f"Die {bezug} wurde ohne Befund geprüft und kann gebucht (RBKP-Status 5) und in den nächsten "
                  "Zahlungslauf übernommen werden."
                  + (f" Skonto {fmt_de(r.skonto_prozent)} % bis {fmt_datum(r.skonto_frist)} nutzen."
                     if r.skonto_prozent is not None and r.skonto_frist else "")),
            belegbezuege=refs, prioritaet="NORMAL",
        ))

    freitext = [p for p in fakten.pruefungen if p.code is PruefCode.VERDAECHTIGER_FREITEXT and p.status is Pruefstatus.WARNUNG]
    if freitext:
        aktionen.append(AktionsEntwurf(
            typ="MELDUNG_COMPLIANCE", kanal="Interne Notiz", empfaenger="Compliance / Informationssicherheit",
            betreff=f"Verdacht auf Prompt-Injection in Rechnung {r.rechnungsnummer}",
            text=(f"In der {bezug} von {r.lieferant_name} wurde ein anweisungsähnlicher Freitext erkannt "
                  f"(Fundstelle {freitext[0].referenz}). Der Text wurde nicht an das Sprachmodell übergeben; Prüfung "
                  "und Routing sind davon unberührt. Bitte prüfen, ob der Lieferant informiert oder der Vorfall "
                  "dokumentiert werden soll."),
            belegbezuege=[f"ZFA_PRUEFPROT – VERDAECHTIGER_FREITEXT ({freitext[0].referenz})"], prioritaet="HOCH",
        ))
    return aktionen


# ═════════════════════════════════════════════════════════════════════════════
# 4 · Gesamter Vorgang
# ═════════════════════════════════════════════════════════════════════════════

class Vorgang(BaseModel):
    datei: str
    abgelehnt: bool = False
    ablehnungstyp: Optional[str] = None
    ablehnungsgrund: Optional[str] = None
    rechnung: Optional[GeparsteRechnung] = None
    fakten: Optional[AuditFakten] = None
    schritt: Optional[WorkflowSchritt] = None
    pfad: list[WorkflowSchritt] = Field(default_factory=list)
    urteil: Optional[Gesamturteil] = None
    begruendung: list[str] = Field(default_factory=list)
    audit: Optional[AuditErgebnis] = None
    sap: Optional[SapAbgleich] = None
    aktionen: list[AktionsEntwurf] = Field(default_factory=list)
    faktencheck_assistent: Optional[FaktencheckErgebnis] = None

    def diagramm(self, stil: Literal["bpmn", "epk"] = "bpmn", mit_organisation: bool = True) -> Optional[str]:
        if not self.pfad:
            return None
        titel = None
        if self.fakten:  # Rechnungsnummer stammt aus der Datei → für den Diagrammtitel strikt bereinigen
            titel = "Rechnung " + re.sub(r"[^A-Za-z0-9 ._/\-]", "", self.fakten.rechnung.rechnungsnummer)[:40]
        return prozessdiagramm(self.pfad, stil, titel=titel, mit_organisation=mit_organisation)


class KIAssistent:
    """Orchestriert die komplette Prüfkette für eine Datei – ohne Seiteneffekte auf die Datenbank."""

    def __init__(self, db_pfad: Path | str, einstellungen: Optional[Einstellungen] = None,
                 agent: Optional[FlowAuditAgent] = None, stichtag: Optional[date] = None) -> None:
        self.db_pfad = Path(db_pfad)
        self.einstellungen = einstellungen or Einstellungen.aus_umgebung()
        self.agent = agent or FlowAuditAgent(einstellungen=self.einstellungen)
        self.engine = MatchingEngine(self.db_pfad, self.einstellungen.preis_toleranz_prozent, stichtag)

    def pruefe(self, quelle: Path | str | bytes, dateiname: Optional[str] = None) -> Vorgang:
        name = dateiname or (Path(quelle).name if not isinstance(quelle, bytes) else "upload")
        try:
            rechnung = lese_rechnung(quelle)
        except EInvoiceFehler as exc:
            return self._ablehnung(name, exc)
        return self.bearbeite(rechnung, name)

    def bearbeite(self, rechnung: GeparsteRechnung, name: str) -> Vorgang:
        fakten = self.engine.pruefe(rechnung)
        schritt, pfad = ermittle_soll_route(fakten)
        audit = self.agent.pruefe_rechnung(fakten)
        sap = sap_abgleich(self.db_pfad, fakten)
        aktionen = erzeuge_aktionen(fakten, sap, rechnung.verkaeufer.email)
        begruendung = begruende(fakten)
        return Vorgang(
            datei=name, rechnung=rechnung, fakten=fakten, schritt=schritt, pfad=pfad,
            urteil=URTEIL_JE_SCHRITT[schritt], begruendung=begruendung, audit=audit, sap=sap,
            aktionen=aktionen, faktencheck_assistent=self.faktencheck(fakten, sap, begruendung, aktionen),
        )

    @staticmethod
    def faktencheck(fakten: AuditFakten, sap: SapAbgleich, begruendung: list[str],
                    aktionen: list[AktionsEntwurf]) -> FaktencheckErgebnis:
        """Defense in Depth: auch die deterministisch erzeugten Texte müssen zahlenmäßig belegt sein."""
        quelle = fakten.model_dump()
        for p in quelle["pruefungen"]:
            p.pop("meldung", None)
        pruefer = FaktenPruefer.aus_quelldaten(
            quelle, sap.model_dump(),
            zusatzzahlen=[Decimal(len(fakten.pruefungen)), Decimal(len(fakten.rechnung.positionen)), *SAP_KONSTANTEN],
        )
        texte = {f"begruendung[{i}]": s for i, s in enumerate(begruendung)}
        texte.update({f"aktion[{a.typ}]": a.als_text() for a in aktionen})
        return pruefer.pruefe_texte(texte)

    @staticmethod
    def _ablehnung(name: str, exc: Exception) -> Vorgang:
        sicherheit = isinstance(exc, XMLSicherheitsFehler)
        aktion = AktionsEntwurf(
            typ="MELDUNG_COMPLIANCE" if sicherheit else "EMAIL_LIEFERANT",
            kanal="Interne Notiz" if sicherheit else "E-Mail",
            empfaenger="Compliance / Informationssicherheit" if sicherheit else "Absender der Datei",
            prioritaet="HOCH" if sicherheit else "NORMAL",
            betreff=("Sicherheitsvorfall: abgewiesene Rechnungsdatei" if sicherheit
                     else "Bitte senden Sie eine E-Rechnung (XRechnung oder ZUGFeRD)"),
            text=(f"Die Datei „{name}“ wurde vor jeder fachlichen Verarbeitung abgewiesen: {exc}"
                  if sicherheit else
                  "Sehr geehrte Damen und Herren,\n\nIhre Rechnung liegt uns nur als Bild-PDF ohne strukturierte "
                  "Rechnungsdaten vor. Seit dem 01.01.2025 müssen Unternehmen im Inland E-Rechnungen empfangen können; "
                  "bitte senden Sie uns die Rechnung als XRechnung oder als ZUGFeRD-Rechnung (PDF/A-3 mit eingebetteter "
                  "XML) erneut zu.\n\nMit freundlichen Grüßen\n[Name], Kreditorenbuchhaltung"),
        )
        return Vorgang(datei=name, abgelehnt=True, ablehnungstyp=type(exc).__name__,
                       ablehnungsgrund=str(exc), aktionen=[aktion])
