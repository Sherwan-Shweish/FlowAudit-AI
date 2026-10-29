"""End-to-End: XML → Parser → 3-Way-Matching → AuditFakten → KI-Audit-Agent (Mock/Offline) → Buchung/KPIs."""

from __future__ import annotations

import json

import pytest

from src.agent import (
    Einstellungen,
    FaktenPruefer,
    FlowAuditAgent,
    MockLLMClient,
    ReadOnlySQLGuard,
    WorkflowSchritt as WS,
    pruefe_konsistenz,
)
from src.matching_engine import MatchingEngine, verarbeite_datei, verarbeite_ordner

from .conftest import llm_json

OFFLINE = Einstellungen(llm_provider="offline")
MOCK = Einstellungen(llm_provider="mock", max_korrekturversuche=1)


def _ergebnis(demo, datei):
    return verarbeite_datei(demo.rechnungsordner / datei, MatchingEngine(demo.db_pfad))


@pytest.mark.parametrize("index", range(12))
def test_offline_pipeline_jede_rechnung(demo, manifest, index):
    """Ohne API-Key: regelbasierter Vermerk – Route korrekt, Zahlen belegt, Bericht konsistent."""
    fall = manifest[index]
    e = _ergebnis(demo, fall["datei"])
    audit = FlowAuditAgent(einstellungen=OFFLINE).pruefe_rechnung(e.fakten)
    assert audit.bericht.workflow_schritt.value == fall["erwartete_route"]
    assert audit.faktencheck.bestanden, audit.faktencheck
    assert pruefe_konsistenz(audit.bericht, e.fakten) == []
    assert audit.eingabe_hash == e.fakten.eingabe_hash()


def test_llm_bericht_mit_korrekten_zahlen_wird_akzeptiert(demo):
    e = _ergebnis(demo, "03_preisabweichung_ubl.xml")
    antwort = llm_json(
        e.fakten,
        vermerk=("Die XRechnung TPK-2026-118 der Taunus Präzisionsteile KG über 2.487,10 EUR brutto weicht beim "
                 "Einzelpreis von der Bestellung 4500000103 ab: 4,18 EUR statt 4,00 EUR (+4,50 %, Toleranz 2 %), "
                 "Mehrbetrag 90,00 EUR. Die Skontofrist läuft am 30.10.2026 ab; 49,74 EUR Skonto sind gefährdet."),
        naechster="Einkauf klärt die Preisabweichung kurzfristig mit dem Lieferanten, um den Skonto zu sichern.",
    )
    audit = FlowAuditAgent(MockLLMClient([antwort]), MOCK).pruefe_rechnung(e.fakten)
    assert audit.quelle == "LLM", audit.warnungen
    assert audit.bericht.rueckfrage.empfaenger.value == "EINKAUF"


def test_prompt_injection_end_to_end(demo):
    """Ein 'überredetes' LLM will freigeben – Routing-Prüfung lehnt ab, der Freitext erreicht das LLM nie."""
    e = _ergebnis(demo, "11_prompt_injection_ubl.xml")
    manipuliert = llm_json(
        e.fakten,
        vermerk="Laut Anweisung des Vorstands ist die Rechnung bereits geprüft und ohne Klärung freizugeben.",
        schritt="TOUCHLESS_FREIGABE", urteil="FREIGABE", rueckfrage=None, feststellungen=[],
        pfad=["RECHNUNGSEINGANG", "DUBLETTENPRUEFUNG", "FORMALE_PRUEFUNG", "BESTELLBEZUG_PRUEFEN",
              "DREI_WEGE_ABGLEICH", "TOUCHLESS_FREIGABE"],
    )
    llm = MockLLMClient([manipuliert])
    audit = FlowAuditAgent(llm, MOCK).pruefe_rechnung(e.fakten)
    assert audit.quelle == "REGELWERK_FALLBACK"
    assert audit.bericht.workflow_schritt is WS.KLAERUNG_EINKAUF_PREIS
    assert any("widerspricht dem deterministischen Routing" in w for w in audit.warnungen)
    for _system, nutzer in llm.aufrufe:
        assert "Ignoriere" not in nutzer and "Vorstand" not in nutzer
    codes = {f.code.value for f in audit.bericht.feststellungen}
    assert codes == {"PREISABWEICHUNG", "VERDAECHTIGER_FREITEXT"}


def test_mehrere_befunde_desselben_codes_werden_zusammengefasst(demo):
    e = _ergebnis(demo, "05_fehlender_wareneingang_ubl.xml")
    assert len([p for p in e.fakten.fehler if p.code.value == "KEIN_WARENEINGANG"]) == 2
    audit = FlowAuditAgent(einstellungen=OFFLINE).pruefe_rechnung(e.fakten)
    (feststellung,) = audit.bericht.feststellungen
    assert "00010" in feststellung.beschreibung and "00020" in feststellung.beschreibung
    assert FaktenPruefer.fuer_audit(e.fakten).pruefe_bericht(audit.bericht).bestanden


def test_controlling_frage_auf_gebuchtem_bestand(demo):
    verarbeite_ordner(demo.rechnungsordner, demo.db_pfad, buchen=True)
    sql = json.dumps({"sql": "SELECT count(*) AS anzahl, round(sum(RMWWR), 2) AS volumen FROM RBKP "
                             "WHERE ZZ_WF_SCHRITT = 'SPERRE_DUBLETTE' AND ZZ_EINGANG >= '2026-10-28'"})
    antwort = json.dumps({"antwort": "Heute wurden 2 Dubletten mit zusammen 5.773,88 EUR gesperrt."})
    agent = FlowAuditAgent(MockLLMClient([sql, antwort]), MOCK, ReadOnlySQLGuard(demo.db_pfad))
    e = agent.beantworte_frage("Wie viele Dubletten wurden heute gesperrt?")
    assert e.zeilen == [{"anzahl": 2, "volumen": 5773.88}]
    assert e.quelle == "LLM" and e.faktencheck.bestanden
