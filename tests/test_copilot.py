"""Audit-Copilot, Wochenreihen, Injection-Test und Anbieter-Konfiguration (Agenten-Standard v1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.agent import Einstellungen, WorkflowSchritt
from src.assistent import KIAssistent
from src.bpmn_tracker import Vorgang, berechne_kpis, wochenreihen
from src.copilot import ABLAUF, ablauf_status, kernaussagen, pruef_scan, pruefzahlen
from src.demo import (
    INJEKTIONSTEXT,
    batchlauf,
    einstellungen_fuer,
    fuehre_injektionstest_aus,
    manipulierte_rechnung,
    stelle_demodaten_sicher,
)
from src.einvoice_parser import lese_rechnung

WS = WorkflowSchritt


@pytest.fixture()
def demodaten(demo):
    return stelle_demodaten_sicher(demo.db_pfad.parent)


@pytest.fixture()
def assistent(demodaten):
    return KIAssistent(demodaten.db_pfad, einstellungen_fuer("offline"), stichtag=demodaten.stichtag)


def _datei(demodaten, praefix: str):
    return demodaten.ordner / next(n for n in demodaten.manifest if n.startswith(praefix))


# --- Prüf-Scan, Kernaussagen, Ablauf -------------------------------------------------------------------------


def test_scan_touchless_ohne_befund(assistent, demodaten):
    v = assistent.pruefe(_datei(demodaten, "01_"))
    scan = pruef_scan(v)
    assert [s[0] for s in scan] == ["Eingang & Format", "Dublettenprüfung", "Formalprüfung § 14 UStG", "Bestellbezug",
                                    "3-Way-Matching", "Freitext BT-22", "Faktenprüfung Prüfvermerk"]
    assert all(ton == "gut" for _, _, ton in scan)
    assert kernaussagen(v)[0].startswith("Ja – die Rechnung")
    assert ablauf_status(v) == [(s, "fertig") for s in ABLAUF]


@pytest.mark.parametrize("praefix, schritt, befund", [
    ("03_", "3-Way-Matching", "Abweichung: Preis"),
    ("05_", "3-Way-Matching", "Abweichung: Wareneingang fehlt"),
    ("06_", "3-Way-Matching", "Abweichung: Teil-Lieferung"),
    ("07_", "Bestellbezug", "keine Bestellung (Maverick Buying)"),
    ("08_", "Dublettenprüfung", "Dublette"),
    ("09_", "Formalprüfung § 14 UStG", "Formfehler"),
    ("11_", "Freitext BT-22", "Steuerungsmuster erkannt – isoliert"),
])
def test_scan_benennt_den_grund(assistent, demodaten, praefix, schritt, befund):
    v = assistent.pruefe(_datei(demodaten, praefix))
    eintrag = next(s for s in pruef_scan(v) if s[0] == schritt)
    assert befund in eintrag[1] and eintrag[2] in ("schlecht", "warnung")
    assert kernaussagen(v)[0].startswith("Nein, noch nicht")
    assert v.fakten.rechnung.rechnungsnummer in kernaussagen(v)[0]


def test_kernaussagen_nur_aus_dem_pruefprotokoll(assistent, demodaten):
    """Jede Zahl der Kernaussagen stammt aus Rechnung oder Begründung (deterministisch, ohne LLM)."""
    import re

    v = assistent.pruefe(_datei(demodaten, "03_"))
    quellen = " ".join(v.begruendung) + v.fakten.rechnung.model_dump_json()
    for satz in kernaussagen(v):
        for zahl in re.findall(r"\d[\d.]*,\d+", satz):
            roh = zahl.replace(".", "").replace(",", ".")
            assert zahl in quellen or roh.rstrip("0").rstrip(".") in quellen or zahl.endswith("€"), (zahl, satz)


def test_ablauf_klaerung_bis_zur_entscheidung(assistent, demodaten):
    v = assistent.pruefe(_datei(demodaten, "03_"))
    assert dict(ablauf_status(v)) == {"Eingang": "fertig", "Formalprüfung": "fertig", "Matching": "fertig",
                                      "Klärung": "aktiv", "Freigabe": "offen"}
    assert dict(ablauf_status(v, entschieden=True))["Freigabe"] == "aktiv"
    dublette = assistent.pruefe(_datei(demodaten, "08_"))
    assert dict(ablauf_status(dublette))["Formalprüfung"] == "offen"   # Sperre vor der Formalprüfung


def test_abgewiesene_datei(assistent, demodaten):
    v = assistent.pruefe(_datei(demodaten, "15_"))
    assert v.abgelehnt
    assert pruef_scan(v)[0][2] == "schlecht" and kernaussagen(v)[0].startswith("Nein – die Datei wurde abgewiesen")
    assert ablauf_status(v)[0] == ("Eingang", "aktiv")


# --- Prüftrichter ------------------------------------------------------------------------------------------------


@pytest.mark.invariante
def test_pruefzahlen_trichter(demodaten, tmp_path):
    b = batchlauf(demodaten, Decimal("2.0"), tmp_path)
    z = pruefzahlen(b.ergebnisse)
    werte = [n for _, n in z.stufen]
    assert werte == sorted(werte, reverse=True)                        # Trichter: jede Stufe ≤ der vorherigen
    assert z.in_toleranz == z.zahlungsfreigegeben                      # deterministisch: Toleranz ⇒ Touchless
    assert z.zahlungsfreigegeben + z.klaerung + z.abgewiesen == z.eingegangen
    touchless = sum(e.schritt is WS.TOUCHLESS_FREIGABE for e in b.ergebnisse)
    assert (z.eingegangen, z.abgewiesen, z.zahlungsfreigegeben) == (15, 1, touchless)
    assert z.sperre == sum(1 for e in b.ergebnisse if e.schritt and e.schritt.value.startswith("SPERRE"))


# --- Wochenreihen ----------------------------------------------------------------------------------------------


def _v(nr: str, schritt: WorkflowSchritt, eingang: datetime, abschluss: datetime | None, brutto: str = "100",
       skonto: str | None = None, frist: date | None = None) -> Vorgang:
    return Vorgang(nr, "100100", schritt, Decimal(brutto), eingang, abschluss,
                   Decimal(skonto) if skonto else None, frist)


@pytest.mark.invariante
def test_wochenreihen_bestand_und_fenster():
    stichtag = date(2026, 10, 28)
    t = datetime(2026, 10, 27, 9, 0)
    vorgaenge = [
        _v("A", WS.TOUCHLESS_FREIGABE, t, t + timedelta(minutes=5)),
        _v("B", WS.KLAERUNG_EINKAUF_PREIS, t, None, "200", "2", date(2026, 11, 2)),
        _v("C", WS.SPERRE_DUBLETTE, t - timedelta(days=9), t - timedelta(days=8, hours=20), "50"),
    ]
    reihen = wochenreihen(vorgaenge, stichtag, 2)
    assert [w.bis for w in reihen] == [date(2026, 10, 21), stichtag]
    assert [w.eingegangen for w in reihen] == [1, 2] and reihen[-1].touchless == 1
    assert reihen[-1].touchless_quote_prozent == Decimal("50.00")
    assert reihen[0].volumen_offen_eur == Decimal("0.00")              # C war vor Ende der Woche abgeschlossen
    assert reihen[-1].volumen_offen_eur == Decimal("200.00")
    assert reihen[-1].skonto_gefaehrdet_eur == Decimal("4.00") == berechne_kpis(vorgaenge, stichtag).skonto_gefaehrdet_eur
    with pytest.raises(ValueError):
        wochenreihen(vorgaenge, stichtag, 0)


# --- Injection-Test per Knopf ----------------------------------------------------------------------------------


def test_manipulierte_rechnung_bleibt_gueltige_e_rechnung(demodaten):
    datei, xml = manipulierte_rechnung(demodaten)
    roh = lese_rechnung(xml)
    assert datei.startswith("03_") and INJEKTIONSTEXT in roh.bemerkungen


def test_injektionstest_blockiert_die_freigabe(demodaten):
    t = fuehre_injektionstest_aus(demodaten)
    assert t.erkannt and not t.im_prompt
    assert t.llm_wollte == "TOUCHLESS_FREIGABE" and t.route == "KLAERUNG_EINKAUF_PREIS" and t.urteil == "KLAERUNG"
    assert t.abgelehnte_antworten >= 1 and t.quelle == "REGELWERK_FALLBACK" and t.blockiert
    assert any("widerspricht dem deterministischen Routing" in g for g in t.gruende)


# --- Anbieter wie in der Suite (LLM_ANBIETER) ------------------------------------------------------------------


@pytest.mark.parametrize("variablen, erwartet", [
    ({"LLM_ANBIETER": "claude"}, "anthropic"),
    ({"LLM_ANBIETER": "mock"}, "mock"),
    ({"LLM_PROVIDER": "gemini"}, "gemini"),                           # ältere Schreibweise bleibt gültig
    ({"LLM_ANBIETER": "offline", "LLM_PROVIDER": "anthropic"}, "offline"),  # LLM_ANBIETER hat Vorrang
    ({}, "offline"),
])
def test_llm_anbieter_wie_in_der_suite(monkeypatch, variablen, erwartet):
    for name in ("LLM_ANBIETER", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    for name, wert in variablen.items():
        monkeypatch.setenv(name, wert)
    assert Einstellungen.aus_umgebung().llm_provider == erwartet
    assert einstellungen_fuer("claude").llm_provider == "anthropic"
