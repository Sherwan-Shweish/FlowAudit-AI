"""
FlowAudit-AI · Interaktives Cockpit (Streamlit)
===============================================

Start:  streamlit run app.py

Tab 1 – Live-Rechnungsprüfung   (Musterrechnung oder Upload → komplette Prüfkette)
Tab 2 – Controlling & KPIs      (Batchlauf aller Musterdokumente, Export)
Tab 3 – Security & Guardrails   (Prompt-Injection, SQL-Guardrail, XML/PDF-Angriffe)

Ohne API-Key läuft das Cockpit vollständig im Offline-Modus.
"""

from __future__ import annotations

import html
import json
import sys
from decimal import Decimal
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PROJEKT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJEKT))

from src.agent import (  # noqa: E402
    FlowAuditAgent,
    MockLLMClient,
    ReadOnlySQLGuard,
    SQLGuardrailVerletzung,
    baue_audit_prompt,
    fmt_datum,
    fmt_de,
)
from src.assistent import KIAssistent, Vorgang  # noqa: E402
from src.bpmn_tracker import kpi_bericht  # noqa: E402
from src.demo import (  # noqa: E402
    ANGRIFFS_DATEIEN,
    ANGRIFFS_SQL,
    batchlauf,
    daten_ordner,
    einstellungen_fuer,
    kompromittierte_antwort,
    protokolliere_freigabe,
    stelle_demodaten_sicher,
    verfuegbare_provider,
)
from src.einvoice_parser import EInvoiceFehler, lese_rechnung  # noqa: E402

st.set_page_config(page_title="FlowAudit-AI Cockpit", page_icon="🧾", layout="wide")

FARBEN = {"FREIGABE": "#1a7f37", "KLAERUNG": "#bf8700", "SPERRE": "#cf222e", "ABGELEHNT": "#cf222e"}
SYMBOL = {"FREIGABE": "✅", "KLAERUNG": "✉️", "SPERRE": "⛔", "ABGELEHNT": "🚫"}
STATUS_SYMBOL = {"OK": "✅", "WARNUNG": "⚠️", "FEHLER": "❌"}
SCHRITT_TEXT = {
    "TOUCHLESS_FREIGABE": "Touchless-Freigabe (Dunkelverarbeitung)",
    "SPERRE_DUBLETTE": "Sperre: Dublette",
    "KLAERUNG_LIEFERANT_FORMFEHLER": "Klärung Lieferant: Formfehler",
    "COMPLIANCE_MAVERICK_BUYING": "Compliance: Maverick Buying",
    "SPERRE_FEHLENDER_WARENEINGANG": "Sperre: Wareneingang fehlt",
    "KLAERUNG_EINKAUF_PREIS": "Klärung Einkauf: Preis",
    "KLAERUNG_EINKAUF_MENGE": "Klärung Einkauf: Menge",
    "ABGELEHNT": "Abgewiesen (keine verarbeitbare E-Rechnung)",
}


# ═════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="Demo-Daten werden vorbereitet …")
def demodaten(version: int, ordner: str):
    return stelle_demodaten_sicher(ordner, neu=version > 0)


@st.cache_data(show_spinner="Batchlauf über alle Musterdokumente …")
def batch(version: int, ordner: str, toleranz: str):
    demo_ = demodaten(version, ordner)
    b = batchlauf(demo_, Decimal(toleranz))
    zeilen = [{
        "Datei": e.datei,
        "Rechnung": e.fakten.rechnung.rechnungsnummer if e.fakten else "–",
        "Lieferant": e.fakten.rechnung.lieferant_name if e.fakten else "–",
        "Brutto (EUR)": fmt_de(e.fakten.rechnung.summe_brutto or Decimal(0)) if e.fakten else "",
        "Ergebnis": e.schritt.value if e.schritt else "ABGELEHNT",
        "Erwartet": demo_.manifest.get(e.datei, {}).get("erwartete_route", "–"),
    } for e in b.ergebnisse]
    return b.kpis.model_dump(mode="json"), kpi_bericht(b.kpis), zeilen, b.statistik, str(b.db_kopie)


@st.cache_data(show_spinner="Excel-Prüfbericht und Power-BI-Paket werden erstellt …")
def exportpaket(version: int, ordner: str, toleranz: str) -> tuple[bytes, bytes]:
    """Excel-Prüfbericht und Power-BI-ZIP auf Basis des Batchlaufs (Datenbankkopie inkl. aller Musterdokumente)."""
    from src.exporter import erstelle_excel_bericht, erstelle_powerbi_paket, lade_exportdaten

    db_kopie = batch(version, ordner, toleranz)[4]
    daten = lade_exportdaten(db_kopie, demodaten(version, ordner).stichtag)
    return erstelle_excel_bericht(db_kopie, daten=daten), erstelle_powerbi_paket(db_kopie, daten=daten)


