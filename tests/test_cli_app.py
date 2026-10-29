"""Rauchtests: CLI (run_agent.py), Streamlit-Cockpit (app.py) und Excel-/Power-BI-Export."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from src.demo import batchlauf, stelle_demodaten_sicher
from src.exporter import pruefe_hashkette, speichere_excel_bericht, speichere_powerbi_paket

PROJEKT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def projektkopie(tmp_path, demo) -> Path:
    """Kopie des Projekts mit eigenen Demo-Daten – CLI/App verändern nie das echte data/."""
    ziel = tmp_path / "projekt"
    ziel.mkdir()
    for name in ("src", "static", ".streamlit", "docs"):
        shutil.copytree(PROJEKT / name, ziel / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("run_agent.py", "app.py"):
        shutil.copy(PROJEKT / name, ziel / name)
    shutil.copytree(demo.db_pfad.parent, ziel / "data")
    return ziel


def _cli(projekt: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "run_agent.py", "--keine-farben", *args], cwd=projekt,
                          capture_output=True, text=True, timeout=180,
                          env={"LLM_PROVIDER": "offline", "PATH": "", "FLOWAUDIT_DATA_DIR": str(projekt / "data")})


class TestCLI:
    def test_alle_musterdokumente(self, projektkopie):
        r = _cli(projektkopie, "alle")
        assert r.returncode == 0, r.stderr
        assert "15/15 Dokumente entsprechen der erwarteten Route." in r.stdout
        assert "13_zugferd_pdf_touchless.pdf" in r.stdout and "MELDUNG_COMPLIANCE" in r.stdout

    def test_pruefe_pdf_mit_diagramm(self, projektkopie):
        r = _cli(projektkopie, "pruefe", "data/invoices/14_zugferd_pdf_mengenabweichung.pdf", "--diagramm", "epk")
        assert r.returncode == 0, r.stderr
        assert "KLAERUNG  →  KLAERUNG_EINKAUF_MENGE" in r.stdout
        assert "PDF/A-3 · factur-x.xml" in r.stdout and "flowchart TD" in r.stdout
        assert "Aktionsentwurf [ENTWURF] NOTIZ_EINKAUF" in r.stdout

    def test_pruefe_json_und_freigabe_protokoll(self, projektkopie):
        r = _cli(projektkopie, "pruefe", "data/invoices/09_formfehler_ohne_ustid_ubl.xml", "--json",
                 "--freigeben", "Erika Musterfrau")
        daten = json.loads(r.stdout)
        assert daten["schritt"] == "KLAERUNG_LIEFERANT_FORMFEHLER"
        assert daten["aktionen"][0]["status"] == "FREIGEGEBEN"
        protokoll = (projektkopie / "data" / "aktionsprotokoll.jsonl").read_text("utf-8").splitlines()
        assert json.loads(protokoll[-1])["bearbeitet_von"] == "Erika Musterfrau"

    def test_sql_guardrail(self, projektkopie):
        r = _cli(projektkopie, "sql", "SELECT IBAN FROM LFBK")
        assert r.returncode == 3 and "BLOCKIERT – Schicht 2" in r.stdout
        r = _cli(projektkopie, "sql", "SELECT count(*) AS n FROM LFA1")
        assert r.returncode == 0 and '{"n": 6}' in r.stdout

    def test_kpi_und_export(self, projektkopie):
        r = _cli(projektkopie, "kpi")
        assert r.returncode == 0 and "Dunkelverarbeitungsquote" in r.stdout and "0 wirksam" in r.stdout
        r = _cli(projektkopie, "export", "--ziel", "exports")
        assert r.returncode == 0 and "Hash-Kette intakt" in r.stdout
        assert (projektkopie / "exports" / "flowaudit_pruefbericht.xlsx").exists()
        assert (projektkopie / "exports" / "flowaudit_powerbi.zip").exists()

    def test_frage_ohne_llm_wird_sauber_abgelehnt(self, projektkopie):
        r = _cli(projektkopie, "frage", "Wie viele Rechnungen sind gesperrt?")
        assert r.returncode == 3 and "Offline-Modus" in r.stdout

    def test_fehlender_api_key_faellt_auf_offline_zurueck(self, projektkopie):
        r = _cli(projektkopie, "--provider", "anthropic", "pruefe", "data/invoices/01_touchless_xrechnung_ubl.xml")
        assert r.returncode == 0 and "Offline-Modus wird verwendet" in r.stderr
        assert "TOUCHLESS_FREIGABE" in r.stdout


class TestStreamlitApp:
    @pytest.fixture()
    def app(self, projektkopie, monkeypatch):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("LLM_PROVIDER", "offline")
        monkeypatch.setenv("FLOWAUDIT_DATA_DIR", str(projektkopie / "data"))
        monkeypatch.chdir(projektkopie)
        return AppTest.from_file(str(projektkopie / "app.py"), default_timeout=180)

    def test_start_ohne_fehler(self, app):
        app.run()
        assert not app.exception, [e.value for e in app.exception]
        labels = [t.label for t in app.tabs]
        assert {"🔍 Live-Rechnungsprüfung", "📊 Controlling & KPIs", "🛡️ Security & Guardrails"} <= set(labels)
        assert any("Aktionsentwürfe" in s.value for s in app.subheader)

    def test_kopfbereich_titel_und_leitlinie(self, app):
        app.run()
        assert app.title[0].value == "FlowAudit-AI – Prüf- & Freigabeplattform für Purchase-to-Pay"
        assert any("der Mensch gibt frei" in m.value and "§ 14 UStG" in m.value for m in app.markdown)

    def test_aufbau_tabelle_offen_und_dynamisch(self, app, projektkopie):
        from src.bpmn_tracker import berechne_kpis, lade_vorgaenge
        from src.demo import batchlauf, stelle_demodaten_sicher

        app.run()
        (aufbau,) = [e for e in app.expander if e.label == "🧭 So ist die App aufgebaut"]
        assert aufbau.proto.expanded is True
        tabelle = next(m.value for m in aufbau.markdown if "| Tab (Modul) |" in m.value)
        demo = stelle_demodaten_sicher(projektkopie / "data")
        erwartet = batchlauf(demo).kpis.anzahl_rechnungen
        assert f"({erwartet} Vorgänge)" in tabelle and "62 Vorgänge" not in tabelle
        assert "12 XML-Rechnungen" in tabelle and "3 PDF-Dokumente" in tabelle
        assert berechne_kpis(lade_vorgaenge(demo.db_pfad)).anzahl_rechnungen == 50  # Original bleibt unverändert

    def test_leitfrage_in_jedem_tab(self, app):
        app.run()
        leitfragen = [i.value for i in app.info if i.icon == "🎯" and i.value.startswith("**Leitfrage:**")]
        assert len(leitfragen) == 3
        assert all("**Was das Modul tut:**" in t and "Für:" not in t for t in leitfragen)
        assert "Human-in-the-Loop" in leitfragen[0]

    def test_kein_vier_augen_versprechen(self, app):
        app.run()
        namen = [t.label for t in app.text_input if t.label.startswith("Ihr Name")]
        assert namen and all("Human-in-the-Loop" in n and "Vier-Augen" not in n for n in namen)

    @pytest.mark.parametrize("button, praefix, ziel_tab, urteil", [
        ("① Preisabweichung", "03_", "🔍 Live-Rechnungsprüfung", "KLAERUNG"),
        ("② Prompt-Injection", "11_", "🛡️ Security & Guardrails", "KLAERUNG"),
        ("③ ZUGFeRD-PDF", "13_", "🔍 Live-Rechnungsprüfung", "FREIGABE"),
    ])
    def test_demo_in_60_sekunden(self, app, button, praefix, ziel_tab, urteil):
        app.run()
        assert app.session_state["musterdokument"].startswith("03_")  # Startfall
        next(b for b in app.button if b.label == button).click()
        app.run()
        assert not app.exception, [e.value for e in app.exception]
        assert app.session_state["musterdokument"].startswith(praefix)
        assert app.session_state["haupttab"] == ziel_tab
        assert any(f"<b>{'✅' if urteil == 'FREIGABE' else '✉️'} {urteil}</b>" in m.value for m in app.markdown)
        aktiv = next(b for b in app.button if b.label == button)
        assert aktiv.proto.type == "primary"

    def test_export_buttons_in_der_seitenleiste(self, app):
        app.run()
        labels = [b.label for b in app.sidebar.get("download_button")]
        assert labels == ["📊 Excel-Prüfbericht (.xlsx)", "🟨 Power-BI-Paket (.zip)"]

    def test_jedes_musterdokument_im_cockpit(self, app):
        app.run()
        def auswahl():
            return next(s for s in app.selectbox if s.label == "Musterdokument")
        for wert in list(auswahl().options):
            auswahl().set_value(wert)
            app.run()
            assert not app.exception, (wert, [e.value for e in app.exception])

    def test_security_demo(self, app):
        app.run()
        next(b for b in app.button if b.label.startswith("🧨")).click()
        app.run()
        assert any("Endgültige Route: KLAERUNG_EINKAUF_PREIS" in m.value for m in app.markdown)
        next(s for s in app.selectbox if s.label == "Angriff / Abfrage").set_value("IBAN aller Lieferanten auslesen (DSGVO/IKS)")
        app.run()
        next(b for b in app.button if b.label.startswith("▶️ Abfrage")).click()
        app.run()
        assert any("BLOCKIERT in Schicht 2" in e.value for e in app.error)

    def test_freigabe_im_cockpit(self, app, projektkopie):
        app.run()
        name = next(t for t in app.text_input if t.label.startswith("Ihr Name"))
        name.set_value("Erika Musterfrau")
        app.run()
        next(b for b in app.button if b.label == "✅ Freigeben").click()
        app.run()
        assert any("FREIGEGEBEN" in s.value for s in app.success)
        assert "Erika Musterfrau" in (projektkopie / "data" / "aktionsprotokoll.jsonl").read_text("utf-8")


class TestExport:
    def test_dateien_schreiben(self, demo, tmp_path):
        b = batchlauf(stelle_demodaten_sicher(demo.db_pfad.parent), Decimal("2.0"), tmp_path)
        xlsx = speichere_excel_bericht(b.db_kopie, tmp_path / "out" / "bericht.xlsx", demo.referenzdatum)
        pbi = speichere_powerbi_paket(b.db_kopie, tmp_path / "out" / "powerbi.zip", demo.referenzdatum)
        assert xlsx.stat().st_size > 10_000 and pbi.stat().st_size > 1_000
        assert pruefe_hashkette(xlsx) == (True, None)
