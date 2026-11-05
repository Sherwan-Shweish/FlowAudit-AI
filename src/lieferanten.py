"""Lieferanten-Scorecard: Fehlerquote, Durchlaufzeit, Preisabweichungen und Liefertreue je Kreditor.

Alle Kennzahlen kommen aus den SAP-Tabellen (RBKP, RSEG, EKPO, MATDOC) – schreibgeschützt und deterministisch:

* **Fehlerquote** – Anteil der Rechnungen, die nicht touchless liefen (Klärung oder Sperre).
* **Ø Durchlaufzeit** – Eingang bis Abschluss, nur abgeschlossene Vorgänge.
* **Preisabweichungen** – Rechnungspositionen mit Preis außerhalb der Toleranz und deren Mehrbetrag
  (RSEG-Betrag − Menge × EKPO-Preis).
* **Liefertreue** – Anteil der Bestellpositionen, deren erster Wareneingang (MATDOC, BWA 101) spätestens am
  vereinbarten Liefertermin (EKPO-EINDT) gebucht wurde.

Bewertung A/B/C (regelbasiert, mit Begründung): A = Fehlerquote ≤ 25 % und Liefertreue ≥ 90 %,
C = Fehlerquote > 50 % oder Liefertreue < 70 %, sonst B.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .agent import fmt_de
from .praevention import Risikohinweis


def _r(x: Decimal, stellen: int = 2) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-stellen), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Scorecard:
    lifnr: str
    name: str
    rechnungen: int
    volumen_brutto: Decimal
    fehler: int
    fehlerquote_prozent: Decimal
    durchlaufzeit_stunden: Decimal | None
    preisabweichungen: int
    preisabweichung_eur: Decimal
    positionen_geliefert: int
    puenktlich: int
    liefertreue_prozent: Decimal | None
    risikohinweise: int = 0

    @property
    def bewertung(self) -> str:
        treue = self.liefertreue_prozent
        if self.fehlerquote_prozent > 50 or (treue is not None and treue < 70):
            return "C"
        if self.fehlerquote_prozent <= 25 and (treue is None or treue >= 90):
            return "A"
        return "B"

    @property
    def begruendung(self) -> str:
        teile = [f"Fehlerquote {fmt_de(self.fehlerquote_prozent, 1)} %"]
        if self.liefertreue_prozent is not None:
            teile.append(f"Liefertreue {fmt_de(self.liefertreue_prozent, 1)} % ({self.puenktlich}/"
                         f"{self.positionen_geliefert} Positionen pünktlich)")
        else:
            teile.append("ohne Bestellungen – Liefertreue nicht messbar")
        if self.preisabweichungen:
            teile.append(f"{self.preisabweichungen} Preisabweichung(en) über {fmt_de(self.preisabweichung_eur)} €")
        if self.risikohinweise:
            teile.append(f"{self.risikohinweise} Präventionshinweis(e)")
        return " · ".join(teile)


def scorecards(db_pfad: Path | str, preis_toleranz_prozent: Decimal = Decimal("2.0"),
               risiken: Iterable[Risikohinweis] = ()) -> list[Scorecard]:
    con = sqlite3.connect(f"{Path(db_pfad).resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        namen = {z["LIFNR"]: z["NAME1"] for z in con.execute("SELECT LIFNR, NAME1 FROM LFA1")}
        rechnungen: dict[str, list[sqlite3.Row]] = {}
        for z in con.execute("SELECT * FROM RBKP WHERE STBLG IS NULL AND ZZ_WF_SCHRITT IS NOT NULL"):
            rechnungen.setdefault(z["LIFNR"], []).append(z)
        preise: dict[str, list[Decimal]] = {}
        for z in con.execute(
            "SELECT r.LIFNR, s.MENGE, s.WRBTR, p.NETPR, p.PEINH FROM RSEG s "
            "JOIN RBKP r ON r.BELNR = s.BELNR AND r.GJAHR = s.GJAHR "
            "JOIN EKPO p ON p.EBELN = s.EBELN AND p.EBELP = s.EBELP WHERE r.STBLG IS NULL AND s.MENGE > 0"
        ):
            soll = Decimal(str(z["NETPR"])) / Decimal(z["PEINH"] or 1)
            ist = Decimal(str(z["WRBTR"])) / Decimal(str(z["MENGE"]))
            if soll and abs((ist - soll) / soll * 100) > preis_toleranz_prozent:
                preise.setdefault(z["LIFNR"], []).append(_r(Decimal(str(z["WRBTR"])) - Decimal(str(z["MENGE"])) * soll))
        lieferung: dict[str, list[bool]] = {}
        for z in con.execute(
            "SELECT k.LIFNR, p.EINDT, min(m.BUDAT) AS ERSTER_WE FROM EKPO p JOIN EKKO k ON k.EBELN = p.EBELN "
            "JOIN MATDOC m ON m.EBELN = p.EBELN AND m.EBELP = p.EBELP AND m.BWART = '101' "
            "WHERE p.EINDT IS NOT NULL GROUP BY p.EBELN, p.EBELP"
        ):
            lieferung.setdefault(z["LIFNR"], []).append(date.fromisoformat(z["ERSTER_WE"]) <= date.fromisoformat(z["EINDT"]))
    finally:
        con.close()
    risiko_je: dict[str, int] = {}
    for h in risiken:
        risiko_je[h.lifnr] = risiko_je.get(h.lifnr, 0) + 1
    karten = []
    for lifnr, belege in rechnungen.items():
        fehler = sum(b["ZZ_WF_SCHRITT"] != "TOUCHLESS_FREIGABE" for b in belege)
        dlz = [Decimal(str((datetime.fromisoformat(b["ZZ_ABSCHLUSS"]) - datetime.fromisoformat(b["ZZ_EINGANG"]))
                           .total_seconds() / 3600)) for b in belege if b["ZZ_ABSCHLUSS"]]
        treue = lieferung.get(lifnr, [])
        karten.append(Scorecard(
            lifnr=lifnr, name=namen.get(lifnr, lifnr), rechnungen=len(belege),
            volumen_brutto=_r(sum((Decimal(str(b["RMWWR"])) for b in belege), Decimal(0))),
            fehler=fehler, fehlerquote_prozent=_r(Decimal(fehler) * 100 / len(belege)),
            durchlaufzeit_stunden=_r(sum(dlz, Decimal(0)) / len(dlz)) if dlz else None,
            preisabweichungen=len(preise.get(lifnr, [])), preisabweichung_eur=_r(sum(preise.get(lifnr, []), Decimal(0))),
            positionen_geliefert=len(treue), puenktlich=sum(treue),
            liefertreue_prozent=_r(Decimal(sum(treue)) * 100 / len(treue)) if treue else None,
            risikohinweise=risiko_je.get(lifnr, 0),
        ))
    return sorted(karten, key=lambda k: ({"C": 0, "B": 1, "A": 2}[k.bewertung], -k.fehlerquote_prozent, k.name))


__all__ = ["Scorecard", "scorecards"]
