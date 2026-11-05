"""Designsystem (aus VarianceAI übernommen) und interaktive Diagramme des Cockpits – beide Darstellungen."""

from __future__ import annotations

import importlib.util
import re
from datetime import date
from decimal import Decimal

import pytest

from src import ui_design as ui
from src import ui_diagramme as dia
from src.bpmn_tracker import berechne_kpis, lade_vorgaenge, prozessdiagramm, wochenreihen
from src.demo import batchlauf, stelle_demodaten_sicher


@pytest.fixture(params=ui.MODI)
def modus(request):
    ui.setze_modus(request.param)
    yield request.param
    ui.setze_modus("dunkel")


@pytest.fixture(scope="module")
def lauf(tmp_path_factory):
    from src.seed_data import erzeuge_demo_daten

    ordner = tmp_path_factory.mktemp("ui_daten")
    erzeuge_demo_daten(ordner, referenzdatum=date(2026, 10, 28))
    demo = stelle_demodaten_sicher(ordner)
    b = batchlauf(demo, Decimal("2.0"), tmp_path_factory.mktemp("ui_lauf"))
    return demo, b


def _kontrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        kanal = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        kanal = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in kanal]
        return 0.2126 * kanal[0] + 0.7152 * kanal[1] + 0.0722 * kanal[2]

    hell, dunkel = sorted((lum(a), lum(b)), reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


def test_html_wird_maskiert():
    boese = '<img src=x onerror="alert(1)">'
    html = (ui.kpi_leiste([ui.Kpi(boese, boese, boese)]) + ui.protokoll([ui.Logeintrag("1", boese, "neutral", boese, boese)])
            + ui.texttabelle([boese], [[boese]]) + ui.saetze([boese]) + ui.zitat(boese) + ui.urteil_pill(boese)
            + ui.kopfzeile(boese, boese, boese, boese, [], logo=boese))
    assert "<img src=x" not in html and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_grafiken_als_data_uri_ohne_inline_svg():
    for html in (ui.symbol("check"), ui.sparkline([1, 2, 3], [1, 1, 2]), ui.ring(0.5, "1/2")):
        assert "<svg" not in html and 'src="data:image/svg+xml;base64,' in html


def test_logo_und_untertitel_von_flowaudit():
    kopf = ui.kopfzeile("FlowAudit-AI", "P2P-Audit-Copilot · E-Rechnung & 3-Way-Matching", "m", "u", [])
    assert '<div class="va-logo">§</div>' in kopf and "E-Rechnung &amp; 3-Way-Matching" in kopf


def test_farben_semantisch_getrennt(modus):
    """Der Akzent ist für Interaktion/KI reserviert und kollidiert nicht mit den Datenfarben."""
    daten = {ui.FARBEN[k] for k in ("ac", "py", "gut", "schlecht", "warnung")}
    assert ui.FARBEN["akzent"] not in daten
    assert ui.URTEIL_TON == {"FREIGABE": "gut", "KLAERUNG": "warnung", "SPERRE": "schlecht", "ABGELEHNT": "schlecht"}


def test_paletten_kontraste(modus):
    """Agenten-Standard v1, Abschnitt 5: Textfarben ≥ 4,5:1, Datenfarben günstig/ungünstig ≥ 3:1."""
    f = ui.PALETTEN[modus]
    for schluessel in ("text", "text_2", "text_leise", "gut_text", "schlecht_text", "warnung", "akzent_text"):
        assert _kontrast(f[schluessel], f["karte"]) >= 4.5, (modus, schluessel)
    for schluessel in ("gut", "schlecht"):
        assert _kontrast(f[schluessel], f["karte"]) >= 3.0, (modus, schluessel)


def test_mische_liefert_deckende_hexfarbe():
    assert ui.mische("#FFFFFF", "#000000", 0.5) == "#808080"
    assert ui.mische("#3AAB86", "#161615", 1.0) == "#3AAB86"


def _alle_diagramme(lauf):
    demo, b = lauf
    k = b.kpis
    sperren = sum(v for s, v in k.sperrgruende.items() if s.startswith("SPERRE"))
    return [dia.wochenverlauf(wochenreihen(lade_vorgaenge(b.db_kopie), demo.stichtag)),
            dia.gruendebalken(k.sperrgruende), dia.gruendebalken({}),
            dia.urteilsbalken(k.anzahl_touchless, sum(k.sperrgruende.values()) - sperren, sperren)]


def test_diagramme_sind_gueltige_vega_lite_specs(lauf, modus):
    for chart in _alle_diagramme(lauf):
        spec = chart.to_dict()  # validiert gegen das Vega-Lite-Schema
        assert spec["background"] == "transparent"
        assert spec["config"]["axis"]["labelColor"] == ui.PALETTEN[modus]["text_leise"]  # Farben der aktiven Palette


def test_diagramme_ohne_akzentfarbe_und_mit_deutschen_zahlen(lauf, modus):
    import json

    akzent = ui.PALETTEN[modus]["akzent"]
    for chart in _alle_diagramme(lauf):
        text = json.dumps(chart.to_dict())
        assert akzent not in text                                     # Kupfer nur für Bedienung und KI
    daten = dia.gruendedaten({"KLAERUNG_EINKAUF_PREIS": 3, "SPERRE_DUBLETTE": 1})
    assert list(daten["label"]) == ["3 · 75 %", "1 · 25 %"] and list(daten["art"]) == ["Klärung", "Sperre"]


def test_wochenverlauf_summiert_exakt(lauf):
    demo, b = lauf
    reihen = wochenreihen(lade_vorgaenge(b.db_kopie), demo.stichtag)
    df = dia.wochendaten(reihen)
    assert df.groupby("woche", sort=False)["anzahl"].sum().tolist() == [w.eingegangen for w in reihen]
    assert (df["anzahl"] >= 0).all()                                   # keine negativen Stapel, Achse ab 0


@pytest.mark.skipif(importlib.util.find_spec("vl_convert") is None, reason="vl-convert nicht installiert")
def test_diagramme_rendern_fehlerfrei(lauf):
    import vl_convert

    for chart in _alle_diagramme(lauf):
        assert vl_convert.vegalite_to_svg(chart.to_dict()).startswith("<svg")


def test_mermaid_farbschema_aus_aktiver_palette(modus, fakten_preis):
    schema = dia.mermaid_farbschema()
    code = prozessdiagramm(fakten_preis, "bpmn", farbschema=schema)
    assert f"stroke:{ui.FARBEN['warnung']}" in code                  # aktueller Haltepunkt: Klärung
    for zeile in code.splitlines():
        if zeile.strip().startswith(("classDef", "linkStyle")):
            assert "rgba" not in zeile and not re.search(r"#[0-9A-Fa-f]{8}\b", zeile)  # Mermaid: nur deckende Hexwerte
    standard = prozessdiagramm(fakten_preis, "bpmn")
    assert "#1d4ed8" in standard                                      # Druckstil für README/docs unverändert


def test_letzte_woche_entspricht_den_kennzahlen(lauf):
    """Invariante: Das letzte Fenster endet am Stichtag – Bestandsgrößen stimmen exakt mit berechne_kpis überein."""
    demo, b = lauf
    vorgaenge = lade_vorgaenge(b.db_kopie)
    letzte = wochenreihen(vorgaenge, demo.stichtag, 8)[-1]
    k = berechne_kpis(vorgaenge, demo.stichtag)
    assert letzte.bis == demo.stichtag
    assert letzte.volumen_offen_eur == k.volumen_offen_eur
    assert letzte.skonto_gefaehrdet_eur == k.skonto_gefaehrdet_eur
