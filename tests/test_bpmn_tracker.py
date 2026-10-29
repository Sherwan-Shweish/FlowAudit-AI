"""Tests für src/bpmn_tracker.py – BPMN-/eEPK-Diagramme und Prozess-KPIs."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.agent import WorkflowSchritt as WS
from src.bpmn_tracker import (
    BPMN,
    EPK,
    Vorgang,
    berechne_kpis,
    kpi_bericht,
    lade_vorgaenge,
    prozessdiagramm,
)
from src.matching_engine import MatchingEngine, verarbeite_datei, verarbeite_ordner

from .conftest import STICHTAG


def _klassen(mermaid: str) -> dict[str, set[str]]:
    """'class A,B besucht' → {'besucht': {'A', 'B'}}"""
    return {m[2]: set(m[1].split(",")) for m in re.finditer(r"^\s*class (\S+) (\S+)$", mermaid, re.M)}


def _fakten(demo, datei):
    return verarbeite_datei(demo.rechnungsordner / datei, MatchingEngine(demo.db_pfad)).fakten


class TestModelle:
    @pytest.mark.parametrize("modell", [BPMN, EPK], ids=["bpmn", "epk"])
    def test_modell_konsistent(self, modell):
        ids = {k.id for k in modell.knoten}
        assert len(ids) == len(modell.knoten), "Knoten-IDs müssen eindeutig sein"
        assert set(modell.besitz) == set(WS), "Jeder Workflow-Schritt braucht eine Darstellung"
        assert all(i in ids for liste in modell.besitz.values() for i in liste)
        assert all(k.von in ids and k.nach in ids for k in modell.kanten)

    def test_epk_enthaelt_xor_und_and_konnektoren(self):
        texte = [k.text for k in EPK.knoten]
        assert texte.count("XOR") == 4 and texte.count("AND") == 2
        assert {k.art for k in EPK.knoten} == {"prozess", "org", "info"}


class TestDiagramme:
    def test_bpmn_preisklaerung(self, demo):
        m = prozessdiagramm(_fakten(demo, "03_preisabweichung_ubl.xml"), "bpmn")
        k = _klassen(m)
        assert m.startswith("flowchart LR")
        assert k["aktiv_KLAERUNG"] == {"E5"}
        assert k["besucht"] == {"S", "T1", "G1", "T2", "G2", "T3", "G3", "T4", "G4"}
        assert {"E1", "E2", "E3", "E4", "E6", "E7"} <= k["inaktiv"]
        assert 'G4 -->|"Preis > Toleranz"| E5' in m

    def test_bpmn_dublette_stoppt_am_ersten_gateway(self, demo):
        m = prozessdiagramm(_fakten(demo, "08_dublette_ubl.xml"), "bpmn")
        k = _klassen(m)
        assert k["aktiv_SPERRE"] == {"E1"} and k["besucht"] == {"S", "T1", "G1"}
        assert "T4" in k["inaktiv"]
        assert re.search(r"linkStyle 0,1,2 stroke:#1d4ed8", m)  # S→T1, T1→G1, G1→E1

    def test_epk_touchless_mit_and_verzweigung(self, demo):
        m = prozessdiagramm(_fakten(demo, "01_touchless_xrechnung_ubl.xml"), "epk")
        k = _klassen(m)
        assert m.startswith("flowchart TD")
        assert k["aktiv_FREIGABE"] == {"EVok", "A1", "Fb", "Fz", "A2", "EVend"}
        assert {"EV0", "F1", "X1", "F4", "X4"} <= k["besucht"]
        assert {"OKB", "OEK"} <= k["org"] and {"IR", "IW"} <= k["info"]
        assert "OEK -.- Fp" in m and 'EVend{{"Rechnung ist zahlungsbereit"}}' in m

    def test_epk_ohne_organisation(self, demo):
        m = prozessdiagramm(_fakten(demo, "07_maverick_buying_ubl.xml"), "epk", mit_organisation=False)
        assert "-.-" not in m and "OKB" not in m
        assert _klassen(m)["aktiv_KLAERUNG"] == {"EVm", "Fm", "EVme"}

    def test_pfad_als_eingabe_und_titel(self):
        m = prozessdiagramm([WS.RECHNUNGSEINGANG, WS.DUBLETTENPRUEFUNG, WS.SPERRE_DUBLETTE], titel="Beleg X")
        assert m.startswith("---\ntitle: Beleg X\n---\nflowchart LR")

    def test_neutrales_modell_ohne_hervorhebung(self):
        m = prozessdiagramm(None, "bpmn")
        assert not any(c.startswith("aktiv") or c in {"besucht", "inaktiv"} for c in _klassen(m))
        assert "linkStyle" not in m

    def test_unbekannter_stil(self):
        with pytest.raises(ValueError, match="Stil"):
            prozessdiagramm(None, "uml")

    @pytest.mark.parametrize("stil", ["bpmn", "epk"])
    def test_jede_musterrechnung_genau_ein_aktiver_endzustand(self, demo, manifest, stil):
        for fall in (m for m in manifest if m["erwartete_route"] != "ABGELEHNT"):
            k = _klassen(prozessdiagramm(_fakten(demo, fall["datei"]), stil))
            aktiv = [c for c in k if c.startswith("aktiv_")]
            assert len(aktiv) == 1, fall["datei"]


def _v(nr, schritt, brutto, eingang, stunden=None, skonto=None, frist=None) -> Vorgang:
    return Vorgang(nr, "100100", schritt, Decimal(brutto), eingang,
                   eingang + timedelta(hours=stunden) if stunden is not None else None,
                   Decimal(skonto) if skonto else None, frist)


class TestKPIs:
    def test_kennzahlen_exakt(self):
        t0 = datetime(2026, 10, 1, 8, 0)
        vorgaenge = [
            _v("A", WS.TOUCHLESS_FREIGABE, "1190.00", t0, stunden=0.25, skonto="2", frist=date(2026, 10, 14)),
            _v("B", WS.TOUCHLESS_FREIGABE, "595.00", t0, stunden=0.75),
            _v("C", WS.KLAERUNG_EINKAUF_PREIS, "2380.00", t0, stunden=50, skonto="2", frist=date(2026, 10, 2)),
            _v("D", WS.COMPLIANCE_MAVERICK_BUYING, "119.00", t0),
            _v("E", WS.SPERRE_FEHLENDER_WARENEINGANG, "1000.00", t0, skonto="3", frist=date(2026, 10, 30)),
            _v("F", WS.KLAERUNG_EINKAUF_PREIS, "500.00", t0, skonto="2", frist=date(2026, 10, 20)),
        ]
        k = berechne_kpis(vorgaenge, stichtag=date(2026, 10, 28))
        assert k.anzahl_rechnungen == 6 and k.anzahl_touchless == 2
        assert k.dunkelverarbeitungsquote_prozent == Decimal("33.33")
        assert (k.anzahl_abgeschlossen, k.anzahl_offen) == (3, 3)
        assert k.durchlaufzeit_avg_stunden == Decimal("17.00")      # (0,25 + 0,75 + 50) / 3
        assert k.durchlaufzeit_median_stunden == Decimal("0.75")
        assert k.durchlaufzeit_touchless_avg_minuten == Decimal("30.00")
        assert k.durchlaufzeit_manuell_avg_stunden == Decimal("50.00")
        assert k.sperrgruende == {"KLAERUNG_EINKAUF_PREIS": 2, "COMPLIANCE_MAVERICK_BUYING": 1,
                                  "SPERRE_FEHLENDER_WARENEINGANG": 1}
        assert k.sperrgruende_anteil_prozent["KLAERUNG_EINKAUF_PREIS"] == Decimal("50.00")
        assert (k.anzahl_maverick, k.maverick_quote_prozent) == (1, Decimal("16.67"))
        assert k.skonto_gesichert_eur == Decimal("23.80")           # A
        assert k.skonto_verloren_eur == Decimal("57.60")            # C (zu spät) 47,60 + F (Frist vorbei) 10,00
        assert k.skonto_gefaehrdet_eur == Decimal("30.00")          # E (offen, Frist läuft noch)
        assert k.volumen_offen_eur == Decimal("1619.00")

    def test_leerer_bestand(self):
        k = berechne_kpis([], stichtag=STICHTAG)
        assert k.anzahl_rechnungen == 0 and k.dunkelverarbeitungsquote_prozent == Decimal("0.00")
        assert k.durchlaufzeit_avg_stunden is None and k.sperrgruende == {}

    def test_kpis_aus_der_datenbank_vor_und_nach_buchung(self, demo):
        vorher = berechne_kpis(lade_vorgaenge(demo.db_pfad), STICHTAG)
        assert vorher.anzahl_rechnungen == 50
        verarbeite_ordner(demo.rechnungsordner, demo.db_pfad, buchen=True)
        nachher = berechne_kpis(lade_vorgaenge(demo.db_pfad), STICHTAG)
        assert nachher.anzahl_rechnungen == 64
        assert nachher.anzahl_touchless == vorher.anzahl_touchless + 4
        assert nachher.anzahl_maverick == vorher.anzahl_maverick + 1
        assert nachher.skonto_gefaehrdet_eur >= Decimal("49.74")  # Rechnung 03 hängt in der Preisklärung
        assert Decimal(0) < nachher.dunkelverarbeitungsquote_prozent < Decimal(100)

    def test_kpi_bericht(self, demo):
        text = kpi_bericht(berechne_kpis(lade_vorgaenge(demo.db_pfad), STICHTAG))
        assert "Dunkelverarbeitungsquote" in text and "Skonto gesichert / gefährdet / verloren" in text
        assert "| Sperr-/Klärungsgrund | Anzahl | Anteil |" in text