def mermaid(code: str, hoehe: int = 520) -> None:
    """Rendert Mermaid-Code im Browser.

    Mermaid wird lokal aus ``static/mermaid.min.js`` geladen (Streamlit-Static-Serving, funktioniert offline);
    nur falls das fehlschlägt, aus dem CDN. Der Code stammt ausschließlich aus src/bpmn_tracker.py (feste
    Modelle), wird HTML-escaped und mit ``securityLevel: 'strict'`` gerendert.
    """
    inhalt = f"""<div style="background:#fff;padding:8px;border-radius:8px">
<pre class="mermaid">{html.escape(code)}</pre></div>
<script src="/app/static/mermaid.min.js"></script>
<script>
function zeichne() {{
  mermaid.initialize({{startOnLoad: false, securityLevel: 'strict', flowchart: {{useMaxWidth: true}}}});
  mermaid.run({{querySelector: '.mermaid'}});
}}
if (window.mermaid) {{ zeichne(); }} else {{
  var s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/mermaid@12/dist/mermaid.min.js';
  s.onload = zeichne; document.head.appendChild(s);
}}
</script>"""
    if hasattr(st, "iframe"):  # Streamlit ≥ 1.53
        st.iframe(inhalt, height=hoehe)
    else:  # pragma: no cover
        components.html(inhalt, height=hoehe, scrolling=True)


def badge(urteil: str, text: str) -> None:
    st.markdown(
        f"<div style='padding:14px 18px;border-radius:10px;border:2px solid {FARBEN[urteil]};"
        f"background:{FARBEN[urteil]}14;font-size:1.25rem'><b>{SYMBOL[urteil]} {urteil}</b> · {html.escape(text)}</div>",
        unsafe_allow_html=True,
    )


def karte(spalte, titel: str, wert: str, unter: str) -> None:
    spalte.markdown(
        f"<div style='border:1px solid #d0d7de;border-radius:10px;padding:10px 14px;margin-top:10px'>"
        f"<div style='font-size:.8rem;color:#57606a'>{html.escape(titel)}</div>"
        f"<div style='font-size:1.05rem;font-weight:600;line-height:1.35'>{html.escape(str(wert))}</div>"
        f"<div style='font-size:.8rem;color:#57606a'>{html.escape(unter)}</div></div>",
        unsafe_allow_html=True,
    )


@st.cache_data
def offizielle_validierung() -> dict:
    pfad = PROJEKT / "docs" / "validierung.json"
    return json.loads(pfad.read_text("utf-8")) if pfad.exists() else {}


def _z(x, stellen: int = 2) -> str:
    """Deutsches Zahlenformat für Tabellen; leere Zelle statt 'None'."""
    if x is None:
        return ""
    x = Decimal(str(x))
    return fmt_de(x, 0) if x == x.to_integral_value() and stellen == 0 else fmt_de(x, stellen)


def pruef_df(v: Vorgang) -> pd.DataFrame:
    return pd.DataFrame([{
        "": STATUS_SYMBOL[p.status.value], "Prüfcode": p.code.value, "Status": p.status.value, "Meldung": p.meldung,
        "Referenz": p.referenz or "", "Soll": _z(p.soll), "Ist": _z(p.ist),
        "Abweichung": _z(p.abweichung), "Abw. %": _z(p.abweichung_prozent),
    } for p in sorted(v.fakten.pruefungen, key=lambda p: {"FEHLER": 0, "WARNUNG": 1, "OK": 2}[p.status.value])])


# ═════════════════════════════════════════════════════════════════════════════
# Seitenleiste
# ═════════════════════════════════════════════════════════════════════════════

if "datenversion" not in st.session_state:
    st.session_state.datenversion = 0
if "entscheidungen" not in st.session_state:
    st.session_state.entscheidungen = {}
if "sql_protokoll" not in st.session_state:
    st.session_state.sql_protokoll = []

