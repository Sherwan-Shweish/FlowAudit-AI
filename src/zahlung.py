"""Skonto-Optimierung: Zahlungsvorschlag nach Skontofrist und Fälligkeit (deterministisch).

Regeln
------
* Bezahlt wird nur, was freigegeben ist: touchless ohne sperrenden Präventionstreffer oder Klärfall „gelöst“.
* Mit Skonto: Zahlung im **letzten Zahllauf vor Ablauf der Skontofrist** (Skonto gesichert, Liquidität so lange wie
  möglich gehalten). Liegt die Frist vor dem nächsten Zahllauf, wird eine Eilzahlung am Stichtag vorgeschlagen.
* Ohne Skonto (oder Frist abgelaufen): Zahlung im letzten Zahllauf vor der Nettofälligkeit, überfällige sofort.
* Gesperrte Rechnungen erscheinen mit **gefährdetem** Skonto und der Frist, bis zu der die Klärung abgeschlossen
  sein muss; abgelaufene Fristen zählen als **verloren**.

Der Skontoertrag wird als Jahreszins ausgewiesen: ``s / (1 − s) × 360 / (Zahlungsziel − Skontotage)``
(Beispiel 2 % / 14 Tage, netto 30 Tage ≙ 45,9 % p. a.) – Argument für die priorisierte Klärung.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .agent import fmt_de
from .assistent import Vorgang
from .klaerfall import Klaerfall, zahlungsfreigabe
from .praevention import Risikohinweis

Zustand = Literal["Zahlung", "Eilzahlung", "gesperrt"]
ZAHLLAUF_TAGE = (1, 4)            # Dienstag und Freitag (Montag = 0)
ZAHLUNGSZIEL_STANDARD = 30


def _r2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def zahllaeufe(von: date, bis: date, wochentage: Sequence[int] = ZAHLLAUF_TAGE) -> list[date]:
    tage = (bis - von).days
    return [von + timedelta(days=i) for i in range(max(tage, -1) + 1) if (von + timedelta(days=i)).weekday() in wochentage]


def letzter_zahllauf(stichtag: date, frist: date, wochentage: Sequence[int] = ZAHLLAUF_TAGE) -> date | None:
    laeufe = zahllaeufe(stichtag, frist, wochentage)
    return laeufe[-1] if laeufe else None


def skonto_jahreszins(prozent: Decimal, skonto_tage: int, ziel_tage: int) -> Decimal | None:
    if ziel_tage <= skonto_tage or prozent <= 0 or prozent >= 100:
        return None
    s = prozent / 100
    return _r2(s / (1 - s) * Decimal(360) / Decimal(ziel_tage - skonto_tage) * 100)


@dataclass(frozen=True)
class Zahlposition:
    datei: str
    rechnungsnummer: str
    lieferant: str
    brutto: Decimal
    zustand: Zustand
    zahltag: date | None
    skonto_prozent: Decimal | None
    skonto_frist: date | None
    faellig: date | None
    skonto_betrag: Decimal          # € (0, wenn keine Vereinbarung)
    skonto_status: Literal["gesichert", "gefährdet", "verloren", "keins"]
    zahlbetrag: Decimal
    jahreszins_prozent: Decimal | None
    begruendung: str


@dataclass(frozen=True)
class Zahlungsvorschlag:
    stichtag: date
    positionen: list[Zahlposition]

    def _summe(self, feld: str, **filter_) -> Decimal:
        return sum((getattr(p, feld) for p in self.positionen if all(getattr(p, k) == v for k, v in filter_.items())),
                   Decimal("0.00"))

    @property
    def zahlbetrag(self) -> Decimal:
        return sum((p.zahlbetrag for p in self.positionen if p.zustand != "gesperrt"), Decimal("0.00"))

    @property
    def skonto_gesichert(self) -> Decimal:
        return self._summe("skonto_betrag", skonto_status="gesichert")

    @property
    def skonto_gefaehrdet(self) -> Decimal:
        return self._summe("skonto_betrag", skonto_status="gefährdet")

    @property
    def skonto_verloren(self) -> Decimal:
        return self._summe("skonto_betrag", skonto_status="verloren")

    def je_zahllauf(self) -> dict[date, Decimal]:
        laeufe: dict[date, Decimal] = {}
        for p in self.positionen:
            if p.zustand != "gesperrt" and p.zahltag:
                laeufe[p.zahltag] = laeufe.get(p.zahltag, Decimal("0.00")) + p.zahlbetrag
        return dict(sorted(laeufe.items()))


def zahlungsvorschlag(belege: Iterable[tuple[Vorgang, Klaerfall | None, list[Risikohinweis]]], stichtag: date,
                      wochentage: Sequence[int] = ZAHLLAUF_TAGE) -> Zahlungsvorschlag:
    positionen: list[Zahlposition] = []
    for v, fall, risiken in belege:
        if v.abgelehnt or v.fakten is None:
            continue
        r = v.fakten.rechnung
        brutto = r.summe_brutto or Decimal("0")
        skonto_tage = v.rechnung.skonto.tage if v.rechnung and v.rechnung.skonto else None
        faellig = r.faelligkeit or ((r.rechnungsdatum + timedelta(days=ZAHLUNGSZIEL_STANDARD))
                                    if r.rechnungsdatum else None)
        betrag = _r2(brutto * r.skonto_prozent / 100) if r.skonto_prozent and r.skonto_frist else Decimal("0.00")
        zins = (skonto_jahreszins(r.skonto_prozent, skonto_tage, (faellig - r.rechnungsdatum).days)
                if betrag and skonto_tage and faellig and r.rechnungsdatum else None)
        frei = zahlungsfreigabe(fall) and not (fall is None and any(h.sperrt_zahlung for h in risiken))
        if not frei:
            if betrag == 0:
                status, text = "keins", "keine Skontovereinbarung"
            elif r.skonto_frist < stichtag:
                status, text = "verloren", "Skontofrist abgelaufen"
            else:
                status, text = "gefährdet", f"Klärung bis {r.skonto_frist:%d.%m.%Y} nötig, sonst Skontoverlust"
            grund = fall.grund if fall else "Präventionstreffer"
            positionen.append(Zahlposition(v.datei, r.rechnungsnummer, r.lieferant_name, brutto, "gesperrt", None,
                                           r.skonto_prozent, r.skonto_frist, faellig, betrag, status, Decimal("0.00"),
                                           zins, f"gesperrt: {grund} · {text}"))
            continue
        if betrag and r.skonto_frist >= stichtag:
            lauf = letzter_zahllauf(stichtag, r.skonto_frist, wochentage)
            zustand: Zustand = "Zahlung" if lauf else "Eilzahlung"
            zahltag = lauf or stichtag
            zusatz = f" ≙ {fmt_de(zins, 1)} % p. a." if zins is not None else ""
            positionen.append(Zahlposition(
                v.datei, r.rechnungsnummer, r.lieferant_name, brutto, zustand, zahltag, r.skonto_prozent,
                r.skonto_frist, faellig, betrag, "gesichert", brutto - betrag, zins,
                f"letzter Zahllauf vor Skontofrist {r.skonto_frist:%d.%m.%Y}{zusatz}" if lauf
                else f"Skontofrist {r.skonto_frist:%d.%m.%Y} vor dem nächsten Zahllauf – Eilzahlung"))
            continue
        status = "verloren" if betrag else "keins"
        if faellig and faellig >= stichtag:
            lauf = letzter_zahllauf(stichtag, faellig, wochentage)
            zustand, zahltag = ("Zahlung", lauf) if lauf else ("Eilzahlung", stichtag)
            text = f"netto fällig {faellig:%d.%m.%Y} – Zahlung im letzten Lauf davor"
        else:
            zustand, zahltag = "Eilzahlung", stichtag
            text = "überfällig – sofort zahlen" if faellig else "ohne Fälligkeit – sofort zahlen"
        positionen.append(Zahlposition(v.datei, r.rechnungsnummer, r.lieferant_name, brutto, zustand, zahltag,
                                       r.skonto_prozent, r.skonto_frist, faellig, betrag, status, brutto, zins, text))
    reihenfolge = {"Eilzahlung": 0, "Zahlung": 1, "gesperrt": 2}
    positionen.sort(key=lambda p: (reihenfolge[p.zustand], p.zahltag or date.max, p.rechnungsnummer))
    return Zahlungsvorschlag(stichtag, positionen)


__all__ = ["ZAHLLAUF_TAGE", "Zahlposition", "Zahlungsvorschlag", "zahllaeufe", "letzter_zahllauf",
           "skonto_jahreszins", "zahlungsvorschlag"]
