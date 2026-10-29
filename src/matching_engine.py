"""
FlowAudit-AI · 3-Way-Matching-Engine (SAP S/4HANA MM/FI-Stil)
=============================================================

Gleicht eine geparste E-Rechnung gegen die ERP-Datenbank ab und liefert das
Ergebnis als :class:`src.agent.AuditFakten` – exakt den Vertrag, den der
KI-Audit-Agent erwartet.

Prüfungen (in der Reihenfolge des BPMN-Prozesses):

* **Dublettenprüfung** gegen ``RBKP`` – gleiche Rechnungsnummer (XBLNR) beim
  selben Lieferanten *oder* gleicher Lieferant + Rechnungsdatum + Bruttobetrag.
* **Bestellbezug** gegen ``EKKO`` – keine/unbekannte Bestellnummer oder
  Bestellung eines anderen Lieferanten ⇒ Maverick Buying.
* **3-Way-Matching** je Position gegen ``EKPO`` (Bestellung), ``MATDOC``
  (Wareneingang, BWART 101/122) und ``RSEG`` (bereits berechnete Mengen):
  fehlender WE, unvollständiger WE, Mengen- und Preisabweichung (Toleranz).
* **Skontofrist** relativ zum Stichtag (Hinweis, blockiert nicht).

Die Engine liest ausschließlich über eine schreibgeschützte Verbindung. Das
Buchen des Ergebnisses (``RBKP``/``RSEG``/``ZFA_PRUEFPROT``) ist eine
separate, explizite Funktion.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from .agent import (
    URTEIL_JE_SCHRITT,
    AuditFakten,
    Pruefergebnis,
    PruefCode,
    Pruefstatus,
    Rechnungsfakten,
    Rechnungsposition,
    WorkflowSchritt,
    ermittle_soll_route,
)
from .einvoice_parser import (
    EInvoiceFehler,
    GeparsteRechnung,
    erkenne_prompt_injection,
    lese_rechnung,
    validiere_rechnung,
)

__all__ = ["MatchingEngine", "Verarbeitungsergebnis", "verarbeite_ordner", "buche_ergebnis", "lese_stichtag"]

MENGEN_EPSILON = Decimal("0.0005")
NEUTRALISIERT = "[Positionstext entfernt – Verdacht auf Prompt-Injection]"


def _d(wert: object) -> Decimal:
    """SQLite-REAL → Decimal ohne Gleitkomma-Artefakte."""
    return Decimal(str(round(float(wert), 6))).normalize() if wert is not None else Decimal("0")


def _r2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pr(code: PruefCode, status: Pruefstatus, meldung: str, **kw) -> Pruefergebnis:
    return Pruefergebnis(code=code, status=status, meldung=meldung, **kw)


def lese_stichtag(db_pfad: Path | str) -> Optional[date]:
    """Referenzdatum der Demo-Daten (ZFA_META), falls vorhanden."""
    try:
        con = sqlite3.connect(f"{Path(db_pfad).resolve().as_uri()}?mode=ro", uri=True)
        try:
            zeile = con.execute("SELECT WERT FROM ZFA_META WHERE SCHLUESSEL='REFERENZDATUM'").fetchone()
        finally:
            con.close()
        return date.fromisoformat(zeile[0]) if zeile else None
    except sqlite3.Error:
        return None


@dataclass
class MatchingEngine:
    db_pfad: Path | str
    preis_toleranz_prozent: Decimal = Decimal("2.0")
    stichtag: Optional[date] = None
    skonto_vorwarnung_tage: int = 3

    def __post_init__(self) -> None:
        self.db_pfad = Path(self.db_pfad)
        if not self.db_pfad.exists():
            raise FileNotFoundError(f"ERP-Datenbank nicht gefunden: {self.db_pfad} – zuerst 'python -m src.seed_data' ausführen.")
        self.preis_toleranz_prozent = Decimal(str(self.preis_toleranz_prozent))
        if self.stichtag is None:
            self.stichtag = lese_stichtag(self.db_pfad) or date.today()

    # ---------- Infrastruktur ----------------------------------------------
    def _verbindung(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"{self.db_pfad.resolve().as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    # ---------- Öffentliche API --------------------------------------------
    def pruefe(self, r: GeparsteRechnung, formal: Optional[list[Pruefergebnis]] = None) -> AuditFakten:
        formal = validiere_rechnung(r) if formal is None else formal
        con = self._verbindung()
        try:
            lifnr, lieferant_name = self._ermittle_lieferant(con, r)
            pruefungen = list(formal)
            pruefungen += self._dubletten(con, r, lifnr)
            bestell_ok, bestellbefunde = self._bestellbezug(con, r, lifnr)
            pruefungen += bestellbefunde
            if bestell_ok:
                pruefungen += self._drei_wege(con, r)
            pruefungen += self._skonto(r)
        finally:
            con.close()
        return AuditFakten(
            rechnung=self._rechnungsfakten(r, lifnr, lieferant_name),
            pruefungen=pruefungen,
            preis_toleranz_prozent=self.preis_toleranz_prozent,
        )

    # ---------- Lieferant ---------------------------------------------------
    def _ermittle_lieferant(self, con: sqlite3.Connection, r: GeparsteRechnung) -> tuple[str, str]:
        v = r.verkaeufer
        if v.ust_id:
            z = con.execute("SELECT LIFNR, NAME1 FROM LFA1 WHERE REPLACE(UPPER(STCEG),' ','') = ?",
                            (v.ust_id.replace(" ", "").upper(),)).fetchone()
            if z:
                return z["LIFNR"], z["NAME1"]
        if v.name:
            z = con.execute("SELECT LIFNR, NAME1 FROM LFA1 WHERE LOWER(NAME1) = LOWER(?)", (v.name.strip(),)).fetchone()
            if z:
                return z["LIFNR"], z["NAME1"]
        if r.bestellnummer:
            z = con.execute("SELECT k.LIFNR, l.NAME1 FROM EKKO k JOIN LFA1 l USING (LIFNR) WHERE k.EBELN = ?",
                            (r.bestellnummer,)).fetchone()
            if z:
                return z["LIFNR"], z["NAME1"]
        return "UNBEKANNT", v.name or "Unbekannter Lieferant"

    # ---------- Dublettenprüfung -------------------------------------------
    def _dubletten(self, con: sqlite3.Connection, r: GeparsteRechnung, lifnr: str) -> list[Pruefergebnis]:
        befunde: list[Pruefergebnis] = []
        if r.rechnungsnummer:
            norm = r.rechnungsnummer.replace(" ", "").upper()
            for z in con.execute(
                "SELECT BELNR, GJAHR, XBLNR FROM RBKP WHERE LIFNR = ? AND REPLACE(UPPER(XBLNR),' ','') = ? AND STBLG IS NULL",
                (lifnr, norm),
            ):
                befunde.append(_pr(PruefCode.DUBLETTE, Pruefstatus.FEHLER,
                                   "Rechnungsnummer beim selben Lieferanten bereits gebucht (Duplicate Invoice Check).",
                                   referenz=f"RBKP {z['BELNR']}/{z['GJAHR']} · XBLNR {z['XBLNR']}"))
        if not befunde and r.rechnungsdatum and r.summen.brutto is not None:
            for z in con.execute(
                "SELECT BELNR, GJAHR, XBLNR FROM RBKP WHERE LIFNR = ? AND BLDAT = ? AND ABS(RMWWR - ?) < 0.005 "
                "AND STBLG IS NULL",
                (lifnr, r.rechnungsdatum.isoformat(), float(r.summen.brutto)),
            ):
                befunde.append(_pr(PruefCode.DUBLETTE, Pruefstatus.FEHLER,
                                   f"Dublettenverdacht: gleicher Lieferant, gleiches Rechnungsdatum und gleicher "
                                   f"Bruttobetrag wie bereits gebuchte Rechnung {z['XBLNR']}.",
                                   ist=r.summen.brutto, referenz=f"RBKP {z['BELNR']}/{z['GJAHR']} · XBLNR {z['XBLNR']}"))
        return befunde or [_pr(PruefCode.DUBLETTE, Pruefstatus.OK, "Keine Dublette in RBKP gefunden.")]

    # ---------- Bestellbezug / Maverick Buying ------------------------------
    def _bestellbezug(self, con: sqlite3.Connection, r: GeparsteRechnung, lifnr: str) -> tuple[bool, list[Pruefergebnis]]:
        if not r.bestellnummer:
            return False, [_pr(PruefCode.KEINE_BESTELLUNG, Pruefstatus.FEHLER,
                               "Rechnung ohne Bestellbezug (BT-13 leer) – Maverick Buying.", referenz="BT-13")]
        kopf = con.execute("SELECT LIFNR FROM EKKO WHERE EBELN = ?", (r.bestellnummer,)).fetchone()
        if kopf is None:
            return False, [_pr(PruefCode.KEINE_BESTELLUNG, Pruefstatus.FEHLER,
                               "Referenzierte Bestellnummer existiert nicht in EKKO – Maverick Buying.",
                               referenz=f"EKKO {r.bestellnummer}")]
        if lifnr != "UNBEKANNT" and kopf["LIFNR"] != lifnr:
            return False, [_pr(PruefCode.KEINE_BESTELLUNG, Pruefstatus.FEHLER,
                               "Bestellung ist einem anderen Lieferanten zugeordnet.",
                               referenz=f"EKKO {r.bestellnummer}")]
        return True, [_pr(PruefCode.KEINE_BESTELLUNG, Pruefstatus.OK, "Gültiger Bestellbezug vorhanden.",
                          referenz=f"EKKO {r.bestellnummer}")]

    # ---------- 3-Way-Matching ----------------------------------------------
    def _drei_wege(self, con: sqlite3.Connection, r: GeparsteRechnung) -> list[Pruefergebnis]:
        ebeln = r.bestellnummer
        ekpo = {z["EBELP"]: z for z in con.execute("SELECT * FROM EKPO WHERE EBELN = ?", (ebeln,))}
        befunde: dict[PruefCode, list[Pruefergebnis]] = {c: [] for c in (
            PruefCode.KEIN_WARENEINGANG, PruefCode.WARENEINGANG_UNVOLLSTAENDIG,
            PruefCode.MENGENABWEICHUNG, PruefCode.PREISABWEICHUNG)}
        preis_hinweise: list[Pruefergebnis] = []

        for pos in r.positionen:
            bp = self._zuordnen(pos, ekpo, len(r.positionen))
            if bp is None:
                befunde[PruefCode.MENGENABWEICHUNG].append(_pr(
                    PruefCode.MENGENABWEICHUNG, Pruefstatus.FEHLER,
                    f"Rechnungsposition {pos.id} ist keiner Bestellposition zuordenbar.",
                    soll=Decimal("0"), ist=pos.menge, referenz=f"EKPO {ebeln}"))
                continue
            ref = f"EKPO {ebeln}/{bp['EBELP']:05d}"
            menge = pos.menge or Decimal("0")
            we = _d(con.execute(
                "SELECT COALESCE(SUM(CASE BWART WHEN '101' THEN MENGE WHEN '122' THEN -MENGE ELSE 0 END), 0) "
                "FROM MATDOC WHERE EBELN = ? AND EBELP = ?", (ebeln, bp["EBELP"])).fetchone()[0])
            berechnet = _d(con.execute(
                "SELECT COALESCE(SUM(s.MENGE), 0) FROM RSEG s JOIN RBKP k ON k.BELNR = s.BELNR AND k.GJAHR = s.GJAHR "
                "WHERE s.EBELN = ? AND s.EBELP = ? AND k.STBLG IS NULL", (ebeln, bp["EBELP"])).fetchone()[0])
            offen_bestellt = _d(bp["MENGE"]) - berechnet
            offen_we = we - berechnet

            if we <= 0:
                befunde[PruefCode.KEIN_WARENEINGANG].append(_pr(
                    PruefCode.KEIN_WARENEINGANG, Pruefstatus.FEHLER,
                    f"Für Position {pos.id} ist kein Wareneingang gebucht (MATDOC, BWART 101).",
                    soll=Decimal("0"), ist=menge, referenz=ref))
            elif menge > offen_bestellt + MENGEN_EPSILON:
                befunde[PruefCode.MENGENABWEICHUNG].append(_pr(
                    PruefCode.MENGENABWEICHUNG, Pruefstatus.FEHLER,
                    f"Position {pos.id}: berechnete Menge übersteigt die offene Bestellmenge.",
                    soll=offen_bestellt, ist=menge, abweichung=menge - offen_bestellt, referenz=ref))
            elif menge > offen_we + MENGEN_EPSILON:
                befunde[PruefCode.WARENEINGANG_UNVOLLSTAENDIG].append(_pr(
                    PruefCode.WARENEINGANG_UNVOLLSTAENDIG, Pruefstatus.FEHLER,
                    f"Position {pos.id}: berechnete Menge übersteigt die gelieferte, noch nicht berechnete Menge.",
                    soll=offen_we, ist=menge, abweichung=menge - offen_we, referenz=ref))

            if pos.preis is not None and bp["NETPR"]:
                soll = _r2(_d(bp["NETPR"]) / Decimal(bp["PEINH"] or 1))
                ist = _r2(pos.preis / (pos.basismenge or Decimal("1")))
                prozent = _r2((ist - soll) / soll * 100)
                if abs(prozent) > self.preis_toleranz_prozent:
                    befunde[PruefCode.PREISABWEICHUNG].append(_pr(
                        PruefCode.PREISABWEICHUNG, Pruefstatus.FEHLER,
                        f"Position {pos.id}: Einzelpreis weicht außerhalb der Toleranz vom Bestellpreis ab.",
                        soll=soll, ist=ist, abweichung=_r2((ist - soll) * menge),
                        abweichung_prozent=prozent, referenz=ref))
                elif prozent != 0:
                    preis_hinweise.append(_pr(
                        PruefCode.PREISABWEICHUNG, Pruefstatus.OK,
                        f"Position {pos.id}: Preisabweichung innerhalb der Toleranz.",
                        soll=soll, ist=ist, abweichung=_r2((ist - soll) * menge),
                        abweichung_prozent=prozent, referenz=ref))

        ergebnisse: list[Pruefergebnis] = []
        ok_texte = {
            PruefCode.KEIN_WARENEINGANG: "Wareneingang für alle Positionen gebucht.",
            PruefCode.WARENEINGANG_UNVOLLSTAENDIG: "Berechnete Mengen durch Wareneingänge gedeckt.",
            PruefCode.MENGENABWEICHUNG: "Berechnete Mengen innerhalb der offenen Bestellmengen.",
            PruefCode.PREISABWEICHUNG: "Einzelpreise entsprechen der Bestellung (Toleranz eingehalten).",
        }
        for code, liste in befunde.items():
            if liste:
                ergebnisse += liste
            elif code is PruefCode.PREISABWEICHUNG and preis_hinweise:
                ergebnisse += preis_hinweise
            else:
                ergebnisse.append(_pr(code, Pruefstatus.OK, ok_texte[code]))
        return ergebnisse

    @staticmethod
    def _zuordnen(pos, ekpo: dict, anzahl_rechnungspositionen: int):
        if pos.bestellposition and pos.bestellposition.strip().isdigit():
            treffer = ekpo.get(int(pos.bestellposition))
            if treffer is not None:
                return treffer
        if pos.artikelnummer:
            kandidaten = [z for z in ekpo.values() if z["MATNR"] == pos.artikelnummer]
            if len(kandidaten) == 1:
                return kandidaten[0]
        if len(ekpo) == 1 and anzahl_rechnungspositionen == 1:
            return next(iter(ekpo.values()))
        return None

    # ---------- Skonto ------------------------------------------------------
    def _skonto(self, r: GeparsteRechnung) -> list[Pruefergebnis]:
        s = r.skonto
        if s is None or s.frist is None or r.summen.brutto is None:
            return [_pr(PruefCode.SKONTO_FRIST, Pruefstatus.OK, "Keine Skontovereinbarung.")]
        betrag = _r2((s.basisbetrag or r.summen.brutto) * s.prozent / 100)
        rest = (s.frist - self.stichtag).days
        if rest < 0:
            return [_pr(PruefCode.SKONTO_FRIST, Pruefstatus.WARNUNG,
                        "Skontofrist ist bereits abgelaufen – Skonto nicht mehr realisierbar.",
                        ist=betrag, referenz="BT-20")]
        if rest <= self.skonto_vorwarnung_tage:
            return [_pr(PruefCode.SKONTO_FRIST, Pruefstatus.WARNUNG,
                        "Skontofrist läuft in Kürze ab – bei Verzögerung droht Skontoverlust.",
                        ist=betrag, referenz="BT-20")]
        return [_pr(PruefCode.SKONTO_FRIST, Pruefstatus.OK, "Skontofrist ausreichend.", ist=betrag)]

    # ---------- Abbildung auf den Agenten-Vertrag ---------------------------
    @staticmethod
    def _rechnungsfakten(r: GeparsteRechnung, lifnr: str, lieferant_name: str) -> Rechnungsfakten:
        positionen = []
        for i, pos in enumerate(r.positionen, start=1):
            text = pos.bezeichnung or "(ohne Bezeichnung)"
            if erkenne_prompt_injection(text):
                text = NEUTRALISIERT
            positionen.append(Rechnungsposition(
                position=int(pos.id) if pos.id.isdigit() and int(pos.id) > 0 else i,
                material=pos.artikelnummer,
                beschreibung=text[:200],
                menge=pos.menge if pos.menge is not None else Decimal("0"),
                einheit=pos.einheit,
                einzelpreis_netto=_r2(pos.preis / pos.basismenge) if pos.preis is not None else Decimal("0"),
                steuersatz=pos.steuersatz if pos.steuersatz is not None else Decimal("0"),
                betrag_netto=pos.nettobetrag if pos.nettobetrag is not None else Decimal("0"),
                bestellposition=int(pos.bestellposition) if pos.bestellposition and pos.bestellposition.isdigit() else None,
            ))
        return Rechnungsfakten(
            rechnungsnummer=(r.rechnungsnummer or "OHNE-NUMMER")[:64],
            rechnungsdatum=r.rechnungsdatum,
            format=r.format,
            lieferant_id=lifnr,
            lieferant_name=lieferant_name,
            ust_id=r.verkaeufer.ust_id,
            leitweg_id=r.kaeuferreferenz,
            bestellnummer=r.bestellnummer,
            waehrung=r.waehrung or "EUR",
            summe_netto=r.summen.netto,
            summe_steuer=r.summen.steuer,
            summe_brutto=r.summen.brutto,
            faelligkeit=r.faelligkeit,
            skonto_prozent=r.skonto.prozent if r.skonto else None,
            skonto_frist=r.skonto.frist if r.skonto else None,
            positionen=positionen,
        )


# ═════════════════════════════════════════════════════════════════════════════
# Batch-Verarbeitung & Buchung
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Verarbeitungsergebnis:
    datei: str
    rechnung: Optional[GeparsteRechnung] = None
    fakten: Optional[AuditFakten] = None
    schritt: Optional[WorkflowSchritt] = None
    pfad: list[WorkflowSchritt] = field(default_factory=list)
    fehler: Optional[str] = None
    eingang: Optional[datetime] = None

    @property
    def abgelehnt(self) -> bool:
        return self.fehler is not None


def verarbeite_datei(pfad: Path, engine: MatchingEngine, eingang: Optional[datetime] = None) -> Verarbeitungsergebnis:
    try:
        rechnung = lese_rechnung(pfad)
    except EInvoiceFehler as exc:
        return Verarbeitungsergebnis(datei=pfad.name, fehler=f"{type(exc).__name__}: {exc}", eingang=eingang)
    fakten = engine.pruefe(rechnung)
    schritt, weg = ermittle_soll_route(fakten)
    return Verarbeitungsergebnis(pfad.name, rechnung, fakten, schritt, weg, eingang=eingang)


def verarbeite_ordner(
    ordner: Path | str,
    db_pfad: Path | str,
    *,
    buchen: bool = False,
    preis_toleranz_prozent: Decimal = Decimal("2.0"),
    stichtag: Optional[date] = None,
) -> list[Verarbeitungsergebnis]:
    """Verarbeitet alle E-Rechnungen (XML und ZUGFeRD-PDF) eines Ordners in Dateinamen-Reihenfolge.

    Mit ``buchen=True`` wird jedes Ergebnis sofort in RBKP/RSEG/ZFA_PRUEFPROT
    geschrieben – spätere Rechnungen sehen also frühere (realistische Dublettenlogik).
    """
    ordner = Path(ordner)
    eingaenge: dict[str, datetime] = {}
    manifest = ordner / "manifest.json"
    if manifest.exists():
        daten = json.loads(manifest.read_text(encoding="utf-8"))
        eingaenge = {e["datei"]: datetime.fromisoformat(e["eingang"]) for e in daten.get("rechnungen", [])}
    engine = MatchingEngine(db_pfad, preis_toleranz_prozent, stichtag)
    ergebnisse = []
    dateien = sorted(p for p in ordner.iterdir() if p.suffix.lower() in {".xml", ".pdf"})
    for pfad in dateien:
        e = verarbeite_datei(pfad, engine, eingaenge.get(pfad.name))
        if buchen and not e.abgelehnt:
            buche_ergebnis(db_pfad, e)
        ergebnisse.append(e)
    return ergebnisse


def buche_ergebnis(db_pfad: Path | str, e: Verarbeitungsergebnis) -> str:
    """Schreibt Rechnungskopf (RBKP), Positionen (RSEG) und Prüfprotokoll. Rückgabe: BELNR."""
    assert e.fakten is not None and e.schritt is not None and e.rechnung is not None
    r, f = e.rechnung, e.fakten.rechnung
    urteil = URTEIL_JE_SCHRITT[e.schritt].value
    eingang = e.eingang or datetime.now().replace(microsecond=0)
    abschluss = eingang + timedelta(minutes=1) if urteil == "FREIGABE" else None
    gjahr = (f.rechnungsdatum or eingang.date()).year
    con = sqlite3.connect(db_pfad)
    try:
        with con:
            belnr = str(int(con.execute("SELECT COALESCE(MAX(CAST(BELNR AS INTEGER)), 5105600000) FROM RBKP").fetchone()[0]) + 1)
            con.execute(
                "INSERT INTO RBKP VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (belnr, gjahr, f.rechnungsnummer, f.lieferant_id, "1000",
                 f.rechnungsdatum.isoformat() if f.rechnungsdatum else None, eingang.date().isoformat(),
                 float(f.summe_brutto or 0), float(f.summe_steuer or 0), float(f.summe_netto or 0), f.waehrung,
                 "5" if urteil == "FREIGABE" else "A", None if urteil == "FREIGABE" else "R", None,
                 f.format, f.bestellnummer, e.schritt.value, urteil,
                 eingang.isoformat(timespec="seconds"), abschluss.isoformat(timespec="seconds") if abschluss else None,
                 float(f.skonto_prozent) if f.skonto_prozent is not None else None,
                 f.skonto_frist.isoformat() if f.skonto_frist else None, r.sha256),
            )
            for buzei, pos in enumerate(f.positionen, start=1):
                con.execute("INSERT INTO RSEG VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (belnr, gjahr, buzei, f.bestellnummer if pos.bestellposition else None,
                             pos.bestellposition, pos.material, float(pos.menge), pos.einheit,
                             float(pos.betrag_netto), {Decimal("19"): "V1", Decimal("7"): "V2"}.get(pos.steuersatz, "V0")))
            jetzt = datetime.now().isoformat(timespec="seconds")
            fakten_hash = e.fakten.eingabe_hash()  # SHA-256 des vollständigen Prüfstands (Revisionsnachweis)

            def zahl(x: Optional[Decimal]) -> Optional[float]:
                return float(x) if x is not None else None

            for p in e.fakten.pruefungen:
                con.execute("INSERT INTO ZFA_PRUEFPROT VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (belnr, gjahr, f.rechnungsnummer, p.code.value, p.status.value, p.meldung,
                             zahl(p.soll), zahl(p.ist), zahl(p.abweichung), zahl(p.abweichung_prozent),
                             p.referenz, jetzt, fakten_hash))
    finally:
        con.close()
    return belnr


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="FlowAudit-AI: E-Rechnungen prüfen (3-Way-Matching)")
    ap.add_argument("--ordner", default="data/invoices")
    ap.add_argument("--db", default="data/flowaudit_erp.db")
    ap.add_argument("--buchen", action="store_true", help="Ergebnisse in RBKP/RSEG/ZFA_PRUEFPROT buchen")
    a = ap.parse_args()
    erwartet = {}
    mf = Path(a.ordner) / "manifest.json"
    if mf.exists():
        erwartet = {e["datei"]: e["erwartete_route"] for e in json.loads(mf.read_text("utf-8"))["rechnungen"]}
    print(f"{'Datei':42} {'Rechnung':14} {'Route':32} Soll")
    for e in verarbeite_ordner(a.ordner, a.db, buchen=a.buchen):
        route = e.schritt.value if e.schritt else "ABGELEHNT"
        marke = "✓" if erwartet.get(e.datei) in (None, route) else "✗"
        nr = e.fakten.rechnung.rechnungsnummer if e.fakten else "–"
        print(f"{e.datei:42} {nr:14} {route:32} {marke}")
        if e.fehler:
            print(f"{'':42} ↳ {e.fehler[:110]}")