with st.sidebar:
    st.markdown("## 🧾 FlowAudit-AI")
    st.caption("KI-gestützter E-Rechnungs-, 3-Way-Matching- & BPMN-Audit-Agent")
    provider_namen = verfuegbare_provider()
    modus = st.selectbox("KI-Modus", list(provider_namen), help="Echte LLMs erscheinen, sobald ein API-Key in der .env steht.")
    if len(provider_namen) == 1:
        st.caption("Kein API-Key gefunden – der Prüfvermerk kommt aus dem Regelwerk. Alle Prüfungen laufen vollständig.")
    toleranz = st.slider("Preistoleranz (±%)", 0.5, 10.0, 2.0, 0.5)
    DATENORDNER = str(daten_ordner())
    demo = demodaten(st.session_state.datenversion, DATENORDNER)
    st.caption(f"Stichtag der Demo-Daten: **{fmt_datum(demo.stichtag)}** · {len(demo.manifest)} Musterdokumente")
    if st.button("🔄 Demo-Daten neu erzeugen", width="stretch"):
        st.session_state.datenversion += 1
        st.rerun()
    st.markdown("#### 📥 Export")
    xlsx_bytes, pbi_bytes = exportpaket(st.session_state.datenversion, DATENORDNER, str(toleranz))
    st.download_button("📊 Excel-Prüfbericht (.xlsx)", xlsx_bytes, width="stretch",
                       file_name=f"flowaudit_pruefbericht_{demo.stichtag:%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="Rechnungsübersicht · 3-Way-Abweichungen · Skonto-Risikoliste · IKS-Prüfprotokoll "
                            "mit SHA-256-Hash-Kette · KPIs mit Excel-Gegenrechnung")
    st.download_button("🟨 Power-BI-Paket (.zip)", pbi_bytes, width="stretch",
                       file_name=f"flowaudit_powerbi_{demo.stichtag:%Y%m%d}.zip", mime="application/zip",
                       help="Sternschema als CSV (Semikolon, Dezimalkomma) inkl. Kalendertabelle, DAX-Measures "
                            "und Power-Query-Skript")
    st.info("Alle Unternehmen, Rechnungen und Bankdaten sind **synthetisch**.", icon="ℹ️")

einstellungen = einstellungen_fuer(provider_namen[modus], toleranz)
assistent = KIAssistent(demo.db_pfad, einstellungen, stichtag=demo.stichtag)

TAB_LIVE, TAB_KPI, TAB_SECURITY = "🔍 Live-Rechnungsprüfung", "📊 Controlling & KPIs", "🛡️ Security & Guardrails"
DEMO_PFAD = [  # (Präfix der Musterdatei, Button, Kurzbeschreibung, Ziel-Tab)
    ("03_", "① Preisabweichung", "3-Way-Matching gegen EKPO/MATDOC, Notiz an den Einkauf, Skonto gefährdet", TAB_LIVE),
    ("11_", "② Prompt-Injection", "Angriff im Freitext BT-22 – erkannt, isoliert, wirkungslos", TAB_SECURITY),
    ("13_", "③ ZUGFeRD-PDF", "XML aus PDF/A-3 extrahiert, alles korrekt → Touchless-Freigabe", TAB_LIVE),
]


def starte_demo(praefix: str, ziel_tab: str) -> None:
    """Callback der Demo-Buttons: Musterdokument wählen und in den passenden Tab springen."""
    datei = next((n for n in demo.manifest if n.startswith(praefix)), None)
    if datei:
        st.session_state.eingang = "Musterrechnung"
        st.session_state.musterdokument = datei
    st.session_state.haupttab = ziel_tab


st.title("FlowAudit-AI – Prüf- & Freigabeplattform für Purchase-to-Pay")
st.markdown(
    "<div style='font-size:1.05rem;color:#57606a;margin-top:-.6rem;margin-bottom:.8rem'>"
    "Ein nachvollziehbarer Workflow von der E-Rechnung bis zur Freigabe: Prüfung nach § 14 UStG / EN 16931 · "
    "3-Way-Matching gegen SAP-Tabellen · KI nur hinter deterministischen Guardrails – der Mensch gibt frei</div>",
    unsafe_allow_html=True,
)

