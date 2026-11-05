"""Prüfstand: alle deterministischen Ergebnisse eines Prüflaufs an einer Stelle (für App, CLI und Exporte).

Ein Prüflauf
1. bucht alle Musterbelege in einer **Kopie** der Datenbank (Batchlauf, Kennzahlen, Wochenreihen, Vorperiode),
2. prüft jeden Beleg einzeln gegen den ursprünglichen Bestand (Formalprüfung, Matching, SAP-Drill-down, Treiber),
3. wendet die Präventionsregeln an, legt Klärfälle an und bewertet die Lieferanten.

Der Zahlungsvorschlag hängt vom aktuellen Stand der Klärfälle ab und wird deshalb bei Bedarf neu gerechnet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .agent import Einstellungen
from .assistent import KIAssistent, Vorgang
from .bpmn_tracker import Periodenvergleich, Wochenwert, lade_vorgaenge, periodenvergleich, wochenreihen
from .copilot import Pruefzahlen, pruefzahlen
from .demo import Batchlauf, Demodaten, batchlauf
from .formalpruefung import Pflichtangabe, pflichtangaben
from .klaerfall import VIER_AUGEN_GRENZE, Klaerfall, lege_an
from .lieferanten import Scorecard, scorecards
from .praevention import Risikohinweis, pruefe_bestand, pruefe_vorgang
from .treiber import Rechnungstreiber, Treibersumme, rechnungstreiber, treiber_gesamt
from .zahlung import Zahlungsvorschlag, zahlungsvorschlag

PERIODE_TAGE = 28


@dataclass
class Pruefstand:
    demo: Demodaten
    einstellungen: Einstellungen
    batch: Batchlauf
    vorgaenge: dict[str, Vorgang]
    formal: dict[str, list[Pflichtangabe]]
    risiken: dict[str, list[Risikohinweis]]          # je Musterbeleg (gegen den gebuchten Bestand)
    risiken_bestand: list[Risikohinweis]             # alle Belege nach dem Batchlauf
    klaerfaelle: dict[str, Klaerfall]                # Anfangszustand – die App führt sie in der Sitzung fort
    treiber: dict[str, Rechnungstreiber]
    treiber_summen: list[Treibersumme]
    lieferanten: list[Scorecard]
    periode: Periodenvergleich
    reihen: list[Wochenwert]
    zahlen: Pruefzahlen
    vier_augen_grenze: Decimal

    @property
    def stichtag(self):  # noqa: ANN201
        return self.demo.stichtag

    @property
    def kpis(self):  # noqa: ANN201
        return self.batch.kpis

    def zahlungsvorschlag(self, klaerfaelle: Mapping[str, Klaerfall] | None = None) -> Zahlungsvorschlag:
        faelle = self.klaerfaelle if klaerfaelle is None else klaerfaelle
        return zahlungsvorschlag(((v, faelle.get(d), self.risiken.get(d, [])) for d, v in self.vorgaenge.items()),
                                 self.stichtag)


def _rechenfehler(v: Vorgang) -> bool:
    return bool(v.fakten and any(p.code.value == "SUMMENFEHLER" and p.status.value == "FEHLER"
                                 for p in v.fakten.pruefungen))


def erstelle_pruefstand(demo: Demodaten, einstellungen: Einstellungen, vier_augen_grenze: Decimal = VIER_AUGEN_GRENZE,
                        arbeitsordner: Path | None = None) -> Pruefstand:
    b = batchlauf(demo, einstellungen.preis_toleranz_prozent, arbeitsordner, einstellungen.mengen_toleranz_prozent)
    vorgaenge_db = lade_vorgaenge(b.db_kopie)
    assistent = KIAssistent(demo.db_pfad, einstellungen, stichtag=demo.stichtag)
    vorgaenge = {name: assistent.pruefe(demo.ordner / name) for name in demo.manifest}
    risiken = {name: pruefe_vorgang(demo.db_pfad, v, demo.stichtag) for name, v in vorgaenge.items()}
    klaerfaelle = {name: fall for name, v in vorgaenge.items()
                   if (fall := lege_an(v, risiken[name], vier_augen_grenze)) is not None}
    treiber = {name: t for name, v in vorgaenge.items() if (t := rechnungstreiber(v)) is not None}
    bestand = pruefe_bestand(b.db_kopie, demo.stichtag)
    return Pruefstand(
        demo=demo, einstellungen=einstellungen, batch=b, vorgaenge=vorgaenge,
        formal={name: pflichtangaben(v.rechnung, _rechenfehler(v)) for name, v in vorgaenge.items() if v.rechnung},
        risiken=risiken, risiken_bestand=bestand, klaerfaelle=klaerfaelle, treiber=treiber,
        treiber_summen=treiber_gesamt(treiber.values()),
        lieferanten=scorecards(b.db_kopie, einstellungen.preis_toleranz_prozent, bestand),
        periode=periodenvergleich(vorgaenge_db, demo.stichtag, PERIODE_TAGE),
        reihen=wochenreihen(vorgaenge_db, demo.stichtag, 8), zahlen=pruefzahlen(b.ergebnisse),
        vier_augen_grenze=Decimal(str(vier_augen_grenze)),
    )


__all__ = ["Pruefstand", "erstelle_pruefstand", "PERIODE_TAGE"]
