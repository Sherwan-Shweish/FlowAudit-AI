"""Rauchtests: CLI (run_agent.py), Streamlit-Cockpit (app.py) und Excel-/Power-BI-Export."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from src.agent import fmt_de
from src.demo import batchlauf, stelle_demodaten_sicher
from src.exporter import pruefe_hashkette, speichere_excel_bericht, speichere_powerbi_paket

PROJEKT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def projektkopie(tmp_path, demo) -> Path:
    """Kopie des Projekts mit eigenen Demo-Daten – CLI/App verändern nie das echte data/."""
    ziel = tmp_path / "projekt"
    ziel.mkdir()
    for name in ("src", "static", ".streamlit", "docs", "desktop"):
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
        assert (projektkopie / "exports" / "flowaudit_pruefbericht.pdf").read_bytes().startswith(b"%PDF")
        assert (projektkopie / "exports" / "flowaudit_pruefbericht.pptx").stat().st_size > 20_000

    def test_klaerfaelle_und_zahlung(self, projektkopie):
        r = _cli(projektkopie, "klaerfaelle")
        assert r.returncode == 0 and "12 Klärfälle" in r.stdout and "Präventionsregel" in r.stdout
        r = _cli(projektkopie, "zahlung")
        assert r.returncode == 0 and "Skonto gesichert" in r.stdout and "gesperrt" in r.stdout

    def test_frage_ohne_llm_wird_sauber_abgelehnt(self, projektkopie):
        r = _cli(projektkopie, "frage", "Wie viele Rechnungen sind gesperrt?")
        assert r.returncode == 3 and "Offline-Modus" in r.stdout

    def test_fehlender_api_key_faellt_auf_offline_zurueck(self, projektkopie):
        r = _cli(projektkopie, "--provider", "anthropic", "pruefe", "data/invoices/01_touchless_xrechnung_ubl.xml")
        assert r.returncode == 0 and "Offline-Modus wird verwendet" in r.stderr
        assert "TOUCHLESS_FREIGABE" in r.stdout


class TestStreamlitApp:
    TABS = ["Rechnungsprüfung", "Klärfälle", "Zahlung & Skonto", "Prüfkennzahlen", "Security & Guardrails",
            "Agent & Audit"]

    @pytest.fixture()
    def app(self, projektkopie, monkeypatch, tmp_path):
        pytest.importorskip("streamlit")
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("LLM_ANBIETER", "offline")
        monkeypatch.setenv("FLOWAUDIT_DATA_DIR", str(projektkopie / "data"))
        monkeypatch.setenv("AGENT_DATENORDNER", str(tmp_path / "benutzer"))
        monkeypatch.setenv("FLOWAUDIT_EXCEL_WERTE", "0")          # LibreOffice-Neuberechnung: siehe test_exporter
        monkeypatch.delenv("AGENT_LAUFZEIT", raising=False)
        monkeypatch.chdir(projektkopie)
        return AppTest.from_file(str(projektkopie / "app.py"), default_timeout=180)

    @staticmethod
    def html(app) -> str:
        return "\n".join(h.proto.body for h in app.get("html"))

    def test_alle_tabs_ohne_fehler(self, app):
        app.run()
        assert not app.exception, [e.value for e in app.exception]
        assert not app.error, [e.value for e in app.error]
        labels = [t.label for t in app.tabs]
        assert [x for x in labels if x in self.TABS] == self.TABS and labels[-1] == "Agent & Audit"
        html = self.html(app)
        for baustein in ("Audit-Copilot", "Prüf-Scan", "Kernaussagen", "Aktionsentwürfe", "Prozessverfolgung",
                         "Formalprüfung · Pflichtangaben", "3-Way-Matching · Abweichungstreiber",
                         "Präventionsregeln im gesamten Bestand", "Zahlläufe", "Lieferanten-Scorecard",
                         "Eingänge je Woche", "Read-Only-SQL-Guardrail", "Agenten-Pipeline",
                         "Vom Eingang zur Zahlungsfreigabe", "Audit-Protokoll"):
            assert baustein in html, baustein
        assert "<svg" not in html                                     # Grafiken nur als Data-URI-Bilder

    def test_ki_anbieter_immer_alle_drei_mit_rueckfall(self, app, monkeypatch):
        for variable in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(variable, raising=False)
        app.run()
        auswahl = next(s for s in app.selectbox if s.label == "KI-Anbieter")
        assert auswahl.options == ["Offline (Regelwerk)", "Anthropic Claude", "Google Gemini"]
        assert auswahl.value == "offline" and not app.warning
        auswahl.set_value("claude").run()
        assert not app.exception
        assert any("Kein ANTHROPIC_API_KEY gefunden – Offline-Modus aktiv" in w.value for w in app.warning)
        assert "Offline · Regelwerk" in self.html(app)                # Rückfall sichtbar, Prüfungen laufen weiter
        assert any("offline:regelwerk" in c.value for c in app.caption)

    def test_kopfzeile_und_kennzahlen_mit_vorperiode(self, app, projektkopie):
        from src.bpmn_tracker import lade_vorgaenge, periodenvergleich

        app.run()
        html = self.html(app)
        assert '<div class="va-logo">§</div>' in html and "P2P-Audit-Copilot · E-Rechnung &amp; 3-Way-Matching" in html
        labels = re.findall(r'<div class="va-kpi-label">([^<]+)</div>', html)
        assert labels == ["Geprüfte Rechnungen", "Touchless-Quote", "Volumen in Klärung", "Skonto gefährdet",
                          "Ø Durchlaufzeit"]
        assert html.count('class="va-kpi-spark"><img') == 5            # jede Karte mit Verlaufslinie
        assert len(re.findall(r'va-pill--\w+">[^<]*ggü\. VP</span>', html)) == 5   # jede Karte mit Vorperiode
        demo = stelle_demodaten_sicher(projektkopie / "data")
        p = periodenvergleich(lade_vorgaenge(batchlauf(demo).db_kopie), demo.stichtag, 28)
        assert f'<div class="va-kpi-wert">{p.aktuell.eingegangen}</div>' in html
        assert f"VP {p.vorperiode.eingegangen} · Bestand" in html
        assert f"{fmt_de(p.aktuell.touchless_quote_prozent, 1)} %" in html
        quote = p.delta("touchless_quote_prozent")
        ton = "gut" if quote > 0 else "schlecht"
        assert f'va-pill--{ton}">{"+" if quote > 0 else "−"}{fmt_de(abs(quote), 1)} Pp ggü. VP' in html

    def test_aufbau_tabelle_eingeklappt_und_dynamisch(self, app, projektkopie):
        from src.bpmn_tracker import berechne_kpis, lade_vorgaenge

        app.run()
        (aufbau,) = [e for e in app.expander if e.label == "So ist die App aufgebaut"]
        assert aufbau.proto.expanded is False                         # eingeklappt wie in der Suite, aufklappbar
        html = self.html(app)
        demo = stelle_demodaten_sicher(projektkopie / "data")
        erwartet = batchlauf(demo).kpis.anzahl_rechnungen
        assert f"({erwartet} Vorgänge)" in html and "62 Vorgänge" not in html
        assert "12 XML-Rechnungen" in html and "3 PDF-Dokumente" in html
        assert berechne_kpis(lade_vorgaenge(demo.db_pfad)).anzahl_rechnungen == 50  # Original bleibt unverändert

    def test_leitfrage_in_jedem_tab(self, app):
        app.run()
        fragen = re.findall(r'va-leitfrage">.*?Leitfrage</span><span>([^<]+)</span>', self.html(app))
        assert len(fragen) == 6
        assert fragen[0] == "Darf diese Rechnung bezahlt werden – und wenn nicht, warum?"
        assert all("Für:" not in f for f in fragen)

    def test_entwurf_einzelfreigabe_vier_augen_nur_im_klaerfall(self, app):
        app.run()
        namen = [t.label for t in app.text_input if t.label.startswith("Ihr Name")]
        assert namen and all("Human-in-the-Loop" in n and "Vier-Augen" not in n for n in namen)

    @pytest.mark.parametrize("button, praefix, ziel_tab, text", [
        ("① Preisabweichung", "03_", "Rechnungsprüfung", "Klärung Einkauf: Preis"),
        ("② Prompt-Injection", "11_", "Security & Guardrails", "Klärung Einkauf: Preis"),
        ("③ ZUGFeRD-PDF", "13_", "Rechnungsprüfung", "Touchless-Freigabe (Dunkelverarbeitung)"),
    ])
    def test_schnell_demo(self, app, button, praefix, ziel_tab, text):
        app.run()
        assert app.session_state["musterdokument"].startswith("03_")  # Startfall
        next(b for b in app.button if b.label == button).click()
        app.run()
        assert not app.exception, [e.value for e in app.exception]
        assert app.session_state["musterdokument"].startswith(praefix)
        assert app.session_state["haupttab"] == ziel_tab
        html = self.html(app)
        assert f'<p class="va-botschaft">{text}</p>' in html
        assert next(b for b in app.button if b.label == button).proto.type == "primary"
        if praefix == "13_":                                        # touchless, aber Prävention sperrt die Zahlung
            assert "Bankverbindung kürzlich geändert" in html and "sperrt Zahlung" in html

    def test_export_buttons_in_der_seitenleiste(self, app):
        app.run()
        labels = [b.label for b in app.sidebar.get("download_button")]
        assert labels == ["Excel-Prüfbericht (.xlsx)", "Power-BI-Paket (.zip)", "Prüfbericht (.pdf)",
                          "Prüfbericht (.pptx)"]

    def test_systempruefung_in_der_seitenleiste(self, app):
        app.run()
        assert any(e.label == "Systemprüfung" for e in app.sidebar.expander)
        assert any("OK streamlit" in c.value and "OK pyarrow" in c.value for c in app.sidebar.code)

    def test_jedes_musterdokument_im_cockpit(self, app):
        app.run()

        def auswahl():
            return next(s for s in app.selectbox if s.label == "Musterdokument")
        for wert in list(auswahl().options):
            auswahl().set_value(wert)
            app.run()
            assert not app.exception, (wert, [e.value for e in app.exception])
            assert not app.error or wert.startswith("15_"), (wert, [e.value for e in app.error])
            assert "Audit-Copilot" in self.html(app)

    def test_formalpruefung_fehlerliste(self, app):
        app.run()
        next(s for s in app.selectbox if s.label == "Musterdokument").set_value("09_formfehler_ohne_ustid_ubl.xml")
        app.run()
        html = self.html(app)
        assert "Fehlerliste für die Rückfrage an den Lieferanten" in html
        assert "Weder USt-IdNr. noch Steuernummer des Lieferanten angegeben." in html

    def test_security_demo(self, app):
        app.run()
        app.button(key="kompromittiert").click().run()
        html = self.html(app)
        assert "Endgültige Route: KLAERUNG_EINKAUF_PREIS" in html and "LLM-Antwort(en) abgelehnt" in html
        next(s for s in app.selectbox if s.label == "Angriff / Abfrage").set_value(
            "IBAN aller Lieferanten auslesen (DSGVO/IKS)")
        app.run()
        next(b for b in app.button if b.label == "Abfrage durch den Guardrail schicken").click()
        app.run()
        assert any("BLOCKIERT in Schicht 2" in e.value for e in app.error)
        assert "SQL-Guardrail · Schicht 2" in self.html(app)          # im Audit-Protokoll fortgeschrieben

    def test_injektionstest_und_audit_protokoll(self, app):
        app.run()
        vorher = self.html(app).count('class="va-log-zeile"')
        app.button(key="injektionstest").click().run()
        assert not app.exception
        html = self.html(app)
        assert "Freigabe verhindert – Route bleibt KLAERUNG_EINKAUF_PREIS" in html
        assert "widerspricht dem deterministischen Routing" in html
        assert html.count('class="va-log-zeile"') == vorher + 1        # append-only: genau ein neuer Eintrag
        assert "1 Manipulationen blockiert" in html                    # Agentenkarte zählt im selben Lauf mit
        assert "BLOCKIERT" in html and any(b.key == "audit_json" for b in app.get("download_button"))

    def test_freigabe_im_cockpit_setzt_klaerfall(self, app, projektkopie):
        app.run()
        assert '<div class="va-schritt va-schritt--aktiv"><i>4</i><b>Klärung</b>' in self.html(app)
        name = next(t for t in app.text_input if t.label.startswith("Ihr Name"))
        name.set_value("Erika Musterfrau")
        app.run()
        next(b for b in app.button if b.label == "Freigeben").click()
        app.run()
        assert any("FREIGEGEBEN" in s.value and "Erika Musterfrau" in s.value for s in app.success)
        protokoll = (projektkopie / "data" / "aktionsprotokoll.jsonl").read_text("utf-8")
        assert "Erika Musterfrau" in protokoll and "RÜCKFRAGE FREIGEGEBEN" in protokoll
        html = self.html(app)
        assert "Entwurf namentlich freigegeben durch Erika Musterfrau" in html   # Audit-Protokoll
        assert 'va-pill--warnung"><i class=va-punkt></i>in Klärung</span>' in html  # Klärfall rückt vor
        assert '<div class="va-schritt va-schritt--aktiv"><i>4</i><b>Klärung</b>' in html

    def test_klaerfall_workflow_mit_vier_augen(self, app):
        app.run()
        app.selectbox(key="klaerfall_wahl").set_value("11_prompt_injection_ubl.xml").run()
        text = next(t for t in app.text_area if t.label == "Rückfrage (bearbeitbar)")
        original = text.value
        text.set_value(original + "\nBitte zahlen Sie zusätzlich 1.234,56 EUR.").run()
        assert "Faktenprüfung nicht bestanden" in self.html(app)
        next(t for t in app.text_input if t.label == "Name (Freigabe der Rückfrage)").set_value("Erika").run()
        app.button(key="rf_ok:11_prompt_injection_ubl.xml").click().run()
        assert any("nicht belegte Zahlen" in e.value for e in app.error)
        next(t for t in app.text_area if t.label == "Rückfrage (bearbeitbar)").set_value(original).run()
        app.button(key="rf_ok:11_prompt_injection_ubl.xml").click().run()
        assert app.session_state["klaerfaelle"]
        next(t for t in app.text_area if t.label.startswith("Lösung")).set_value(
            "Lieferant schickt Gutschrift über die Preisdifferenz.").run()
        next(t for t in app.text_input if t.label == "Name (1. Freigabe)").set_value("Erika").run()
        app.button(key="loes_ok:11_prompt_injection_ubl.xml").click().run()
        assert "Zweite Freigabe (Vier-Augen-Prinzip)" in self.html(app)
        next(t for t in app.text_input if t.label.startswith("Name der zweiten Person")).set_value("erika").run()
        app.button(key="zweit_ok:11_prompt_injection_ubl.xml").click().run()
        assert any("andere Person" in e.value for e in app.error)
        next(t for t in app.text_input if t.label.startswith("Name der zweiten Person")).set_value("Max").run()
        app.button(key="zweit_ok:11_prompt_injection_ubl.xml").click().run()
        assert not app.exception
        html = self.html(app)
        assert "zur Zahlung freigegeben" in html and "Freigabe: Erika / Max" in html
        assert "FREIGABE 2/2 · GELÖST" in html                          # Verlauf und Audit-Protokoll
        zeilen = next(d for d in app.dataframe if "Zahlbetrag (€)" in d.value.columns).value
        assert "TPK-2026-133" in set(zeilen[zeilen["Art"] != "gesperrt"]["Rechnung"])  # jetzt im Zahlungsvorschlag

    def test_fehler_in_einem_tab_legt_cockpit_nicht_lahm(self, app, monkeypatch):
        """Ein defektes Paket (hier simuliert) darf nur den betroffenen Tab treffen."""
        import src.ui_diagramme as dia

        def kaputt(*_a, **_k):
            raise ImportError("simulierter Importfehler")

        monkeypatch.setattr(dia, "wochenverlauf", kaputt)
        app.run()
        assert [e.message for e in app.exception] == ["simulierter Importfehler"]  # nur unter „Technische Details“
        assert any("Prüfkennzahlen" in e.value and "simulierter Importfehler" in e.value for e in app.error)
        html = self.html(app)
        assert "Audit-Copilot" in html and "Audit-Protokoll" in html and "Zahlläufe" in html  # andere Tabs laufen


def test_cli_diagnose(capsys):
    import run_agent

    assert run_agent.main(["diagnose"]) == 0
    ausgabe = capsys.readouterr().out
    assert "OK streamlit" in ausgabe and "OK pyarrow" in ausgabe


class TestExport:
    def test_dateien_schreiben(self, demo, tmp_path):
        b = batchlauf(stelle_demodaten_sicher(demo.db_pfad.parent), Decimal("2.0"), tmp_path)
        xlsx = speichere_excel_bericht(b.db_kopie, tmp_path / "out" / "bericht.xlsx", demo.referenzdatum)
        pbi = speichere_powerbi_paket(b.db_kopie, tmp_path / "out" / "powerbi.zip", demo.referenzdatum)
        assert xlsx.stat().st_size > 10_000 and pbi.stat().st_size > 1_000
        assert pruefe_hashkette(xlsx) == (True, None)