# Kennzahlen für die Aufbau-Tabelle werden live berechnet (keine fest eingetragenen Zahlen).
_kpis_kopf, _, _, _, _ = batch(st.session_state.datenversion, DATENORDNER, str(toleranz))
_n_xml = sum(1 for e in demo.manifest.values() if e["container"] == "XML")
_n_pdf = len(demo.manifest) - _n_xml
with st.expander("🧭 So ist die App aufgebaut", expanded=True):
    st.markdown(f"""
| Tab (Modul) | Datenquelle | Leitfrage | Für wen |
|---|---|---|---|
| **{TAB_LIVE}** | {_n_xml} XML-Rechnungen (XRechnung, ZUGFeRD) und {_n_pdf} PDF-Dokumente · SAP-Tabellen EKKO, EKPO, MATDOC, RSEG, RBKP | „Ist die Eingangsrechnung formal gültig, mit Bestellung und Wareneingang abgeglichen und bereit zur Zahlung?“ | Kreditorenbuchhaltung, Einkauf, Fachbereich |
| **{TAB_KPI}** | Prüfprotokolle und Workflow-Historie ({_kpis_kopf['anzahl_rechnungen']} Vorgänge) | „Wie hoch ist unsere Dunkelverarbeitungsquote, wo entstehen Durchlaufzeit-Verluste und wo droht Skontoverlust?“ | Operatives Controlling, Prozessmanagement |
| **{TAB_SECURITY}** | Freitextfelder (BT-22), SQL-Schnittstelle, XML-/PDF-Parser | „Wie schützen wir den Prüfprozess vor Prompt-Injection, Datenabfluss (IBAN) und manipulierten Dateien?“ | Interne Revision, IT-Security, AI-Governance |
""")
    st.caption(f"Stand: {len(demo.manifest)} Musterdokumente · Stichtag {fmt_datum(demo.stichtag)} · "
               "alle Daten synthetisch")

if st.session_state.get("musterdokument") not in demo.manifest:  # Start: Preisabweichung (Fall 03)
    st.session_state.musterdokument = next((n for n in demo.manifest if n.startswith("03_")), next(iter(demo.manifest)))

st.markdown("**⚡ Demo in 60 Sekunden** – drei Fälle, die das Wesentliche zeigen:")
_spalten = st.columns(len(DEMO_PFAD))
_aktiv = st.session_state.get("musterdokument", "")
for _spalte, (_praefix, _label, _text, _ziel) in zip(_spalten, DEMO_PFAD):
    _spalte.button(_label, key=f"demo_{_praefix}", width="stretch", on_click=starte_demo, args=(_praefix, _ziel),
                   type="primary" if _aktiv.startswith(_praefix) else "secondary")
    _spalte.caption(_text)

tab1, tab2, tab3 = st.tabs([TAB_LIVE, TAB_KPI, TAB_SECURITY], key="haupttab", on_change="rerun")


def leitfrage(frage: str, modul: str) -> None:
    st.info(f"🎯 **Leitfrage:** {frage}  \n**Was das Modul tut:** {modul}")


# ═════════════════════════════════════════════════════════════════════════════
# Tab 1 – Live-Rechnungsprüfung
# ═════════════════════════════════════════════════════════════════════════════

