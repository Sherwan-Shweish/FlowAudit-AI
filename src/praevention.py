"""Betrugs- und Fehlerprävention: deterministische Regeln mit Begründung (kein LLM, kein Scoring-Modell).

Regeln (je Treffer: Begründung in Klartext, Belegbezüge, empfohlene Maßnahme):

* **Dublettenverdacht** – gleicher Lieferant und gleicher Betrag (±0,5 %) sowie zeitliche Nähe (≤ 14 Tage) oder
  ähnliche Rechnungsnummer (≥ 85 % Übereinstimmung). Ergänzt die exakte Dublettenprüfung der Matching-Engine.
* **Bankverbindung kürzlich geändert** – Änderungsbeleg zur IBAN des Lieferanten (CDHDR/CDPOS, FK02) innerhalb
  von 30 Tagen vor Rechnungseingang bzw. Stichtag (Muster „CEO-/Lieferantenbetrug“).
* **Rechnung ohne Bestellung** – kein Bestellbezug: Bedarf und Preis wurden nicht vorab genehmigt.
* **Glatter Betrag** – Nettobetrag ist ein Vielfaches von 500 € (ab 1.000 €) und nicht aus Menge × Bestellpreis
  entstanden (ohne Bestellung oder Pauschale mit Menge 1) – typisch für Schein- oder Pauschalrechnungen.

Treffer der Schwere „hoch“ sperren den Zahlungsvorschlag, bis die Maßnahme dokumentiert ist (siehe ``zahlung.py``).
Das BPMN-Routing bleibt unverändert – die Regeln sind eine zusätzliche Kontrollebene.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from .agent import fmt_datum, fmt_de
from .assistent import Vorgang

Schwere = Literal["hoch", "mittel", "niedrig"]
BETRAG_TOLERANZ_PROZENT = Decimal("0.5")
DATUM_FENSTER_TAGE = 14
NUMMER_AEHNLICHKEIT = 0.85
BANK_FENSTER_TAGE = 30
RUNDUNG_BASIS = Decimal("500")
RUNDUNG_AB = Decimal("1000")

REGELN = {
    "DUBLETTENVERDACHT": ("Dublettenverdacht", "hoch"),
    "BANKDATEN_GEAENDERT": ("Bankverbindung kürzlich geändert", "hoch"),
    "OHNE_BESTELLUNG": ("Rechnung ohne Bestellung", "mittel"),
    "GLATTER_BETRAG": ("Ungewöhnlich glatter Betrag", "mittel"),
}
MASSNAHMEN = {
    "DUBLETTENVERDACHT": "Beide Belege vergleichen; vor Zahlung bestätigen, dass es sich um verschiedene Leistungen handelt.",
    "BANKDATEN_GEAENDERT": "Rückruf beim Lieferanten über die im Stamm hinterlegte Telefonnummer (nicht aus der "
                           "Rechnung) und Bestätigung der neuen IBAN dokumentieren – erst dann zahlen.",
    "OHNE_BESTELLUNG": "Bedarf und Preis vom Fachbereich bestätigen lassen (nachträgliche BANF/Bestellung).",
    "GLATTER_BETRAG": "Leistungsnachweis (Stundenzettel, Abnahmeprotokoll) anfordern und mit dem Fachbereich abgleichen.",
}


@dataclass(frozen=True)
class Beleg:
    """Minimale Sicht auf eine Eingangsrechnung – aus RBKP/RSEG oder aus einem geprüften Vorgang."""

    xblnr: str
    lifnr: str
    bldat: date | None
    eingang: date | None
    brutto: Decimal
    netto: Decimal
    ebeln: str | None
    mengen: tuple[Decimal, ...] = ()
    belnr: str | None = None


@dataclass(frozen=True)
class Risikohinweis:
    regel: str
    xblnr: str
    lifnr: str
    betrag: Decimal
    begruendung: str
    belege: list[str] = field(default_factory=list)

    @property
    def titel(self) -> str:
        return REGELN[self.regel][0]

    @property
    def schwere(self) -> Schwere:
        return REGELN[self.regel][1]  # type: ignore[return-value]

    @property
    def massnahme(self) -> str:
        return MASSNAHMEN[self.regel]

    @property
    def sperrt_zahlung(self) -> bool:
        return self.schwere == "hoch"


@dataclass(frozen=True)
class Bankaenderung:
    lifnr: str
    datum: date
    benutzer: str
    alt: str
    neu: str
    changenr: str


# =============================================================================
# Daten laden (schreibgeschützt)
# =============================================================================


def _lesen(db_pfad: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(f"{Path(db_pfad).resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def lade_belege(db_pfad: Path | str) -> list[Beleg]:
    con = _lesen(db_pfad)
    try:
        mengen: dict[tuple[str, int], list[Decimal]] = {}
        for z in con.execute("SELECT BELNR, GJAHR, MENGE FROM RSEG ORDER BY BELNR, BUZEI"):
            mengen.setdefault((z["BELNR"], z["GJAHR"]), []).append(Decimal(str(z["MENGE"])))
        return [Beleg(z["XBLNR"], z["LIFNR"], date.fromisoformat(z["BLDAT"]),
                      datetime.fromisoformat(z["ZZ_EINGANG"]).date() if z["ZZ_EINGANG"] else None,
                      Decimal(str(round(z["RMWWR"], 2))), Decimal(str(round(z["ZZ_NETTO"] or 0, 2))), z["ZZ_EBELN"],
                      tuple(mengen.get((z["BELNR"], z["GJAHR"]), [])), z["BELNR"])
                for z in con.execute("SELECT * FROM RBKP WHERE STBLG IS NULL ORDER BY ZZ_EINGANG, BELNR")]
    finally:
        con.close()


def lade_bankaenderungen(db_pfad: Path | str) -> list[Bankaenderung]:
    con = _lesen(db_pfad)
    try:
        return [Bankaenderung(z["OBJECTID"], date.fromisoformat(z["UDATE"]), z["USERNAME"], z["VALUE_OLD"] or "",
                              z["VALUE_NEW"] or "", z["CHANGENR"])
                for z in con.execute(
                    "SELECT h.OBJECTID, h.UDATE, h.USERNAME, h.CHANGENR, p.VALUE_OLD, p.VALUE_NEW FROM CDHDR h "
                    "JOIN CDPOS p ON p.OBJECTCLAS = h.OBJECTCLAS AND p.OBJECTID = h.OBJECTID AND p.CHANGENR = h.CHANGENR "
                    "WHERE h.OBJECTCLAS = 'KRED' AND p.TABNAME = 'LFBK' AND p.FNAME = 'IBAN' ORDER BY h.UDATE")]
    except sqlite3.OperationalError:  # ältere Datenbank ohne Änderungsbelege
        return []
    finally:
        con.close()


def beleg_aus_vorgang(v: Vorgang) -> Beleg | None:
    if v.abgelehnt or v.fakten is None:
        return None
    r = v.fakten.rechnung
    return Beleg(r.rechnungsnummer, r.lieferant_id, r.rechnungsdatum, None, r.summe_brutto or Decimal(0),
                 r.summe_netto or Decimal(0), r.bestellnummer, tuple(p.menge for p in r.positionen))


# =============================================================================
# Regeln
# =============================================================================


def _norm(nummer: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", nummer.upper())


def nummer_aehnlichkeit(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _dublettenverdacht(beleg: Beleg, bestand: Sequence[Beleg]) -> list[Risikohinweis]:
    treffer = []
    for alt in bestand:
        if alt is beleg or alt.lifnr != beleg.lifnr or (alt.belnr and alt.belnr == beleg.belnr) or not alt.brutto:
            continue
        if alt.eingang and beleg.eingang and alt.eingang > beleg.eingang:
            continue  # Verdacht immer am späteren Beleg melden
        if _norm(alt.xblnr) == _norm(beleg.xblnr) and alt.bldat == beleg.bldat:
            continue  # exakte Dublette: entscheidet die Matching-Engine (SPERRE_DUBLETTE)
        abw = abs(beleg.brutto - alt.brutto) * 100 / alt.brutto
        if abw > BETRAG_TOLERANZ_PROZENT:
            continue
        tage = abs((beleg.bldat - alt.bldat).days) if beleg.bldat and alt.bldat else None
        aehnlich = nummer_aehnlichkeit(beleg.xblnr, alt.xblnr)
        nah = tage is not None and tage <= DATUM_FENSTER_TAGE
        if not (nah or aehnlich >= NUMMER_AEHNLICHKEIT):
            continue
        gruende = [f"gleicher Lieferant {beleg.lifnr}",
                   f"Betrag {fmt_de(beleg.brutto)} € vs. {fmt_de(alt.brutto)} € (Abweichung {fmt_de(abw)} %)"]
        if nah:
            gruende.append(f"Rechnungsdatum {tage} Tage auseinander")
        if aehnlich >= NUMMER_AEHNLICHKEIT:
            gruende.append(f"Rechnungsnummer {beleg.xblnr} ähnelt {alt.xblnr} ({round(aehnlich * 100)} %)")
        treffer.append(Risikohinweis("DUBLETTENVERDACHT", beleg.xblnr, beleg.lifnr, beleg.brutto,
                                     "; ".join(gruende) + ".",
                                     [f"RBKP {alt.belnr} · XBLNR {alt.xblnr}" if alt.belnr else f"XBLNR {alt.xblnr}"]))
    return treffer


def _bankaenderung(beleg: Beleg, aenderungen: Sequence[Bankaenderung], stichtag: date) -> list[Risikohinweis]:
    bezug = beleg.eingang or stichtag
    treffer = []
    for a in aenderungen:
        if a.lifnr == beleg.lifnr and 0 <= (bezug - a.datum).days <= BANK_FENSTER_TAGE:
            treffer.append(Risikohinweis(
                "BANKDATEN_GEAENDERT", beleg.xblnr, beleg.lifnr, beleg.brutto,
                f"IBAN des Lieferanten am {fmt_datum(a.datum)} geändert ({a.alt} → {a.neu}, Benutzer {a.benutzer}) – "
                f"{(bezug - a.datum).days} Tage vor Rechnungseingang.",
                [f"CDHDR KRED {a.lifnr} · Änderungsnr. {a.changenr} (FK02)"]))
    return treffer


def _ohne_bestellung(beleg: Beleg) -> list[Risikohinweis]:
    if beleg.ebeln:
        return []
    return [Risikohinweis("OHNE_BESTELLUNG", beleg.xblnr, beleg.lifnr, beleg.brutto,
                          f"Kein Bestellbezug (BT-13 leer) – {fmt_de(beleg.brutto)} € ohne vorab genehmigten Bedarf.",
                          ["EKKO – keine Bestellung"])]


def _glatter_betrag(beleg: Beleg) -> list[Risikohinweis]:
    pauschal = not beleg.ebeln or (len(beleg.mengen) == 1 and beleg.mengen[0] == 1)
    if beleg.netto >= RUNDUNG_AB and beleg.netto % RUNDUNG_BASIS == 0 and pauschal:
        return [Risikohinweis("GLATTER_BETRAG", beleg.xblnr, beleg.lifnr, beleg.brutto,
                              f"Nettobetrag {fmt_de(beleg.netto)} € ist ein Vielfaches von {fmt_de(RUNDUNG_BASIS, 0)} € "
                              "und nicht aus Menge × Bestellpreis entstanden (Pauschale ohne Mengenbasis).",
                              [f"RSEG: {len(beleg.mengen)} Position(en), Menge {', '.join(fmt_de(m, 0) for m in beleg.mengen) or '–'}"])]
    return []


def pruefe_beleg(beleg: Beleg, bestand: Sequence[Beleg], aenderungen: Sequence[Bankaenderung],
                 stichtag: date) -> list[Risikohinweis]:
    return (_dublettenverdacht(beleg, bestand) + _bankaenderung(beleg, aenderungen, stichtag)
            + _ohne_bestellung(beleg) + _glatter_betrag(beleg))


def pruefe_bestand(db_pfad: Path | str, stichtag: date, nur_ab: date | None = None) -> list[Risikohinweis]:
    """Alle Regeln über den Rechnungsbestand (RBKP); ``nur_ab``: nur Belege mit Eingang ab diesem Datum."""
    bestand = lade_belege(db_pfad)
    aenderungen = lade_bankaenderungen(db_pfad)
    pruefen = [b for b in bestand if nur_ab is None or (b.eingang and b.eingang >= nur_ab)]
    hinweise: list[Risikohinweis] = []
    gesehen: set[frozenset[str]] = set()
    for b in pruefen:
        for h in pruefe_beleg(b, bestand, aenderungen, stichtag):
            if h.regel == "DUBLETTENVERDACHT":   # Paar nur einmal melden (am späteren Beleg)
                paar = frozenset({b.xblnr, h.belege[0].rsplit(" ", 1)[-1]})
                if paar in gesehen:
                    continue
                gesehen.add(paar)
            hinweise.append(h)
    return hinweise


def pruefe_vorgang(db_pfad: Path | str, v: Vorgang, stichtag: date) -> list[Risikohinweis]:
    """Regeln für eine einzelne, (noch) nicht gebuchte Rechnung gegen den gebuchten Bestand."""
    beleg = beleg_aus_vorgang(v)
    if beleg is None:
        return []
    beleg = Beleg(beleg.xblnr, beleg.lifnr, beleg.bldat, stichtag, beleg.brutto, beleg.netto, beleg.ebeln,
                  beleg.mengen)
    return pruefe_beleg(beleg, lade_belege(db_pfad), lade_bankaenderungen(db_pfad), stichtag)


def je_rechnung(hinweise: Iterable[Risikohinweis]) -> dict[str, list[Risikohinweis]]:
    ergebnis: dict[str, list[Risikohinweis]] = {}
    for h in hinweise:
        ergebnis.setdefault(h.xblnr, []).append(h)
    return ergebnis


__all__ = ["REGELN", "Beleg", "Risikohinweis", "Bankaenderung", "lade_belege", "lade_bankaenderungen",
           "pruefe_beleg", "pruefe_bestand", "pruefe_vorgang", "je_rechnung", "nummer_aehnlichkeit"]

