"""Abweichungstreiber im 3-Way-Matching: Wie setzt sich der Rechnungsbetrag zusammen?

Je Rechnungsposition wird der Nettobetrag centgenau zerlegt in

    gedeckt (WE-Menge × Bestellpreis) + Preis + Menge + Teil-Lieferung + fehlender WE + Rundung = Rechnung netto

* **gedeckt** – durch Bestellung *und* Wareneingang belegte, noch nicht berechnete Menge zum Bestellpreis,
* **Preis** – berechnete Menge × (Rechnungspreis − Bestellpreis),
* **Menge** – Mehrmenge über der offenen Bestellmenge (zum Bestellpreis),
* **Teil-Lieferung** – bestellt, aber (noch) nicht geliefert (zum Bestellpreis),
* **fehlender WE** – zur Position ist überhaupt kein Wareneingang gebucht,
* **Rundung** – Rest aus Rundung des Lieferanten (in der Regel 0,00 €).

Rechnungen ohne Bestellung bzw. Dubletten werden vollständig dem Treiber „ohne Bestellung“ bzw. „Dublette“
zugeordnet. Invariante (Tests): Summe der Treiber = Rechnung netto, auf den Cent – wie die EBIT-Brücke in VarianceAI.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .agent import WorkflowSchritt
from .assistent import Vorgang

TREIBER = ("Preis", "Menge", "Teil-Lieferung", "fehlender WE", "ohne Bestellung", "Dublette", "Rundung")
_NULL = Decimal("0.00")


def _r2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _zwischen(x: Decimal, unten: Decimal, oben: Decimal) -> Decimal:
    return max(unten, min(x, oben))


@dataclass(frozen=True)
class Positionstreiber:
    ebelp: int | None
    text: str
    netto: Decimal
    gedeckt: Decimal
    betraege: dict[str, Decimal] = field(default_factory=dict)

    @property
    def summe(self) -> Decimal:
        return self.gedeckt + sum(self.betraege.values(), _NULL)


@dataclass(frozen=True)
class Rechnungstreiber:
    rechnungsnummer: str
    positionen: list[Positionstreiber]

    @property
    def netto(self) -> Decimal:
        return sum((p.netto for p in self.positionen), _NULL)

    @property
    def gedeckt(self) -> Decimal:
        return sum((p.gedeckt for p in self.positionen), _NULL)

    def betrag(self, treiber: str) -> Decimal:
        return sum((p.betraege.get(treiber, _NULL) for p in self.positionen), _NULL)

    @property
    def treiber(self) -> dict[str, Decimal]:
        return {t: self.betrag(t) for t in TREIBER}


def rechnungstreiber(v: Vorgang) -> Rechnungstreiber | None:
    """Zerlegung einer geprüften Rechnung; ``None`` für abgewiesene Dateien."""
    if v.abgelehnt or v.fakten is None:
        return None
    r = v.fakten.rechnung
    if v.schritt is WorkflowSchritt.SPERRE_DUBLETTE or v.schritt is WorkflowSchritt.COMPLIANCE_MAVERICK_BUYING \
            or not (v.sap and v.sap.positionen):
        grund = "Dublette" if v.schritt is WorkflowSchritt.SPERRE_DUBLETTE else "ohne Bestellung"
        return Rechnungstreiber(r.rechnungsnummer, [
            Positionstreiber(p.bestellposition, p.beschreibung, _r2(p.betrag_netto), _NULL,
                             {grund: _r2(p.betrag_netto)}) for p in r.positionen])
    rechnung_je_pos = {p.bestellposition: p for p in r.positionen if p.bestellposition}
    if not rechnung_je_pos and len(r.positionen) == 1 and len(v.sap.positionen) == 1:
        rechnung_je_pos = {v.sap.positionen[0].ebelp: r.positionen[0]}
    positionen: list[Positionstreiber] = []
    for sp in v.sap.positionen:
        rp = rechnung_je_pos.get(sp.ebelp)
        if rp is None:
            continue
        q, p_soll = rp.menge, sp.bestellpreis
        netto = _r2(rp.betrag_netto)
        offen_b = sp.bestellmenge - sp.bereits_berechnet
        offen_we = sp.we_menge - sp.bereits_berechnet
        b: dict[str, Decimal] = {t: _NULL for t in ("Preis", "Menge", "Teil-Lieferung", "fehlender WE")}
        if sp.we_menge <= 0:
            gedeckt = _NULL
            b["fehlender WE"] = _r2(q * p_soll)
        else:
            deck_b = _zwischen(offen_b, Decimal(0), q)
            deck_we = _zwischen(min(offen_b, offen_we), Decimal(0), q)
            gedeckt = _r2(deck_we * p_soll)
            b["Menge"] = _r2((q - deck_b) * p_soll)
            b["Teil-Lieferung"] = _r2((deck_b - deck_we) * p_soll)
        b["Preis"] = _r2(q * (rp.einzelpreis_netto - p_soll))
        b["Rundung"] = netto - gedeckt - sum(b.values(), _NULL)
        positionen.append(Positionstreiber(sp.ebelp, sp.text or rp.beschreibung, netto, gedeckt, b))
    ohne = [p for p in r.positionen if p not in rechnung_je_pos.values()]
    for p in ohne:  # Rechnungspositionen ohne zuordenbare Bestellposition: Mehrmenge
        positionen.append(Positionstreiber(None, p.beschreibung, _r2(p.betrag_netto), _NULL,
                                           {"Menge": _r2(p.betrag_netto)}))
    return Rechnungstreiber(r.rechnungsnummer, positionen)


@dataclass(frozen=True)
class Treibersumme:
    treiber: str
    betrag: Decimal
    anzahl_rechnungen: int


def treiber_gesamt(liste: Iterable[Rechnungstreiber]) -> list[Treibersumme]:
    """Summe je Treiber über mehrere Rechnungen (nur Treiber ≠ 0), absteigend nach Betrag."""
    summen: dict[str, Decimal] = {t: _NULL for t in TREIBER}
    anzahl: dict[str, int] = {t: 0 for t in TREIBER}
    for rt in liste:
        for t, betrag in rt.treiber.items():
            if betrag != 0:
                summen[t] += betrag
                anzahl[t] += 1
    return sorted((Treibersumme(t, summen[t], anzahl[t]) for t in TREIBER if anzahl[t]),
                  key=lambda s: (-abs(s.betrag), s.treiber))


__all__ = ["TREIBER", "Positionstreiber", "Rechnungstreiber", "Treibersumme", "rechnungstreiber", "treiber_gesamt"]