with tab1:
    leitfrage("„Warum wurde eine Rechnung freigegeben, zur Klärung geroutet oder gesperrt – und welche SAP-Belege "
              "belegen das?“",
              "Prüft E-Rechnungen nach § 14 UStG / EN 16931, gleicht sie im 3-Way-Matching gegen Bestellung und "
              "Wareneingang ab und erstellt Aktionsentwürfe, die ein Mensch freigibt (Human-in-the-Loop).")
    namen = list(demo.manifest)
    quelle = st.radio("Eingang", ["Musterrechnung", "Eigene Datei (XML oder ZUGFeRD-PDF)"], horizontal=True,
                      key="eingang")
    vorgang: Vorgang | None = None
    if quelle == "Musterrechnung":
        auswahl = st.selectbox(
            "Musterdokument", namen, key="musterdokument",
            format_func=lambda n: f"{n[:2]} · {demo.manifest[n]['titel']}  ({demo.manifest[n]['container']}, {demo.manifest[n]['syntax']})",
        )
        vorgang = assistent.pruefe(demo.ordner / auswahl)
    else:
        upload = st.file_uploader("XRechnung (UBL/CII), ZUGFeRD-XML oder ZUGFeRD-PDF", type=["xml", "pdf"])
        if upload is not None:
            vorgang = assistent.pruefe(upload.getvalue(), upload.name)
        else:
            st.info("Datei hochladen – sie wird nur im Arbeitsspeicher geprüft und nicht gespeichert.")

    if vorgang is not None and vorgang.abgelehnt:
        badge("ABGELEHNT", vorgang.ablehnungstyp or "")
        st.error(vorgang.ablehnungsgrund)
    elif vorgang is not None:
        r, f = vorgang.rechnung, vorgang.fakten
        badge(vorgang.urteil.value, SCHRITT_TEXT[vorgang.schritt.value])
        k1, k2, k3, k4 = st.columns(4)
        karte(k1, "Rechnung", f.rechnung.rechnungsnummer,
              f"vom {fmt_datum(f.rechnung.rechnungsdatum)}" if f.rechnung.rechnungsdatum else "ohne Datum")
        karte(k2, "Lieferant", f.rechnung.lieferant_name, f"LIFNR {f.rechnung.lieferant_id}")
        karte(k3, "Betrag brutto", f"{fmt_de(f.rechnung.summe_brutto or Decimal(0))} €",
              f"netto {fmt_de(f.rechnung.summe_netto or Decimal(0))} €")
        karte(k4, "Format", r.container or r.format, f"Bestellung {f.rechnung.bestellnummer or '–'}")

        links, rechts = st.columns([1, 1])
        with links:
            st.subheader("Begründung")
            for satz in vorgang.begruendung:
                st.markdown(f"- {satz}")
            st.caption("BPMN-Pfad: " + " → ".join(s.value for s in vorgang.pfad))
        with rechts:
            st.subheader("KI-Prüfvermerk")
            a = vorgang.audit
            st.markdown(
                f"Quelle **{'LLM · ' + a.modell if a.quelle == 'LLM' else 'Regelwerk (Offline)'}** · "
                f"Faktencheck {'✅ bestanden' if a.faktencheck.bestanden else '❌ nicht bestanden'} "
                f"({a.faktencheck.geprueft} Zahlen/Daten geprüft) · SHA-256 `{a.eingabe_hash[:16]}…`"
            )
            st.write(a.bericht.audit_vermerk)
            st.markdown(f"**Nächster Schritt:** {a.bericht.naechster_schritt}")
            with st.expander("Revisionsprotokoll"):
                st.json(a.als_revisionsprotokoll())

        st.subheader("Prüfprotokoll (§ 14 UStG · EN 16931 · 3-Way-Matching)")
        st.dataframe(pruef_df(vorgang), hide_index=True, width="stretch")
        offiziell = offizielle_validierung().get(vorgang.datei)
        with st.expander("Offizielle Regelwerke (CEN EN 16931 / KoSIT XRechnung)"):
            if offiziell:
                def liste(x):
                    return "✅ keine Verstöße" if not x else ", ".join(f"{'⚠️' if fl == 'warning' else '❌'} {rid}" for rid, fl in x)
                st.markdown(f"- **EN 16931 (CEN):** {liste(offiziell['en16931'])}\n- **XRechnung (KoSIT):** "
                            f"{liste(offiziell['xrechnung'])}\n- **CII-XSD:** {offiziell['cii_xsd']}")
                st.caption("Referenzprüfung mit tools/offizielle_validierung.py – Details in docs/validierung.md.")
            else:
                st.caption("Für eigene Dateien lokal ausführen: `python tools/offizielle_validierung.py <ordner> --vendor vendor`.")

        st.subheader("SAP-Tabellenabgleich")
        sap = vorgang.sap
        if sap and sap.positionen:
            st.dataframe(pd.DataFrame([{
                "EBELP": p.ebelp, "MATNR": p.matnr, "Text (EKPO)": p.text, "Bestellt": _z(p.bestellmenge, 0),
                "WE (MATDOC)": _z(p.we_menge, 0), "berechnet (RSEG)": _z(p.bereits_berechnet, 0),
                "Rechnung": _z(p.rechnungsmenge, 0), "Preis EKPO (€)": _z(p.bestellpreis),
                "Preis Rechnung (€)": _z(p.rechnungspreis), "Status": p.status,
            } for p in sap.positionen]), hide_index=True, width="stretch")
            st.caption(" · ".join(sap.belegbezuege))
            with st.expander(f"{len(sap.abfragen)} SQL-Abfragen über den Read-Only-Guardrail"):
                for q in sap.abfragen:
                    st.markdown(f"**{q['abfrage']}** – {'✅ alle 4 Schichten passiert' if q['erlaubt'] else '⛔ blockiert'} · Hash `{q['sql_hash']}`")
                    st.code(q["sql"], language="sql")
        else:
            st.caption("Kein Bestellbezug – es gibt keine EKKO/EKPO/MATDOC-Belege zum Abgleich.")

        st.subheader("Prozessverfolgung")
        d1, d2 = st.tabs(["BPMN 2.0", "eEPK"])
        with d1:
            mermaid(vorgang.diagramm("bpmn"), 480)
        with d2:
            mit_org = st.toggle("Organisationseinheiten & Informationsobjekte anzeigen", value=False)
            mermaid(vorgang.diagramm("epk", mit_organisation=mit_org), 1100)
        with st.expander("Mermaid-Code"):
            st.code(vorgang.diagramm("bpmn"), language="text")

    if vorgang is not None:
        st.subheader("Aktionsentwürfe · Human-in-the-Loop")
        st.caption("Nichts wird automatisch versendet oder gebucht. Jeder Entwurf wird von einer namentlich "
                   "erfassten Person freigegeben oder verworfen und protokolliert.")
        for akt in vorgang.aktionen:
            schluessel = f"{vorgang.datei}:{akt.typ}:{akt.betreff}"
            entschieden = st.session_state.entscheidungen.get(schluessel)
            kopf = f"{'🔴' if akt.prioritaet == 'HOCH' else '🔵'} {akt.typ} · {akt.betreff}"
            if entschieden:
                kopf += f"  —  {entschieden.status} durch {entschieden.bearbeitet_von}"
            with st.expander(kopf, expanded=entschieden is None):
                st.markdown(f"**An:** {akt.empfaenger} · **Kanal:** {akt.kanal} · **Priorität:** {akt.prioritaet}")
                text = st.text_area("Entwurf (bearbeitbar)", akt.text, height=260, key=f"text:{schluessel}",
                                    disabled=entschieden is not None)
                if akt.felder:
                    st.markdown("**Beanstandete Felder:**\n" + "\n".join(f"- {x}" for x in akt.felder))
                if akt.belegbezuege:
                    st.markdown("**Belegbezüge:** " + " · ".join(akt.belegbezuege))
                if entschieden is None:
                    c1, c2, c3 = st.columns([2, 1, 1])
                    name = c1.text_input("Ihr Name (Freigabe durch eine Person · Human-in-the-Loop)",
                                         key=f"name:{schluessel}")
                    if c2.button("✅ Freigeben", key=f"ok:{schluessel}", disabled=not name.strip()):
                        neu = akt.model_copy(update={"text": text}).freigeben(name, "Freigabe im Cockpit")
                        st.session_state.entscheidungen[schluessel] = neu
                        protokolliere_freigabe(neu, vorgang.datei)
                        st.rerun()
                    if c3.button("🗑️ Verwerfen", key=f"nein:{schluessel}", disabled=not name.strip()):
                        neu = akt.verwerfen(name, "Im Cockpit verworfen")
                        st.session_state.entscheidungen[schluessel] = neu
                        protokolliere_freigabe(neu, vorgang.datei)
                        st.rerun()
                else:
                    st.success(f"{entschieden.status} am {entschieden.bearbeitet_am:%d.%m.%Y %H:%M} UTC – protokolliert in data/aktionsprotokoll.jsonl")
                    st.download_button("📄 Als Textdatei herunterladen", entschieden.als_text(),
                                       file_name=f"{akt.typ.lower()}_{vorgang.datei.rsplit('.', 1)[0]}.txt",
                                       key=f"dl:{schluessel}")


