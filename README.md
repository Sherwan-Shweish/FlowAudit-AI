<div align="center">

# 🧾 FlowAudit-AI – Prüf- & Freigabeplattform für Purchase-to-Pay

**Ein nachvollziehbarer Workflow von der E-Rechnung bis zur Freigabe:**
**Prüfung nach § 14 UStG / EN 16931 · 3-Way-Matching gegen SAP-Tabellen · KI nur hinter deterministischen Guardrails – der Mensch gibt frei**

*XRechnung · ZUGFeRD/Factur-X (PDF/A-3) · EN 16931 · § 14 UStG · SAP S/4HANA MM/FI · BPMN 2.0 · eEPK · IKS · Excel · Power BI*

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Cockpit-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Tests](https://img.shields.io/badge/pytest-281%20gr%C3%BCn-2EA44F?logo=pytest&logoColor=white)
![EN 16931](https://img.shields.io/badge/EN%2016931-offiziell%20validiert-0A66C2)
![Power BI](https://img.shields.io/badge/Export-Excel%20%7C%20Power%20BI-F2C811?logo=powerbi&logoColor=black)
![LLM](https://img.shields.io/badge/LLM-Claude%20%7C%20Gemini%20%7C%20Offline-6E56CF)
![Lizenz](https://img.shields.io/badge/Lizenz-MIT-lightgrey)

<img src="docs/screenshots/cockpit_einstieg.png" alt="FlowAudit-AI – Einstieg mit Aufbau-Übersicht und Demo in 60 Sekunden" width="88%">

</div>

---

## Worum geht es?

Eine Eingangsrechnung ist erst dann zahlungsreif, wenn sie **formal korrekt** ist (§ 14 UStG, EN 16931), **zur Bestellung passt** und **die Ware tatsächlich angekommen ist**. In vielen Kreditorenbuchhaltungen wird genau das noch manuell abgeglichen – langsam, fehleranfällig und schwer prüfbar.

**FlowAudit-AI** automatisiert diesen Ablauf von der Datei bis zur Freigabe:

1. E-Rechnungen (XRechnung, ZUGFeRD-XML **und ZUGFeRD-PDF/A-3**) werden sicher eingelesen und nach § 14 UStG / EN 16931 geprüft,
2. per **3-Way-Matching** gegen Bestellung (`EKKO`/`EKPO`) und Wareneingang (`MATDOC`) abgeglichen,
3. deterministisch durch einen **BPMN-2.0-Prozess** geroutet,
4. von einem **KI-Assistenten** erklärt – mit Aktionsentwürfen (E-Mail an Lieferant, Notiz an Einkauf/Logistik/Fachbereich), die ein **Mensch freigibt**,
5. im **Cockpit** und als **Excel-Prüfbericht / Power-BI-Paket** für Controlling und Revision ausgewertet.

> **Leitgedanke:** *Determinismus vor KI.* Zahlen, Prüfregeln und Routing kommen aus Python/SQL. Das Sprachmodell formuliert nur – jede Zahl, die es schreibt, wird gegen die Quelldaten geprüft, und nichts wird ohne menschliche Freigabe versendet.

---

## 🚀 Schnellstart

```bash
git clone https://github.com/<dein-github-name>/FlowAudit-AI.git
cd FlowAudit-AI

# 1 · Umgebung anlegen und aktivieren
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2 · Tests starten (281 Tests, offline, ohne API-Kosten)
python -m pytest

# 3a · Cockpit starten → http://localhost:8501
streamlit run app.py

# 3b · oder Kommandozeile
python run_agent.py alle
python run_agent.py pruefe data/invoices/11_prompt_injection_ubl.xml
python run_agent.py export
```

Ohne `.env` läuft alles im **Offline-Modus** (regelbasierter Prüfvermerk). Für Claude oder Gemini: `cp .env.example .env` und `LLM_PROVIDER` sowie den API-Key eintragen – das Cockpit bietet das Modell dann in der Seitenleiste an.

---

## 💼 Betriebswirtschaftlicher Nutzen

| Herausforderung | Antwort von FlowAudit-AI | Messgröße |
|---|---|---|
| **E-Rechnungspflicht** (Wachstumschancengesetz): Empfang im B2B seit 01.01.2025 Pflicht, Ausstellungspflicht gestaffelt ab 2027/2028 | XRechnung (UBL/CII), ZUGFeRD-XML und ZUGFeRD-PDF/A-3; Pflichtangaben nach § 14 Abs. 4 UStG, Rechen- und Steuersatzprüfung; reines Bild-PDF wird mit Begründung abgewiesen | Quote formal korrekter E-Rechnungen |
| **Hohe Prozesskosten** | 3-Way-Matching je Position mit Preistoleranz (±2 %, im Cockpit einstellbar) | **Dunkelverarbeitungsquote** |
| **Doppelzahlungen** | Duplicate Invoice Check: gleiche Nummer **oder** gleicher Lieferant + Datum + Betrag | verhinderte Fehlzahlungen in € |
| **Zahlung ohne Gegenleistung** | Sperre bei fehlendem oder unvollständigem Wareneingang | gesperrtes Volumen |
| **Maverick Buying** | Rechnungen ohne/mit fremder Bestellung → Fachbereich | Maverick-Buying-Quote |
| **Skontoverluste** | Skonto aus XRechnung-Konvention, CII oder Freitext; Risikoliste nach Resttagen | gesichert / gefährdet / verloren in € |
| **IKS & Revision** | SHA-256 von Eingangsdatei und Prüfstand, Prüfprotokoll mit **Hash-Kette**, Freigabeprotokoll (Human-in-the-Loop) | lückenlose, manipulationssichere Prüfspur |

---

## 📊 Projektstatus – abgeschlossen (v1.0)

| Modul | Datei | Status |
|---|---|---|
| KI-Audit-Agent: System-Prompt, Pydantic-Schemata, BPMN-Routing, Zahlen-Faktenprüfung, 4-Schichten-SQL-Guardrail, Claude/Gemini/Offline | `src/agent.py` | ✅ |
| E-Rechnungs-Parser & § 14-UStG-Validator: UBL 2.1 + CII, XXE-Schutz, Rechenprüfung, Skonto, Prompt-Injection-Erkennung | `src/einvoice_parser.py` | ✅ |
| **ZUGFeRD/Factur-X-PDF:** XML aus PDF/A-3 extrahieren (`factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml`), PDF-Sicherheitsprüfung, Erzeugung von Muster-PDFs | `src/zugferd_pdf.py` | ✅ |
| 3-Way-Matching im SAP-Stil: Dubletten, Maverick Buying, WE, Menge, Preis, Skonto; Buchung in RBKP/RSEG/Prüfprotokoll | `src/matching_engine.py` | ✅ |
| BPMN-2.0-/eEPK-Prozesstracker (Mermaid, Haltepunkt farbig) & KPI-Engine | `src/bpmn_tracker.py` | ✅ |
| **KI-Assistent & Aktions-Generator** (Human-in-the-Loop): Begründung, SAP-Tabellenabgleich über den SQL-Guardrail, Entwürfe je Route | `src/assistent.py` | ✅ |
| **Export:** Excel-Prüfbericht (5 Blätter, Formeln, Hash-Kette) & Power-BI-Paket (Sternschema-ZIP) | `src/exporter.py` | ✅ |
| **Cockpit** (Streamlit): Aufbau-Übersicht, „Demo in 60 Sekunden“, 3 Tabs mit Leitfragen, Export-Buttons · **CLI** | `app.py`, `run_agent.py` | ✅ |
| Synthetischer Datengenerator: SAP-Datenbank + 15 Musterdokumente (12 XML, 2 ZUGFeRD-PDF, 1 Bild-PDF) | `src/seed_data.py` | ✅ |
| Validierung der Muster gegen offizielle EN-16931-/XRechnung-Regeln | `tools/offizielle_validierung.py` | ✅ |
| pytest: Agent 83 · Parser 46 · Matching 39 · Assistent 28 · ZUGFeRD-PDF 20 · CLI/Cockpit 20 · Tracker 16 · Integration 16 · Export 13 | `tests/` | ✅ 281 grün |

---

## 🏗️ Architektur

```mermaid
flowchart LR
    subgraph EIN["① Eingang"]
        X["XRechnung<br/>UBL 2.1 / CII"]
        Z["ZUGFeRD-XML"]
        P["ZUGFeRD-PDF/A-3<br/>factur-x.xml"]
    end

    subgraph DET["② Deterministische Prüfung"]
        ZP["zugferd_pdf<br/>XML-Extraktion"]
        V["einvoice_parser<br/>XXE-Schutz · § 14 UStG"]
        M["matching_engine<br/>3-Way-Matching"]
        DB[("SQLite im SAP-Stil<br/>LFA1 · EKKO/EKPO<br/>MATDOC · RBKP/RSEG")]
        R["BPMN-Routing<br/>(deterministisch)"]
    end

    subgraph KI["③ KI-Schicht mit Guardrails"]
        AG["agent<br/>Prüfvermerk · Faktencheck"]
        AS["assistent<br/>Begründung · Aktionsentwürfe"]
        SQLG["Read-Only-SQL-Guardrail"]
    end

    subgraph AUS["④ Ausgabe"]
        HITL["Human-in-the-Loop<br/>Freigabe / Verwerfen"]
        APP["Cockpit · CLI"]
        EXP["Excel-Prüfbericht<br/>Power-BI-Paket"]
    end

    P --> ZP --> V
    X & Z --> V --> M
    DB --> M --> R --> AG --> AS --> HITL --> APP
    AS -- "SAP-Abgleich" --> SQLG --> DB
    M -- "Buchung + Prüfprotokoll" --> DB --> EXP --> APP
```

---

## 🧪 Die 15 Prüffälle

`python -m src.seed_data` erzeugt die fiktive **RheinMain Industrietechnik GmbH** (Wiesbaden) mit sechs Lieferanten, Bestellungen, Wareneingängen und 50 bereits gebuchten Rechnungen sowie diese Dokumente in `data/invoices/`:

| # | Fall | Format | Ergebnis (deterministisch) | Aktionsentwurf |
|---|---|---|---|---|
| 01 | Perfekte Rechnung, Skonto | XRechnung UBL | ✅ `TOUCHLESS_FREIGABE` | Buchungsvermerk |
| 02 | Perfekte Rechnung, Skonto 3 % | ZUGFeRD CII | ✅ `TOUCHLESS_FREIGABE` | Buchungsvermerk |
| 03 | Einzelpreis +4,50 % | XRechnung UBL | ✉️ `KLAERUNG_EINKAUF_PREIS` | Notiz Einkauf (EKKO/EKPO/MATDOC, Skonto gefährdet) |
| 04 | Einzelpreis +1,50 % (in Toleranz) | XRechnung CII | ✅ `TOUCHLESS_FREIGABE` | Buchungsvermerk |
| 05 | Kein Wareneingang | XRechnung UBL | ⛔ `SPERRE_FEHLENDER_WARENEINGANG` | Notiz Logistik |
| 06 | Teillieferung 60 von 100 | ZUGFeRD CII | ⛔ `SPERRE_FEHLENDER_WARENEINGANG` | Notiz Logistik |
| 07 | Ohne Bestellung, 19 %/7 % | XRechnung UBL | ⚠️ `COMPLIANCE_MAVERICK_BUYING` | Notiz Fachbereich + Einkauf |
| 08 | Rechnungsnummer bereits gebucht | XRechnung UBL | ⛔ `SPERRE_DUBLETTE` | Vermerk Buchhaltung + E-Mail Lieferant |
| 09 | USt-IdNr./Steuernummer fehlen | XRechnung UBL | ✉️ `KLAERUNG_LIEFERANT_FORMFEHLER` | E-Mail Lieferant (BT-31/BT-32) |
| 10 | Zahlendreher in der USt (273,50 statt 237,50 €) | ZUGFeRD CII | ✉️ `KLAERUNG_LIEFERANT_FORMFEHLER` | E-Mail Lieferant (BT-117, Soll/Ist) |
| 11 | **Prompt-Injection in BT-22** + Preis +8 % | XRechnung UBL | ✉️ `KLAERUNG_EINKAUF_PREIS` – Injection wirkungslos | Notiz Einkauf + Meldung Compliance |
| 12 | Gleicher Lieferant, Tag und Betrag | ZUGFeRD CII | ⛔ `SPERRE_DUBLETTE` (Verdacht) | Vermerk Buchhaltung + E-Mail Lieferant |
| 13 | **ZUGFeRD-PDF/A-3**, alles korrekt | PDF + factur-x.xml | ✅ `TOUCHLESS_FREIGABE` | Buchungsvermerk |
| 14 | **ZUGFeRD-PDF/A-3**, 120 berechnet / 100 bestellt | PDF + factur-x.xml | ✉️ `KLAERUNG_EINKAUF_MENGE` | Notiz Einkauf |
| 15 | Reines Bild-PDF ohne XML | PDF | 🚫 abgewiesen (keine E-Rechnung) | E-Mail: XRechnung/ZUGFeRD anfordern |

**Offiziell validiert** gegen die Schematron-Regeln der **EN 16931 (CEN)** und der **XRechnung (KoSIT)** – inklusive der aus den PDFs extrahierten XML-Dateien. Die Fälle 09/10 scheitern exakt an den beabsichtigten Regeln → [docs/validierung.md](docs/validierung.md).

---

## 🖥️ Cockpit (`streamlit run app.py`)

**Einstieg – die „große Linie“ vor dem Detail:**

1. **🧭 So ist die App aufgebaut** – geöffnete Übersicht: Tab, Datenquelle, Leitfrage und Zielgruppe. Die Mengenangaben (Musterdokumente, Vorgänge) werden **live aus den Daten berechnet**, nicht fest eingetragen.
2. **⚡ Demo in 60 Sekunden** – drei Buttons öffnen die aussagekräftigsten Fälle direkt im passenden Tab:
   ① Preisabweichung (3-Way-Matching, Notiz an den Einkauf) · ② Prompt-Injection (Security-Tab) · ③ ZUGFeRD-PDF (Touchless-Freigabe).
3. **🎯 Leitfrage** am Anfang jedes Tabs – welche Frage das Modul beantwortet und was es dafür tut.

| Tab | Leitfrage | Inhalt |
|---|---|---|
| **🔍 Live-Rechnungsprüfung** | „Warum wurde eine Rechnung freigegeben, zur Klärung geroutet oder gesperrt – und welche SAP-Belege belegen das?“ | Musterdokument wählen oder eigene XML/PDF hochladen → Entscheidung, Begründung, KI-Prüfvermerk mit Faktencheck und SHA-256, Prüfprotokoll, Ergebnis der offiziellen Regelwerke, **SAP-Tabellenabgleich** (EKPO · MATDOC · RSEG inkl. der SQL-Abfragen), **BPMN- und eEPK-Diagramm** genau dieses Falls, **Aktionsentwürfe** mit Freigabe/Verwerfen durch eine namentlich erfasste Person (Human-in-the-Loop, Protokoll in `data/aktionsprotokoll.jsonl`) |
| **📊 Controlling & KPIs** | „Wie effizient läuft unser Purchase-to-Pay-Prozess und wo verlieren wir Skonto oder Zeit durch manuelle Klärungen?“ | Batchlauf aller Dokumente auf einer Datenbankkopie: Dunkelverarbeitungsquote, Durchlaufzeiten, Sperrgründe, Gesamturteil, Skonto, Abwehrstatistik (Injection erkannt/wirksam, abgewiesene Dateien, verhinderte Dubletten) – klar als **synthetische Demo-Daten** gekennzeichnet |
| **🛡️ Security & Guardrails** | „Warum darf ein Sprachmodell niemals direkt über Zahlungsfreigaben entscheiden oder ungefiltert auf ERP-Datenbanken zugreifen?“ | Fall 11 auf drei Ebenen (Erkennung · Datenminimierung mit Blick in den exakten LLM-Prompt · „kompromittiertes LLM“ wird vom deterministischen Routing überstimmt), SQL-Angriffe gegen die 4 Schichten (u. a. `LFBK`/IBAN), XXE/Billion-Laughs/PDF-JavaScript |
| **Seitenleiste** | – | KI-Modus, Preistoleranz, Demo-Daten neu erzeugen, **📥 Excel-Prüfbericht** und **📥 Power-BI-Paket** |

<details>
<summary>Screenshots: Live-Rechnungsprüfung, KPI-Dashboard und Security-Demo</summary>

<img src="docs/screenshots/cockpit_live_pruefung.png" alt="Live-Rechnungsprüfung" width="88%">
<img src="docs/screenshots/cockpit_kpi_dashboard.png" alt="KPI-Dashboard" width="88%">
<img src="docs/screenshots/cockpit_security.png" alt="Security-Demo" width="88%">

</details>

Mermaid wird lokal aus `static/` geladen – das Cockpit funktioniert auch ohne Internetzugang.

---

## 🤝 KI-Assistent & Human-in-the-Loop (`src/assistent.py`)

```text
Datei → Parser → 3-Way-Matching → BPMN-Routing → Prüfvermerk (LLM/Regelwerk) → Begründung
      → SAP-Abgleich (nur über den Read-Only-SQL-Guardrail) → Aktionsentwürfe [ENTWURF]
      → eine namentlich erfasste Person: Freigeben / Verwerfen (Zeitstempel + Kommentar) → Protokoll
```

| Route | Aktionsentwurf | Inhalt |
|---|---|---|
| Formfehler (§ 14 UStG / EN 16931) | **E-Mail an den Lieferanten** | exakte Felder mit BT-Nummer, z. B. *„BT-117: Ausgewiesen 273,50 EUR, korrekt 237,50 EUR“*, Hinweis auf Berichtigung nach § 31 Abs. 5 UStDV |
| Preis-/Mengenklärung | **Notiz an den Einkauf** | Abweichung in € bzw. Menge, Handlungsoptionen (ME22N / Gutschrift / BWA 122), Skonto-Dringlichkeit, Belegbezug EKKO · EKPO · MATDOC |
| Wareneingang fehlt | **Notiz an die Logistik** | fehlende Menge, MIGO/BWA 101 |
| Maverick Buying | **Notiz an den Fachbereich + Einkauf** | Bedarfsbestätigung, nachträgliche BANF |
| Dublette | **Vermerk Buchhaltung + E-Mail Lieferant** | Vergleichsbeleg RBKP/XBLNR |
| Injection-Verdacht | **Meldung an Compliance** | Fundstelle, Hinweis „Routing unberührt“ |

Auch die Texte des Assistenten durchlaufen die **Zahlen-Faktenprüfung** (Defense in Depth). Das LLM sieht ausschließlich bereinigte `AuditFakten` – **keine Roh-Freitexte (BT-22/BT-20) und keine Bankdaten**.

---

## 📥 Excel-Prüfbericht & Power-BI-Paket (`src/exporter.py`)

**Excel-Prüfbericht (`.xlsx`, 5 Blätter)**

| Blatt | Inhalt | Excel-Techniken |
|---|---|---|
| Übersicht | Stichtag, KPIs aus der Python-Engine **und** Gegenrechnung per Formel mit Abgleich ✔/✘, Sperrgründe | `COUNTIFS`/`SUMIFS`/`AVERAGEIFS` auf strukturierte Verweise, benannter Bereich `Stichtag`, Balkendiagramm |
| Rechnungen | alle Rechnungen mit Urteil, Zahlsperre, Durchlaufzeit, SHA-256 der Eingangsdatei | Excel-Tabelle, Ampel-Formatierung, Formelspalte |
| 3-Way-Abweichungen | Preis-, Mengen-, WE-Abweichungen mit Soll/Ist, EKPO-Position, **wertmäßiger Auswirkung** und Zuständigkeit | Datenbalken, `SUBTOTAL(109; …)` |
| Skonto-Risiko | Skontobetrag, Frist, Resttage, Status gesichert/gefährdet/verloren | Formeln `=ROUND(Brutto*Satz;2)`, `=Frist-Stichtag`, regelbasierte Hervorhebung |
| IKS-Prüfprotokoll | jede Einzelprüfung mit SHA-256 der Datei, SHA-256 des Prüfstands und **Hash-Kette** | `pruefe_hashkette()` erkennt jede nachträgliche Änderung |

Alle Formeln wurden mit LibreOffice nachgerechnet – Python-KPIs und Excel-Gegenrechnung stimmen überein.

**Power-BI-Paket (`.zip`)** – Sternschema als CSV (UTF-8 mit BOM, **Semikolon, Dezimalkomma**, Datum TT.MM.JJJJ):

```mermaid
flowchart LR
    FR["fact_rechnung"] --> DL["dim_lieferant"]
    FR --> DW["dim_workflow_schritt"]
    FR --> DD["dim_datum<br/>(Kalender)"]
    FP["fact_pruefung"] --> FR
    FP --> DC["dim_pruefcode"]
```

Dazu `measures.dax` (Dunkelverarbeitungsquote, Maverick-Quote, Skonto …), `laden.pq` (Power Query mit Kultur `de-DE`), `README.txt` mit Beziehungen und `manifest.json` mit SHA-256 je Datei. Bankdaten sind in keinem Export enthalten.

---

## 🔀 Prozessverfolgung: BPMN 2.0 und eEPK

Jede Rechnung durchläuft vier XOR-Gateways; **der erste fehlschlagende Gateway bestimmt die Route** (`ermittle_soll_route`). Durchlaufener Pfad blau, Haltepunkt grün (Freigabe), gelb (Klärung) oder rot (Sperre) – hier Rechnung 03 in der Preisklärung:

```mermaid
flowchart LR
    S(("Rechnung<br/>eingegangen"))
    T1["Dublettenprüfung<br/>(RBKP)"]
    G1{"✕ Dublette?"}
    E1((("Sperre:<br/>Dublette")))
    T2["Formale Prüfung<br/>(§ 14 UStG · EN 16931)"]
    G2{"✕ Formfehler?"}
    E2((("Klärung<br/>Lieferant")))
    T3["Bestellbezug prüfen<br/>(EKKO)"]
    G3{"✕ Bestellung?"}
    E3((("Compliance:<br/>Maverick Buying")))
    T4["3-Way-Matching<br/>(EKPO · MATDOC · RSEG)"]
    G4{"✕ Ergebnis"}
    E4((("Sperre:<br/>Wareneingang")))
    E5((("Klärung Einkauf:<br/>Preis")))
    E6((("Klärung Einkauf:<br/>Menge")))
    E7((("Touchless-<br/>Freigabe")))
    S --> T1
    T1 --> G1
    G1 -->|"ja"| E1
    G1 -->|"nein"| T2
    T2 --> G2
    G2 -->|"ja"| E2
    G2 -->|"nein"| T3
    T3 --> G3
    G3 -->|"nein"| E3
    G3 -->|"ja"| T4
    T4 --> G4
    G4 -->|"WE fehlt"| E4
    G4 -->|"Preis > Toleranz"| E5
    G4 -->|"Menge > Bestellung"| E6
    G4 -->|"alles OK"| E7
    classDef besucht fill:#dbeafe,stroke:#1d4ed8,color:#0b1f4d
    classDef aktiv_FREIGABE fill:#d1f2d9,stroke:#1a7f37,stroke-width:3px,color:#0b3d1a
    classDef aktiv_KLAERUNG fill:#fff4cc,stroke:#bf8700,stroke-width:3px,color:#4a3800
    classDef aktiv_SPERRE fill:#fde2e1,stroke:#cf222e,stroke-width:3px,color:#5c0a0f
    classDef inaktiv fill:#f6f8fa,stroke:#d0d7de,color:#8c959f
    classDef org fill:#ffffff,stroke:#8250df,color:#3e1f79
    classDef info fill:#ffffff,stroke:#57606a,color:#24292f
    class S,T1,G1,T2,G2,T3,G3,T4,G4 besucht
    class E1,E2,E3,E4,E6,E7 inaktiv
    class E5 aktiv_KLAERUNG
    linkStyle 0,1,3,4,6,7,9,10,12 stroke:#1d4ed8,stroke-width:3px
    linkStyle 2,5,8,11,13,14 stroke:#d0d7de,stroke-width:1px
```

Die eEPK-Darstellung (Ereignis → Funktion → XOR/AND-Konnektor, mit Organisationseinheiten und Informationsobjekten) zeigt das Cockpit je Rechnung; Beispiele in [docs/diagramme/](docs/diagramme/).

| Prüfcode | Route | Urteil | Aktion an |
|---|---|---|---|
| `DUBLETTE` | `SPERRE_DUBLETTE` | SPERRE | Buchhaltung + Lieferant |
| `PFLICHTFELD_FEHLT`, `USTID_UNGUELTIG`, `LEITWEG_ID_FEHLT`, `STEUERSATZ_UNZULAESSIG`, `SUMMENFEHLER` | `KLAERUNG_LIEFERANT_FORMFEHLER` | KLAERUNG | Lieferant |
| `KEINE_BESTELLUNG` | `COMPLIANCE_MAVERICK_BUYING` | KLAERUNG | Fachbereich + Einkauf |
| `KEIN_WARENEINGANG`, `WARENEINGANG_UNVOLLSTAENDIG` | `SPERRE_FEHLENDER_WARENEINGANG` | SPERRE | Logistik |
| `PREISABWEICHUNG` | `KLAERUNG_EINKAUF_PREIS` | KLAERUNG | Einkauf |
| `MENGENABWEICHUNG` | `KLAERUNG_EINKAUF_MENGE` | KLAERUNG | Einkauf |
| – | `TOUCHLESS_FREIGABE` | FREIGABE | – |

`SKONTO_FRIST` und `VERDAECHTIGER_FREITEXT` sind Hinweise und blockieren die Dunkelverarbeitung nicht.

---

## 🛡️ Sicherheitskonzept

| Ebene | Schutz | Nachweis |
|---|---|---|
| **Datei** | XML: kein DOCTYPE/ENTITY (XXE, Billion Laughs, auch UTF-16), `defusedxml`, 10 MB · PDF: keine verschlüsselten PDFs, keine `/JavaScript`, `/Launch`, `/SubmitForm`, 20 MB; nur der Anhang wird gelesen | `test_einvoice_parser`, `test_zugferd_pdf` |
| **Prompt-Injection** | Erkennung in BT-22/BT-20/Positionstexten · Freitexte gelangen nie ins LLM · Routing ist deterministisch, ein „überredetes“ LLM wird abgelehnt | `test_integration`, Security-Tab |
| **Halluzination** | Pydantic-Schema, Konsistenz zur Soll-Route, Zahlen-/Datums-Faktenprüfung, Korrekturschleife, Regelwerk-Fallback | `test_agent` |
| **SQL** | 4 Schichten: Lexik · Schema-Whitelist + SQLite-Authorizer (IBAN/SWIFT gesperrt) · `mode=ro` + `query_only` · Zeilen-/Zeitlimit | `test_agent`, Security-Tab |
| **Entscheidungen** | Human-in-the-Loop: Freigabe nur durch eine namentlich erfasste Person, keine Doppelentscheidung, JSONL-Protokoll (bewusst *kein* Vier-Augen-Prinzip mit zwei Personen – das wäre der nächste Ausbauschritt) | `test_assistent`, `test_cli_app` |
| **Revision** | SHA-256 von Datei und Prüfstand, Hash-Kette im IKS-Protokoll, Manifest mit SHA-256 im Power-BI-Paket | `test_exporter` |

---

## ⌨️ Kommandozeile (`run_agent.py`)

```bash
python run_agent.py alle                                   # alle 15 Dokumente, Soll/Ist-Vergleich
python run_agent.py pruefe data/invoices/03_preisabweichung_ubl.xml
python run_agent.py pruefe data/invoices/14_zugferd_pdf_mengenabweichung.pdf --diagramm epk
python run_agent.py pruefe data/invoices/09_formfehler_ohne_ustid_ubl.xml --freigeben "Max Mustermann"
python run_agent.py kpi                                    # Prozess-KPIs + Abwehrstatistik
python run_agent.py export                                 # exports/flowaudit_pruefbericht.xlsx + flowaudit_powerbi.zip
python run_agent.py sql "SELECT IBAN FROM LFBK"            # → BLOCKIERT, Schicht 2
python run_agent.py frage "Wie viele Rechnungen sind gesperrt?"   # nur mit LLM-API-Key
python run_agent.py init                                   # Demo-Daten neu erzeugen
```

### Konfiguration (`.env`)

| Variable | Bedeutung | Standard |
|---|---|---|
| `LLM_PROVIDER` | `offline`, `anthropic`, `gemini` (oder `mock` für Tests) | `offline` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Zugang & Modell für Claude | – / `claude-sonnet-5-5` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Zugang & Modell für Gemini | – / `gemini-3.8-flash` |
| `PRICE_TOLERANCE_PCT` | Preistoleranz im 3-Way-Matching in % | `2.0` |
| `MAX_CORRECTION_RETRIES` | Korrekturversuche vor dem Regelwerk-Fallback | `2` |
| `SQL_MAX_ROWS` / `SQL_TIMEOUT_SECONDS` | Ressourcenlimits des SQL-Guardrails | `200` / `3` |
| `FLOWAUDIT_DATA_DIR` | Datenordner für Cockpit/CLI | `data` |

---

## 📁 Verzeichnisstruktur

```text
FlowAudit-AI/
├── app.py                          Cockpit (Aufbau-Übersicht, Demo-Pfad, 3 Tabs, Export in der Seitenleiste)
├── run_agent.py                    Kommandozeile
├── src/
│   ├── agent.py                    KI-Audit-Agent, Guardrails, Faktenprüfung
│   ├── assistent.py                Begründung, SAP-Abgleich, Aktionsentwürfe (Human-in-the-Loop)
│   ├── einvoice_parser.py          XRechnung/ZUGFeRD-Parser, § 14 UStG, Rechenprüfung
│   ├── zugferd_pdf.py              ZUGFeRD/Factur-X: XML aus PDF/A-3 extrahieren & erzeugen
│   ├── matching_engine.py          3-Way-Matching, Dubletten, Maverick Buying, Buchung
│   ├── bpmn_tracker.py             BPMN-/eEPK-Diagramme, Prozess-KPIs
│   ├── exporter.py                 Excel-Prüfbericht & Power-BI-Paket
│   ├── demo.py                     Batchlauf, Security-Demo, Freigabeprotokoll
│   └── seed_data.py                SAP-Demodatenbank + Musterdokumente
├── data/invoices/                  15 Musterdokumente (XML + PDF) + manifest.json
├── docs/                           validierung.md/.json · diagramme/ · screenshots/
├── static/mermaid.min.js           lokales Mermaid (MIT-Lizenz) für den Offline-Betrieb
├── tools/offizielle_validierung.py Prüfung gegen CEN-/KoSIT-Schematron
├── tests/                          281 Tests (pytest)
├── .streamlit/config.toml · requirements.txt · pyproject.toml · .env.example · LICENSE
```

---

## 🧭 Einordnung im Portfolio

| | **ControllAI** (Projekt 1) | **FlowAudit-AI** (Projekt 2) |
|---|---|---|
| Ebene | strategisches Controlling | operative Prozess- & Systemebene |
| Fachgebiet | Balanced Scorecard, Management-Reporting | Purchase-to-Pay, Kreditorenbuchhaltung, E-Rechnung |
| Methodik | Text-to-SQL, Klassifikation, Process Mining | Regelprüfung, 3-Way-Matching, BPMN 2.0 / eEPK, IKS |
| Werkzeuge | Python, Streamlit, SQL | Python, SQL, Streamlit, Excel, Power BI, SAP-Datenmodell |
| KI-Rolle | Analyse & Interpretation | erklärender Assistent unter strengen Guardrails, Mensch entscheidet |

---

## ⚠️ Hinweise & Grenzen

- Alle Unternehmen, Personen, Adressen, USt-IdNrn. und Bankdaten sind **frei erfunden**; die KPIs zeigen die Methodik, keine realen Werte.
- FlowAudit-AI ist ein Portfolio- und Demonstrationsprojekt und **ersetzt keine steuerliche oder rechtliche Prüfung**.
- Die Demo-PDFs sind nach der Factur-X-Spezifikation aufgebaut (XMP, `/AF`, OutputIntent), aber nicht mit veraPDF zertifiziert; UBL-Dateien wurden nicht gegen das OASIS-XSD geprüft (Details in [docs/validierung.md](docs/validierung.md)).
- SAP-Tabellen- und Feldnamen dienen der fachlichen Orientierung; es besteht keine Verbindung zu einem SAP-System.

## 👤 Autor

**Sherwan Shweish** – B.Sc. Digital Business Management (Hochschule RheinMain, Wiesbaden Business School)
Schwerpunkte: Datenanalyse, Controlling, Prozessmodellierung (BPMN, eEPK), SAP S/4HANA, SQL, Python, Power BI

## 📄 Lizenz

[MIT](LICENSE) · Mermaid (`static/mermaid.min.js`) steht unter der MIT-Lizenz (`static/mermaid.LICENSE`).
