"""Audit-Copilot: deterministische Bausteine für Prüf-Scan, Kernaussagen, Ablauf und Prüftrichter.

Alles hier wird aus den Prüfergebnissen der Engines abgeleitet – ohne LLM, ohne Streamlit. Das Cockpit
(``app.py``) stellt die Ergebnisse nur dar; die Tests prüfen sie unabhängig von der Oberfläche.

Leitfrage des Agenten: **„Darf diese Rechnung bezahlt werden – und wenn nicht, warum?“**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .agent import (
    URTEIL_JE_SCHRITT,
    PruefCode,
    Pruefergebnis,
    Pruefstatus,
    WorkflowSchritt,
    fmt_datum,
    fmt_de,
)
from .assistent import Vorgang
from .matching_engine import Verarbeitungsergebnis

Ton = Literal["neutral", "gut", "schlecht", "warnung", "akzent", "leise"]
Zustand = Literal["fertig", "aktiv", "offen"]
WS = WorkflowSchritt

FORMAL = (PruefCode.PFLICHTFELD_FEHLT, PruefCode.USTID_UNGUELTIG, PruefCode.LEITWEG_ID_FEHLT,
          PruefCode.STEUERSATZ_UNZULAESSIG, PruefCode.SUMMENFEHLER)
MATCHING = (PruefCode.KEIN_WARENEINGANG, PruefCode.WARENEINGANG_UNVOLLSTAENDIG, PruefCode.PREISABWEICHUNG,
            PruefCode.MENGENABWEICHUNG)
ABLAUF = ("Eingang", "Formalprüfung", "Matching", "Klärung", "Freigabe")
_KURZ = {
    PruefCode.KEIN_WARENEINGANG: "Wareneingang fehlt",
    PruefCode.WARENEINGANG_UNVOLLSTAENDIG: "Teil-Lieferung",
    PruefCode.PREISABWEICHUNG: "Preis",
    PruefCode.MENGENABWEICHUNG: "Menge",
}


def _befunde(pruefungen: Sequence[Pruefergebnis], codes: Sequence[PruefCode],
             status: Pruefstatus) -> list[Pruefergebnis]:
    return [p for p in pruefungen if p.code in codes and p.status is status]


def _anzahl(n: int, einzahl: str, mehrzahl: str) -> str:
    return f"{n} {einzahl if n == 1 else mehrzahl}"


# =============================================================================
# Prüf-Scan einer Rechnung
# =============================================================================


def pruef_scan(v: Vorgang, risiken: Sequence | None = None) -> list[tuple[str, str, Ton]]:
    """Checkliste (Prüfschritt, Ergebnis, Ton) in der Reihenfolge des BPMN-Prozesses.

    ``risiken``: Treffer der Präventionsregeln (``src.praevention``) – mit Angabe erscheint eine Zeile „Prävention“.
    """
    if v.abgelehnt or v.fakten is None:
        return [("Eingang & Format", f"abgewiesen · {v.ablehnungstyp or 'keine E-Rechnung'}", "schlecht"),
                ("Weitere Prüfungen", "entfallen – keine strukturierten Daten", "leise")]
    f, r = v.fakten, v.fakten.rechnung
    p = f.pruefungen
    scan: list[tuple[str, str, Ton]] = []
    container = v.rechnung.container if v.rechnung and v.rechnung.container else "XML"
    scan.append(("Eingang & Format", f"{r.format} · {container}", "gut"))

    dublette = [x for x in p if x.code is PruefCode.DUBLETTE]
    if any(x.status is Pruefstatus.FEHLER for x in dublette):
        ref = next(x.referenz for x in dublette if x.status is Pruefstatus.FEHLER)
        scan.append(("Dublettenprüfung", f"Dublette · {ref or 'bereits gebucht'}", "schlecht"))
    elif any(x.status is Pruefstatus.WARNUNG for x in dublette):
        scan.append(("Dublettenprüfung", "Verdacht – Hinweis", "warnung"))
    else:
        scan.append(("Dublettenprüfung", "keine Dublette", "gut"))

    formfehler = _befunde(p, FORMAL, Pruefstatus.FEHLER)
    formhinweise = _befunde(p, FORMAL, Pruefstatus.WARNUNG)
    if formfehler:
        scan.append(("Formalprüfung § 14 UStG", _anzahl(len(formfehler), "Formfehler", "Formfehler"), "schlecht"))
    elif formhinweise:
        scan.append(("Formalprüfung § 14 UStG", _anzahl(len(formhinweise), "Hinweis", "Hinweise"), "warnung"))
    else:
        scan.append(("Formalprüfung § 14 UStG", "Pflichtangaben vollständig", "gut"))

    if any(x.code is PruefCode.KEINE_BESTELLUNG and x.status is Pruefstatus.FEHLER for x in p):
        scan.append(("Bestellbezug", "keine Bestellung (Maverick Buying)", "schlecht"))
        scan.append(("3-Way-Matching", "nicht möglich ohne Bestellung", "leise"))
    else:
        scan.append(("Bestellbezug", f"Bestellung {r.bestellnummer or '–'}", "gut"))
        abweichungen = _befunde(p, MATCHING, Pruefstatus.FEHLER)
        if abweichungen:
            gruende = list(dict.fromkeys(_KURZ[x.code] for x in abweichungen))
            scan.append(("3-Way-Matching", "Abweichung: " + ", ".join(gruende), "schlecht"))
        else:
            scan.append(("3-Way-Matching", f"in Toleranz (±{fmt_de(f.preis_toleranz_prozent, 1)} %)", "gut"))

    if any(x.code is PruefCode.VERDAECHTIGER_FREITEXT and x.status is Pruefstatus.WARNUNG for x in p):
        scan.append(("Freitext BT-22", "Steuerungsmuster erkannt – isoliert", "warnung"))
    else:
        scan.append(("Freitext BT-22", "unauffällig", "gut"))

    if risiken is not None:
        if risiken:
            titel = list(dict.fromkeys(h.titel for h in risiken))
            scan.append(("Prävention", ", ".join(titel),
                         "schlecht" if any(h.sperrt_zahlung for h in risiken) else "warnung"))
        else:
            scan.append(("Prävention", "keine Auffälligkeiten", "gut"))

    if v.audit is not None:
        fc = v.audit.faktencheck
        scan.append(("Faktenprüfung Prüfvermerk",
                     f"{fc.geprueft} Zahlen/Daten belegt" if fc.bestanden else "nicht bestanden – Ersatztext",
                     "gut" if fc.bestanden else "schlecht"))
    return scan


# =============================================================================
# Kernaussagen (deterministisch) und Ablauf
# =============================================================================


def kernaussagen(v: Vorgang, max_befunde: int = 3) -> list[str]:
    """Antwort auf die Leitfrage in wenigen Sätzen – ausschließlich aus Prüfprotokoll und Rechnungsdaten."""
    if v.abgelehnt or v.fakten is None:
        return [f"Nein – die Datei wurde abgewiesen: {v.ablehnungsgrund or v.ablehnungstyp}",
                "Ohne strukturierte E-Rechnung (XRechnung oder ZUGFeRD mit XML) findet keine Prüfung statt."]
    r = v.fakten.rechnung
    betrag = f"{fmt_de(r.summe_brutto or Decimal(0))} € brutto"
    if v.schritt is WS.TOUCHLESS_FREIGABE:
        saetze = [f"Ja – die Rechnung {r.rechnungsnummer} ({betrag}) darf ohne manuellen Eingriff bezahlt werden.",
                  "Formalprüfung, Dublettenprüfung und 3-Way-Matching sind ohne Befund."]
    else:
        urteil = URTEIL_JE_SCHRITT[v.schritt].value if v.schritt else "KLAERUNG"
        wort = "gesperrt" if urteil == "SPERRE" else "zur Klärung geroutet"
        saetze = [f"Nein, noch nicht – die Rechnung {r.rechnungsnummer} ({betrag}) ist {wort}."]
        saetze += v.begruendung[1:1 + max_befunde]
    if r.skonto_prozent and r.skonto_frist and r.summe_brutto:
        skonto = r.summe_brutto * r.skonto_prozent / 100
        zusatz = "" if v.schritt is WS.TOUCHLESS_FREIGABE else " – gefährdet, solange die Klärung offen ist"
        saetze.append(f"Skonto {fmt_de(r.skonto_prozent, 1)} % ({fmt_de(skonto)} €) bis "
                      f"{fmt_datum(r.skonto_frist)}{zusatz}.")
    return saetze


def ablauf_status(v: Vorgang, entschieden: bool = False, klaerstatus: str | None = None) -> list[tuple[str, Zustand]]:
    """Eingang → Formalprüfung → Matching → Klärung → Freigabe.

    ``entschieden``: alle Aktionsentwürfe der Rechnung sind namentlich freigegeben oder verworfen.
    ``klaerstatus``: Status des Klärfalls (offen | in Klärung | gelöst) – hat Vorrang vor ``entschieden``.
    """
    if v.abgelehnt or v.fakten is None:
        return [("Eingang", "aktiv")] + [(s, "offen") for s in ABLAUF[1:]]
    pfad = set(v.pfad)
    formal: Zustand = "fertig" if WS.FORMALE_PRUEFUNG in pfad else "offen"
    matching: Zustand = "fertig" if WS.BESTELLBEZUG_PRUEFEN in pfad else "offen"
    if klaerstatus is not None:
        klaerung: Zustand = "fertig" if klaerstatus == "gelöst" else "aktiv"
        freigabe: Zustand = "fertig" if klaerstatus == "gelöst" else "offen"
    elif v.schritt is WS.TOUCHLESS_FREIGABE:
        klaerung = "fertig"
        freigabe = "fertig"
    else:
        klaerung = "fertig" if entschieden else "aktiv"
        freigabe = "aktiv" if entschieden else "offen"
    return [("Eingang", "fertig"), ("Formalprüfung", formal), ("Matching", matching), ("Klärung", klaerung),
            ("Freigabe", freigabe)]


# =============================================================================
# Prüftrichter über einen Batchlauf
# =============================================================================


@dataclass(frozen=True)
class Pruefzahlen:
    eingegangen: int
    abgewiesen: int
    formal_gueltig: int        # lesbar, keine Dublette, Pflichtangaben vollständig (Gateway „formal ok“)
    gematcht: int              # Bestellung und Wareneingang zugeordnet
    in_toleranz: int           # Preis und Menge innerhalb der Toleranz
    zahlungsfreigegeben: int   # Touchless-Freigabe (Dunkelverarbeitung)
    klaerung: int              # Klärung oder Sperre (ohne abgewiesene Dateien)
    sperre: int

    @property
    def stufen(self) -> list[tuple[str, int]]:
        return [("eingegangen", self.eingegangen), ("formal gültig", self.formal_gueltig),
                ("gematcht", self.gematcht), ("in Toleranz", self.in_toleranz),
                ("zahlungsfreigegeben", self.zahlungsfreigegeben)]


def pruefzahlen(ergebnisse: Sequence[Verarbeitungsergebnis]) -> Pruefzahlen:
    """Zählt die Belege je Stufe entlang der BPMN-Gateways (deterministisch aus der Soll-Route)."""
    geprueft = [e for e in ergebnisse if not e.abgelehnt and e.schritt is not None]
    formal_raus = {WS.SPERRE_DUBLETTE, WS.KLAERUNG_LIEFERANT_FORMFEHLER}
    match_raus = formal_raus | {WS.COMPLIANCE_MAVERICK_BUYING, WS.SPERRE_FEHLENDER_WARENEINGANG}
    toleranz_raus = match_raus | {WS.KLAERUNG_EINKAUF_PREIS, WS.KLAERUNG_EINKAUF_MENGE}
    touchless = sum(e.schritt is WS.TOUCHLESS_FREIGABE for e in geprueft)
    return Pruefzahlen(
        eingegangen=len(ergebnisse),
        abgewiesen=len(ergebnisse) - len(geprueft),
        formal_gueltig=sum(e.schritt not in formal_raus for e in geprueft),
        gematcht=sum(e.schritt not in match_raus for e in geprueft),
        in_toleranz=sum(e.schritt not in toleranz_raus for e in geprueft),
        zahlungsfreigegeben=touchless,
        klaerung=len(geprueft) - touchless,
        sperre=sum(e.schritt in (WS.SPERRE_DUBLETTE, WS.SPERRE_FEHLENDER_WARENEINGANG) for e in geprueft),
    )