# ═════════════════════════════════════════════════════════════════════════════
# Tab 2 – Controlling & KPIs
# ═════════════════════════════════════════════════════════════════════════════

with tab2:
    leitfrage("„Wie effizient läuft unser Purchase-to-Pay-Prozess und wo verlieren wir Skonto oder Zeit durch "
              "manuelle Klärungen?“",
              "Misst Touchless-Quote, Durchlaufzeiten, Maverick Buying sowie verhinderte Dubletten und exportiert "
              "einen Excel-Prüfbericht und ein Sternschema für Power BI (Seitenleiste).")
    st.warning("**Synthetische Demo-Daten:** 50 historische Rechnungen + alle Musterdokumente, gebucht in einer "
               "Kopie der Datenbank. Die Kennzahlen zeigen die Methodik, keine realen Unternehmenswerte.", icon="🧪")
    kpis, bericht_md, faelle, statistik, db_kopie = batch(st.session_state.datenversion, DATENORDNER, str(toleranz))
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Dunkelverarbeitungsquote", f"{fmt_de(Decimal(kpis['dunkelverarbeitungsquote_prozent']))} %")
    m2.metric("Ø Durchlaufzeit Touchless", f"{fmt_de(Decimal(kpis['durchlaufzeit_touchless_avg_minuten'] or 0))} min")
    m3.metric("Ø Durchlaufzeit mit Klärung", f"{fmt_de(Decimal(kpis['durchlaufzeit_manuell_avg_stunden'] or 0))} h")
    m4.metric("Maverick-Buying-Quote", f"{fmt_de(Decimal(kpis['maverick_quote_prozent']))} %")
    m5.metric("Skonto gefährdet", f"{fmt_de(Decimal(kpis['skonto_gefaehrdet_eur']))} €")
    st.caption(f"{kpis['anzahl_touchless']} von {kpis['anzahl_rechnungen']} Rechnungen ohne manuellen Eingriff · "
               f"Median-Durchlaufzeit {fmt_de(Decimal(kpis['durchlaufzeit_median_stunden'] or 0))} h · "
               f"{kpis['anzahl_offen']} offene Vorgänge ({fmt_de(Decimal(kpis['volumen_offen_eur']))} €) · Skonto gesichert "
               f"{fmt_de(Decimal(kpis['skonto_gesichert_eur']))} €, verloren {fmt_de(Decimal(kpis['skonto_verloren_eur']))} €")

    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Verteilung der Sperr- und Klärungsgründe**")
        df = pd.DataFrame([{"Grund": SCHRITT_TEXT.get(k, k), "Anzahl": v} for k, v in kpis["sperrgruende"].items()])
        st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusEnd=4, color="#bf8700").encode(
            x="Anzahl:Q", y=alt.Y("Grund:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
            tooltip=["Grund", "Anzahl"]).properties(height=260),
            width="stretch")
    with g2:
        st.markdown("**Gesamturteil aller Rechnungen**")
        sperren = sum(v for k, v in kpis["sperrgruende"].items() if k.startswith("SPERRE"))
        urteile = pd.DataFrame([
            {"Urteil": "FREIGABE (touchless)", "Anzahl": kpis["anzahl_touchless"]},
            {"Urteil": "KLAERUNG", "Anzahl": sum(kpis["sperrgruende"].values()) - sperren},
            {"Urteil": "SPERRE", "Anzahl": sperren},
        ])
        st.altair_chart(alt.Chart(urteile).mark_arc(innerRadius=60).encode(
            theta="Anzahl:Q",
            color=alt.Color("Urteil:N", scale=alt.Scale(domain=list(urteile["Urteil"]),
                                                         range=["#1a7f37", "#bf8700", "#cf222e"])),
            tooltip=["Urteil", "Anzahl"]).properties(height=260), width="stretch")

    st.markdown("**Compliance- & Abwehrstatistik dieses Laufs**")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Prompt-Injections erkannt / wirksam", f"{statistik['injektionen_erkannt']} / {statistik['injektionen_wirksam']}")
    s2.metric("Dateien abgewiesen", statistik["abgewiesen"])
    s3.metric("Dubletten verhindert", f"{fmt_de(Decimal(statistik['dubletten_verhindert_eur']))} €")
    s4.metric("Routing wie erwartet", f"{statistik['erwartung_erfuellt']}/{statistik['dokumente']}")

    st.markdown("**Alle Testfälle**")
    df_faelle = pd.DataFrame(faelle)
    df_faelle["✓"] = ["✅" if a == b else "❌" for a, b in zip(df_faelle["Ergebnis"], df_faelle["Erwartet"])]
    df_faelle = df_faelle[["Datei", "Rechnung", "Ergebnis", "Erwartet", "✓", "Brutto (EUR)", "Lieferant"]]
    st.dataframe(df_faelle, hide_index=True, width="stretch")
    with st.expander("KPI-Bericht (Markdown)"):
        st.markdown(bericht_md)

    st.caption("📥 Excel-Prüfbericht und Power-BI-Paket dieses Laufs: Download-Buttons in der Seitenleiste "
               "(oder `python run_agent.py export`).")


# ═════════════════════════════════════════════════════════════════════════════
# Tab 3 – Security & Guardrails
# ═════════════════════════════════════════════════════════════════════════════

with tab3:
    leitfrage("„Warum darf ein Sprachmodell niemals direkt über Zahlungsfreigaben entscheiden oder ungefiltert auf "
              "ERP-Datenbanken zugreifen?“",
              "Demonstriert live die Abwehr von Prompt-Injections in Rechnungsfreitexten, den 4-stufigen "
              "Read-Only-SQL-Guardrail und den Schutz vor XXE- und PDF-Angriffen.")
    st.subheader("① Prompt-Injection in Rechnung 11 (BT-22)")
    fall11 = next((n for n in demo.manifest if n.startswith("11_")), None)
    if fall11:
        v11 = assistent.pruefe(demo.ordner / fall11)
        roh = lese_rechnung(demo.ordner / fall11)
        prompt = baue_audit_prompt(v11.fakten)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("##### Ebene 1 · Erkennung")
            st.code("\n".join(roh.bemerkungen), language="text", wrap_lines=True)
            warnung = [p for p in v11.fakten.pruefungen if p.code.value == "VERDAECHTIGER_FREITEXT"][0]
            st.success(f"Erkannt: {warnung.code.value} ({warnung.status.value}, {warnung.referenz})")
        with c2:
            st.markdown("##### Ebene 2 · Datenminimierung")
            pruefungen = {
                "Injection-Text im LLM-Prompt": any(t in prompt for t in ("Ignoriere", "Vorstand", "SYSTEM:")),
                "IBAN (LFBK) im LLM-Prompt": bool(roh.iban and roh.iban in prompt),
                "Roh-Freitext BT-22 im Prompt": any(b[:40] in prompt for b in roh.bemerkungen),
            }
            for text, enthalten in pruefungen.items():
                st.markdown(f"{'❌ enthalten' if enthalten else '✅ nicht enthalten'} – {text}")
            with st.expander("Exakter Prompt an das LLM"):
                st.code(prompt, language="json")
        with c3:
            st.markdown("##### Ebene 3 · Determinismus")
            if st.button("🧨 Kompromittiertes LLM simulieren"):
                llm = MockLLMClient([kompromittierte_antwort(v11.fakten)])
                ergebnis = FlowAuditAgent(llm, einstellungen_fuer("mock", toleranz)).pruefe_rechnung(v11.fakten)
                st.error("LLM wollte: TOUCHLESS_FREIGABE („sofort zahlen“)")
                abgelehnt = [w for w in ergebnis.warnungen if "abgelehnt" in w]
                st.caption(f"{len(abgelehnt)} LLM-Antwort(en) abgelehnt – Grund: "
                           "„workflow_schritt TOUCHLESS_FREIGABE widerspricht dem deterministischen Routing“. "
                           "Danach greift der regelbasierte Vermerk.")
                badge(ergebnis.bericht.gesamturteil.value, f"Endgültige Route: {ergebnis.bericht.workflow_schritt.value}")
            else:
                st.caption("Simuliert ein LLM, das der Injection folgt und die Rechnung freigeben will.")

    st.divider()
    st.subheader("② Read-Only-SQL-Guardrail (4 Schichten)")
    l1, l2, l3, l4 = st.columns(4)
    l1.info("**1 · Lexik**\nnur SELECT/WITH, keine Kommentare, keine Mehrfachanweisungen")
    l2.info("**2 · Schema-Whitelist**\nSAP-Tabellen, gesperrte Spalten LFBK.IBAN/SWIFT, SQLite-Authorizer")
    l3.info("**3 · Datenbank**\nmode=ro + PRAGMA query_only")
    l4.info("**4 · Ressourcen**\nZeilen- und Zeitlimit")
    vorlage = st.selectbox("Angriff / Abfrage", list(ANGRIFFS_SQL))
    sql = st.text_area("SQL", ANGRIFFS_SQL[vorlage], height=90)
    if st.button("▶️ Abfrage durch den Guardrail schicken"):
        guard = ReadOnlySQLGuard(demo.db_pfad, timeout_sekunden=0.5)
        try:
            zeilen, gekuerzt = guard.ausfuehren(sql)
            st.success(f"ERLAUBT – alle vier Schichten passiert · {len(zeilen)} Zeile(n)")
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
            st.session_state.sql_protokoll.append({"Abfrage": sql[:80], "Ergebnis": "erlaubt", "Schicht": "–"})
        except SQLGuardrailVerletzung as v:
            st.error(f"BLOCKIERT in Schicht {v.schicht}: {v.grund}")
            st.session_state.sql_protokoll.append({"Abfrage": sql[:80], "Ergebnis": "blockiert", "Schicht": v.schicht})
    if st.session_state.sql_protokoll:
        st.dataframe(pd.DataFrame(st.session_state.sql_protokoll), hide_index=True, width="stretch")

    st.divider()
    st.subheader("③ Angriffe über die Datei selbst (XML/PDF)")
    angriff = st.selectbox("Angriffsdatei", list(ANGRIFFS_DATEIEN))
    with st.expander("Inhalt der Angriffsdatei"):
        st.code(ANGRIFFS_DATEIEN[angriff].decode("latin-1"), language="xml")
    if st.button("▶️ Datei einlesen lassen"):
        try:
            lese_rechnung(ANGRIFFS_DATEIEN[angriff])
            st.error("Datei wurde akzeptiert – das darf nicht passieren!")
        except EInvoiceFehler as exc:
            st.success(f"Abgewiesen vor jeder Verarbeitung · {type(exc).__name__}: {exc}")

st.caption("FlowAudit-AI · Portfolio-Projekt von Sherwan Shweish · synthetische Daten · keine Steuer- oder Rechtsberatung")
