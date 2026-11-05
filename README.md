# FlowAudit-AI

**KI-Agent für Belegprüfung und Rechnungsfreigabe im Purchase-to-Pay**

<!-- Live-Demo: nach dem Deploy auf share.streamlit.io die tatsächliche App-Adresse eintragen -->
[![Live-Demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://flowaudit-ai.streamlit.app)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-375%20bestanden-2EA44F)
![EN 16931](https://img.shields.io/badge/EN%2016931-offiziell%20validiert-0A66C2)
![Offline](https://img.shields.io/badge/Demo-ohne%20API--Key-0A66C2)
![macOS](https://img.shields.io/badge/macOS-Desktop--App-555555?logo=apple)
![Lizenz](https://img.shields.io/badge/Lizenz-MIT-lightgrey)
![Standard](https://img.shields.io/badge/Agenten--Standard-v1-B85A33)

> *Darf diese Rechnung bezahlt werden – und wenn nicht, warum?*

FlowAudit-AI prüft die Eingangsrechnungen der fiktiven **RheinMain Industrietechnik GmbH** von der E-Rechnung
bis zur Zahlungsfreigabe: Formalprüfung nach **§ 14 Abs. 4 UStG** und **EN 16931**, **3-Way-Matching** gegen
Bestellung und Wareneingang aus SAP-Tabellen, **Betrugs- und Fehlerprävention**, **Klärfall-Workflow mit
Vier-Augen-Prinzip** und ein **Zahlungsvorschlag**, der Skonto sichert.

Die KI entscheidet dabei **nichts**. Prüfungen, Routing und Kennzahlen rechnet Python; ein Sprachmodell formuliert
höchstens Prüfvermerke und Rückfragen – jede Zahl darin wird vor der Übernahme gegen Prüfprotokoll und SAP-Belege
geprüft, und gezahlt wird erst nach namentlicher Freigabe.

---

## Inhalt

1. [Einblick: Cockpit, Prüfbericht und Mac-App](#einblick-cockpit-prüfbericht-und-mac-app)
2. [Schnellstart](#schnellstart)
3. [Einordnung in der Suite](#einordnung-in-der-suite)
4. [Business Value im Purchase-to-Pay](#business-value-im-purchase-to-pay)
5. [Leitprinzip: Determinismus vor KI](#leitprinzip-determinismus-vor-ki)
6. [Architektur](#architektur)
7. [Fachliche Prüfungen](#fachliche-prüfungen)
8. [Klärfall-Workflow und Zahlungsvorschlag](#klärfall-workflow-und-zahlungsvorschlag)
9. [Rechtsstand E-Rechnung (Stand prüfen)](#rechtsstand-e-rechnung-stand-prüfen)
10. [Die 15 Prüffälle](#die-15-prüffälle)
11. [Sicherheitskonzept](#sicherheitskonzept)
12. [Reporting und Export](#reporting-und-export)
13. [Unternehmenseinsatz](#unternehmenseinsatz)
14. [Kommandozeile und Konfiguration](#kommandozeile-und-konfiguration)
15. [Projektstruktur](#projektstruktur)
16. [Tests](#tests)
17. [Hinweise](#hinweise)

---

## Einblick: Cockpit, Prüfbericht und Mac-App

Rechnungseingang bis 28.10.2026 im Offline-Modus (synthetische Daten). Alle Bilder entstehen reproduzierbar aus
`uv run streamlit run app.py` bzw. `uv run python run_agent.py export`.

**Rechnungsprüfung** – Kennzahlenleiste mit Vorperiodenvergleich (geprüfte Rechnungen, Touchless-Quote, Volumen in
Klärung, gefährdetes Skonto, mittlere Durchlaufzeit), darunter Schnell-Demo, die eingeklappte Aufbau-Übersicht und
die Tabs. Je Rechnung: Urteil, Begründung, BPMN-Pfad und der *Audit-Copilot* mit Prüf-Scan und Kernaussagen.

![Rechnungsprüfung](docs/screenshots/01_rechnungspruefung.png)

| Klärfälle | Zahlung & Skonto |
|---|---|
| ![Klärfälle](docs/screenshots/02_klaerfaelle.png) | ![Zahlung und Skonto](docs/screenshots/03_zahlung_skonto.png) |
| Status, Zuständigkeit, faktengeprüfte Rückfrage, Vier-Augen-Freigabe ab 2.500 € mit Verlauf | Zahlläufe Di/Fr, Skonto gesichert/gefährdet/verloren, Skonto als Jahreszins |
| **Prüfkennzahlen** | **Security & Guardrails** |
| ![Prüfkennzahlen](docs/screenshots/04_pruefkennzahlen.png) | ![Security](docs/screenshots/05_security.png) |
| Eingänge je Woche, Sperr- und Klärungsgründe, Abweichungstreiber, Lieferanten-Scorecard | Prompt-Injection, SQL-Angriffe auf vier Schichten, manipulierte Dateien |

**Agent & Audit** – Agenten-Pipeline mit ihren Regeln, Trichter vom Eingang zur Zahlungsfreigabe, Prompt-Injection-Test
per Knopfdruck und append-only Audit-Protokoll (Export als JSON):

![Agent und Audit](docs/screenshots/06_agent_audit.png)

| Darstellung hell (Umschalter oben rechts) | Als Mac-App im eigenen Fenster |
|---|---|
| ![Hell](docs/screenshots/11_rechnungspruefung_hell.png) | ![Desktop-Fenster](docs/screenshots/12_desktop_fenster.png) |

**Prüfbericht** (PowerPoint und PDF, nur gelöste und namentlich freigegebene Klärungen) und **Excel-Gegenprüfung**:

| Kernaussagen (PowerPoint) | Abweichungstreiber (PowerPoint) |
|---|---|
| ![Kernaussagen](docs/screenshots/07_bericht_kernaussagen.png) | ![Treiber](docs/screenshots/08_bericht_treiber.png) |
| **Prüfbericht (PDF, Seite 1)** | **Excel: Formel-Gegenprüfung** |
| ![PDF-Bericht](docs/screenshots/09_bericht_pdf.png) | ![Excel-Gegenprüfung](docs/screenshots/10_excel_gegenpruefung.png) |

---

## Schnellstart

Voraussetzung: [uv](https://docs.astral.sh/uv/) (installiert Python bei Bedarf selbst). Alternativ funktioniert pip
mit `requirements.txt`.

```bash
git clone https://github.com/<benutzername>/FlowAudit-AI.git
cd FlowAudit-AI
uv sync                              # Umgebung mit gesperrten Versionen aus uv.lock
cp .env.example .env                 # optional: KI-Anbieter und Schlüssel (oder Schlüsselbund, siehe unten)
```

### Als Mac-App (empfohlen auf dem Mac)

```bash
uv run python -m desktop.build_app   # erzeugt ~/Applications/FlowAudit-AI.app
```

Danach startet FlowAudit-AI per Doppelklick, über Spotlight oder aus dem Dock – in einem eigenen Fenster ohne Browser.
Die App ist ein Starter für diesen Projektordner: Änderungen am Code sind beim nächsten Öffnen aktiv, ohne neu zu
bauen. Nur lokal erreichbar (127.0.0.1), eine Instanz, Protokolle unter `~/Library/Logs/FlowAudit-AI/`.

**API-Schlüssel im macOS-Schlüsselbund** statt in einer Datei (der Starter liest ihn automatisch):

```bash
security add-generic-password -s "FlowAudit-AI" -a "ANTHROPIC_API_KEY" -w   # fragt den Schlüssel verdeckt ab
```

Vorrang beim Start: Umgebungsvariable > `.env` > Schlüsselbund. Werte werden nie protokolliert.

### Im Browser

```bash
uv run streamlit run app.py                         # Webmodus: festes dunkles Thema
AGENT_LAUFZEIT=lokal uv run streamlit run app.py    # lokal mit Umschalter hell/dunkel (Wahl wird gespeichert)
```

### Online-Demo (Streamlit Community Cloud)

1. Repository auf GitHub veröffentlichen (`.env` ist ausgeschlossen – nie einen API-Schlüssel einchecken).
2. Auf [share.streamlit.io](https://share.streamlit.io) mit GitHub anmelden → *Create app* → Repository, Branch
   `main`, Datei `app.py`, Python 3.12 → *Deploy*. Danach die Adresse im Live-Demo-Badge oben eintragen.
3. Ohne Schlüssel läuft die Demo im Offline-Modus (Regelwerk) – kostenlos und für jeden Besucher gleich.

Im Webbetrieb teilen sich alle Besucher einen Server; deshalb ist das Thema dort fest und es werden keine
Einstellungen gespeichert (`AGENT_LAUFZEIT`, siehe [Agenten-Standard](docs/STANDARD.md)). Klärfälle leben in der
Sitzung des Besuchers. Ohne LibreOffice (Cloud) rechnet Excel die Formeln beim Öffnen; alle Exporte funktionieren.

### Ein Durchlauf auf der Kommandozeile

```bash
uv run python run_agent.py alle           # alle 15 Musterbelege, Soll/Ist-Vergleich der Routen
uv run python run_agent.py klaerfaelle    # Klärfälle mit Grund, Zuständigkeit und Vier-Augen-Pflicht
uv run python run_agent.py zahlung        # Zahlungsvorschlag nach Skontofrist und Fälligkeit
uv run python run_agent.py export         # Excel (Gegenprüfung), Power BI, Prüfbericht PDF und PowerPoint
```

---

## Einordnung in der Suite

FlowAudit-AI ist Teil einer durchgängigen Controlling-Suite rund um dasselbe fiktive Unternehmen – gleiche Firma,
Buchungskreis 1000 und dieselben Stammdatenschlüssel (Kostenstellen 4100–4500, Kostenarten 400000/410000/470000,
Produktgruppen ANT/STE/SEN).

Kette der Suite: DataGuard-AI → FlowAudit-AI → VarianceAI → ControllAI.

| Projekt | Ebene | Leitfrage | Schwerpunkte |
|---|---|---|---|
| **DataGuard-AI** | Stammdatenqualität & S/4HANA-Readiness | Sind unsere Kunden- und Lieferantenstammdaten korrekt, eindeutig und bereit für S/4HANA? | DAMA-Dimensionen, IBAN/USt-IdNr.-Prüfziffern, Dubletten (Blocking, rapidfuzz), Golden Record, CVI |
| **ControllAI** | Strategisches Controlling & BI | Wohin steuert das Unternehmen? | Balanced Scorecard, Text-to-SQL, Process Mining |
| **VarianceAI** | Operatives Controlling, Monatsabschluss | Warum weicht das Ergebnis vom Plan ab – und wo landen wir am Jahresende? | Preis-/Mengen-/Mix-Analyse, Plankostenrechnung, Forecast, IBCS-Bericht |
| **FlowAudit-AI** | Belegprüfung Purchase-to-Pay | Darf diese Rechnung bezahlt werden – und wenn nicht, warum? | E-Rechnung, § 14 UStG, 3-Way-Matching, Prävention, Klärfälle, Skonto |

**Abgrenzung:** FlowAudit-AI bleibt auf **Belegebene** – eine Rechnung, ihre Bestellung, ihr Wareneingang, ihre
Freigabe. Prozessweites Mining (Varianten, Engpässe über alle Vorgänge) liefert ControllAI, Plan-Ist-Abweichungen
im Monatsabschluss VarianceAI. Die Kontierung je Bestellposition (Kostenstelle, Sachkonto, Produktgruppe) macht den
Materialaufwand aus VarianceAI bis zur einzelnen Rechnung nachvollziehbar.

---

## Business Value im Purchase-to-Pay

| Schritt | Typischer Engpass | Beitrag von FlowAudit-AI | Messgröße |
|---|---|---|---|
| Eingang | PDF-Rechnungen, fehlende Pflichtangaben, Rechenfehler | XRechnung/ZUGFeRD lesen, Formalprüfung § 14 Abs. 4 UStG + EN 16931 mit verständlicher Fehlerliste an den Lieferanten | Quote formal korrekter E-Rechnungen |
| Abgleich | Manuelles Nachschlagen in ME23N/MIGO/MIRO | 3-Way-Matching je Position mit Preis- und Mengentoleranz, Treiber Preis/Menge/Teil-Lieferung/fehlender WE | **Touchless-Quote** |
| Prävention | Doppelzahlung, Zahlung an geänderte Bankverbindung | Dublettenverdacht, Bankdatenänderung (CDHDR/CDPOS), Rechnung ohne Bestellung, glatte Beträge – mit Begründung | verhinderte Fehlzahlungen in € |
| Klärung | Rückfragen per E-Mail ohne Status | Klärfall mit Zuständigkeit, faktengeprüfter Rückfrage, Vier-Augen ab Betragsgrenze | Volumen in Klärung, Durchlaufzeit |
| Zahlung | Skonto verfällt während der Klärung | Zahlungsvorschlag im letzten Zahllauf vor der Skontofrist, Priorisierung gefährdeter Klärfälle | Skonto gesichert / gefährdet / verloren |
| Lieferanten | Bauchgefühl im Lieferantengespräch | Scorecard: Fehlerquote, Durchlaufzeit, Preisabweichungen, Liefertreue, Bewertung A/B/C | Fehlerquote je Lieferant |
| Revision | Prüfspur über Mails und Excel verteilt | SHA-256 je Datei, Hash-Kette im Prüfprotokoll, append-only Audit-Log, Prüfbericht nur mit Freigegebenem | lückenlose Prüfspur |

---

## Leitprinzip: Determinismus vor KI

| Grundsatz | Umsetzung |
|---|---|
| **Python entscheidet** | Formalprüfung, Matching, Prävention, Routing, Kennzahlen und Zahlungsvorschlag sind deterministisch (`Decimal`, centgenau); Invarianten wie *Summe der Treiber = Rechnung netto* sind per Test abgesichert |
| **Das LLM sieht nur ein Faktenblatt** | Pydantic-validierte `AuditFakten` – keine Roh-Freitexte (BT-22/BT-20), **keine IBAN oder Bankdaten** |
| **Jede Zahl wird geprüft** | Zahlen und Daten in Prüfvermerk und Rückfrage müssen in Prüfprotokoll oder SAP-Belegen vorkommen; sonst wird der Text verworfen bzw. die Rückfrage gesperrt |
| **Routing-Abgleich** | Weicht die Route des LLM von der deterministischen Route ab, wird die Antwort verworfen (Korrekturschleife, danach Regelwerk) |
| **Human-in-the-Loop** | Rückfragen, Lösungen und Zahlungsfreigaben nur mit Namen; ab 2.500 € brutto durch zwei verschiedene Personen |
| **Offline-Modus** | Ohne API-Schlüssel läuft alles mit dem Regelwerk – Prüfung, Routing, Klärfälle, Zahlung, Exporte |

---

## Architektur

```mermaid
flowchart LR
    subgraph EIN["① Eingang"]
        X["XRechnung UBL/CII<br/>ZUGFeRD-XML"]
        P["ZUGFeRD-PDF/A-3"]
    end

    subgraph DET["② Deterministische Prüfung (Python)"]
        V["einvoice_parser · zugferd_pdf<br/>XXE-/PDF-Schutz"]
        F["formalpruefung<br/>§ 14 Abs. 4 UStG · EN 16931"]
        M["matching_engine · treiber<br/>3-Way-Matching, Toleranzen"]
        PR["praevention<br/>Dublette · Bankdaten · ohne PO · Rundbetrag"]
        DB[("SQLite im SAP-Stil<br/>LFA1/LFBK · EKKO/EKPO · EKBE<br/>MATDOC · RBKP/RSEG · CDHDR/CDPOS")]
        R{"BPMN-Routing"}
    end

    subgraph WF["③ Workflow"]
        K["klaerfall<br/>offen → in Klärung → gelöst<br/>Vier-Augen"]
        Z["zahlung<br/>Zahlläufe Di/Fr · Skonto"]
        L["lieferanten<br/>Scorecard"]
    end

    subgraph KI["④ KI mit Guardrails"]
        AG["agent · assistent<br/>Prüfvermerk · Rückfrage"]
        FP{"Faktenprüfung<br/>jeder Zahl"}
        SQLG["Read-Only-SQL-Guardrail"]
    end

    subgraph AUS["⑤ Ausgabe"]
        APP["Cockpit · Mac-App · CLI"]
        EXP["Excel mit Gegenprüfung<br/>Power BI · PDF · PowerPoint"]
    end

    X & P --> V --> F --> M --> R
    DB --> M & PR
    PR --> R --> K --> Z --> APP
    R --> AG --> FP --> K
    AG -. "SAP-Abgleich" .-> SQLG --> DB
    K & Z & L --> EXP --> APP
```

Alle Ergebnisse eines Prüflaufs bündelt `src/pruefstand.py` (Batch, Vorgänge, Formalprüfung, Prävention, Klärfälle,
Treiber, Scorecard, Periodenvergleich) – Cockpit, CLI und Exporte rechnen damit identisch.

---

## Fachliche Prüfungen

### Formalprüfung nach § 14 Abs. 4 UStG und EN 16931 (`src/formalpruefung.py`)

Je Rechnung eine Checkliste mit 20 Prüfpunkten – jeweils mit Fundstelle (BT-Nummer), Befund, Folge und Korrektur:

| Gruppe | Prüfpunkte |
|---|---|
| **§ 14 Abs. 4 UStG** | Nr. 1 Name und Anschrift von Lieferant und Empfänger · Nr. 2 USt-IdNr. oder Steuernummer · Nr. 3 Ausstellungsdatum · Nr. 4 Rechnungsnummer · Nr. 5 Menge und Art · Nr. 6 Leistungszeitpunkt · Nr. 7 Entgelt nach Steuersätzen · Nr. 8 Steuersatz und Steuerbetrag · Nr. 9–10 Aufbewahrungshinweis / „Gutschrift“ (falls einschlägig) |
| **EN 16931** | BR-01 Spezifikation · BR-04 Rechnungsart · BR-05 Währung · BR-09/11 Länder · BR-13/14 Gesamtbeträge · BR-15 Zahlbetrag · BR-21–26 Positionsdaten · BR-CO-10/13/15 rechnerische Richtigkeit |
| **XRechnung (CIUS)** | BR-DE-15 Käuferreferenz / Leitweg-ID |

Beispiel aus der Fehlerliste (Fall 09): *„Weder USt-IdNr. noch Steuernummer des Lieferanten angegeben (§ 14 Abs. 4
UStG Nr. 2, BT-31/BT-32) – ohne diese Angabe ist der Vorsteuerabzug gefährdet. Berichtigte Rechnung anfordern.“*
Fall 10 (Zahlendreher in der Umsatzsteuer) nennt die Folge nach § 14c Abs. 1 UStG. Deutsche USt-IdNrn. werden
neben dem Format auch über die Prüfziffer (ISO 7064 MOD 11,10) geprüft – offline; ob eine Nummer tatsächlich vergeben
ist, klärt erst die Bestätigungsabfrage (BZSt bzw. VIES), die hier bewusst nicht automatisiert wird. Die Muster sind zusätzlich gegen
die offiziellen Schematron-Regeln von CEN und KoSIT validiert ([docs/validierung.md](docs/validierung.md)).

### 3-Way-Matching mit Abweichungstreibern (`src/matching_engine.py`, `src/treiber.py`)

- **Toleranzen konfigurierbar:** Preis ±2,0 % (`PRICE_TOLERANCE_PCT`), Mengen-Mehrberechnung +0 %
  (`QUANTITY_TOLERANCE_PCT`), beide im Cockpit unter *Toleranzen und Freigabe* einstellbar.
- **Treiber je Position:** Der Nettobetrag jeder Rechnung wird zerlegt in *gedeckt* + **Preis** + **Menge** +
  **Teil-Lieferung** + **fehlender WE** (+ *ohne Bestellung*, *Dublette*). Invariante, auf den Cent geprüft:
  `netto = gedeckt + Σ Treiber`.
- **Drill-down bis zur Position:** Bestellung (EKKO/EKPO mit Werk, Kostenstelle, Sachkonto, Liefertermin),
  Bestellentwicklung (EKBE: Wareneingänge und bereits berechnete Mengen), Rechnung (RBKP/RSEG) – inklusive der
  ausgeführten SQL-Abfragen durch den Read-Only-Guardrail.

### Betrugs- und Fehlerprävention (`src/praevention.py`)

| Regel | Schwere | Logik (deterministisch) | Maßnahme |
|---|---|---|---|
| **Dublettenverdacht** | hoch – Zahlung gesperrt | gleicher Lieferant, Betrag ±0,5 %, Rechnungsdatum ≤ 14 Tage auseinander **oder** Rechnungsnummer ähnlich (≥ 0,85); gemeldet auf dem späteren Beleg | Vergleichsbeleg prüfen, ggf. stornieren |
| **Bankverbindung kürzlich geändert** | hoch – Zahlung gesperrt | Änderungsbeleg zur IBAN (CDHDR/CDPOS) in den 30 Tagen vor Rechnungseingang | Rückruf über die bekannte Telefonnummer dokumentieren |
| **Rechnung ohne Bestellung** | mittel | kein Bestellbezug (BT-13 leer) | Bedarf bestätigen, nachträgliche BANF |
| **Ungewöhnlich glatter Betrag** | mittel | Nettobetrag ≥ 1.000 € und Vielfaches von 500 €, nicht aus Menge × Bestellpreis (ohne Bestellung oder eine Pauschalposition) | Leistungsnachweis anfordern |

Jeder Treffer trägt eine **Begründung mit Belegbezug**, z. B. *„IBAN des Lieferanten am 23.10.2026 geändert
(DE…4711 → DE…7654, Benutzer KRED_SACHB2) – 5 Tage vor Rechnungseingang“*. IBANs erscheinen nur maskiert.

### Kennzahlen mit Vorperiodenvergleich (`src/bpmn_tracker.py`)

Berichtsperiode sind die letzten 28 Tage bis zum Stichtag, Vorperiode die 28 Tage davor; Bestandsgrößen (Volumen
in Klärung, gefährdetes Skonto) gelten zum Periodenende. Sparklines zeigen 8 Wochen. Farbe trägt Bedeutung:
höhere Touchless-Quote und kürzere Durchlaufzeit grün, mehr Volumen in Klärung und mehr gefährdetes Skonto rot.

---

## Klärfall-Workflow und Zahlungsvorschlag

```text
offen ──(Rückfrage freigeben: Name + Faktenprüfung)──▶ in Klärung ──(Lösung + Name)──▶ gelöst
                                                            │
                                    ab 2.500 € brutto ──────┴──▶ FREIGABE 1/2 ──(zweite, andere Person)──▶ gelöst
```

- **Zuständigkeit** aus dem Grund: Einkauf (Preis/Menge), Wareneingang/Logistik, Fachbereich (ohne Bestellung),
  Kreditorenbuchhaltung (Formfehler, Dublette, Rückruf bei Bankdatenänderung).
- **Rückfrage:** im Offline-Modus aus dem Regelwerk, mit KI-Anbieter vom LLM formuliert. Jede Zahl und jedes Datum
  wird live geprüft; eine Rückfrage mit unbelegter Zahl lässt sich nicht freigeben.
- **Vier-Augen-Prinzip:** Die zweite Freigabe muss von einer anderen Person kommen (Groß-/Kleinschreibung zählt
  nicht). Der Verlauf ist append-only.
- **Keine Zahlung** für Dubletten und abgewiesene Dateien – auch nicht nach „Lösung“.

**Zahlungsvorschlag** (`src/zahlung.py`): Zahlläufe dienstags und freitags. Mit Skonto zahlt FlowAudit-AI im letzten
Lauf vor Ablauf der Skontofrist, sonst im letzten Lauf vor Nettofälligkeit. Skonto als Jahreszins:

```math
i = \frac{s}{1 - s} \cdot \frac{360}{\text{Zahlungsziel} - \text{Skontotage}}
```

Beispiel: 3 % Skonto bei Zahlung bis 03.11.2026 statt 23.11.2026 entsprechen **55,7 % p. a.** – deshalb werden
Klärfälle mit laufender Skontofrist zuerst bearbeitet. Gesperrte Rechnungen erscheinen mit Grund und, falls Skonto
droht, mit dem spätesten Klärungstermin.

**Lieferanten-Scorecard** (`src/lieferanten.py`): Fehlerquote, mittlere Durchlaufzeit, Anzahl und Betrag der
Preisabweichungen, Liefertreue (Wareneingang bis Liefertermin EKPO) und Präventionstreffer – Bewertung A
(≤ 25 % Fehler und ≥ 90 % Liefertreue), C (> 50 % Fehler oder < 70 % Liefertreue), sonst B, jeweils mit Begründung.

---

## Rechtsstand E-Rechnung (Stand prüfen)

> **Stand: Oktober 2026 – vor dem Einsatz prüfen.** Keine Steuer- oder Rechtsberatung.

| Zeitpunkt | Regel im inländischen B2B-Verkehr |
|---|---|
| seit 01.01.2025 | Alle Unternehmen müssen E-Rechnungen **empfangen** können (strukturiertes Format nach EN 16931, z. B. XRechnung oder ZUGFeRD ab Profil EN 16931). |
| bis 31.12.2026 | Übergang beim Versand: Papierrechnungen und sonstige elektronische Formate (z. B. PDF, mit Zustimmung des Empfängers) bleiben zulässig. |
| bis 31.12.2027 | Verlängerter Übergang für Unternehmen mit Vorjahresumsatz bis 800.000 € sowie für bestimmte EDI-Verfahren. |
| ab 01.01.2028 | E-Rechnung im inländischen B2B-Verkehr für alle Pflicht. |

Rechtsgrundlagen: § 14 UStG in der Fassung des Wachstumschancengesetzes und die BMF-Schreiben zur E-Rechnung.
FlowAudit-AI weist reine Bild-PDFs deshalb mit Begründung ab und fordert XRechnung oder ZUGFeRD an.
Quellen: [IHK Stuttgart](https://www.ihk.de/stuttgart/fuer-unternehmen/recht-und-steuern/steuerrecht/steuermeldungen/e-rechnungen-5864496),
[IHK Chemnitz](https://www.ihk.de/chemnitz/e-rechnung), [PwC zum BMF-Schreiben](https://newsletter.pwc.de/umsatzsteuer/nl0029-2024-10-29/bmf-e-rechnung).

---

## Die 15 Prüffälle

`uv run python run_agent.py init` erzeugt die fiktive **RheinMain Industrietechnik GmbH** (Wiesbaden) mit sechs
Lieferanten, Bestellungen mit Kontierung, Wareneingängen, 50 gebuchten Rechnungen, Änderungsbelegen zur
Bankverbindung und diesen Dokumenten in `data/invoices/`:

| # | Fall | Format | Route (deterministisch) | Prävention / Klärfall |
|---|---|---|---|---|
| 01 | Perfekte Rechnung, Skonto | XRechnung UBL | `TOUCHLESS_FREIGABE` | Zahlung mit Skonto |
| 02 | Perfekte Rechnung, Skonto 3 % | ZUGFeRD CII | `TOUCHLESS_FREIGABE` | Zahlung mit Skonto |
| 03 | Einzelpreis +4,50 % | XRechnung UBL | `KLAERUNG_EINKAUF_PREIS` | Einkauf · Skonto gefährdet |
| 04 | Einzelpreis +1,50 % (in Toleranz) | XRechnung CII | `TOUCHLESS_FREIGABE` | Zahlung (ohne Skonto) |
| 05 | Kein Wareneingang | XRechnung UBL | `SPERRE_FEHLENDER_WARENEINGANG` | Logistik |
| 06 | Teillieferung 60 von 100 | ZUGFeRD CII | `SPERRE_FEHLENDER_WARENEINGANG` | Logistik · zusätzlich Dublettenverdacht und Bankdaten geändert |
| 07 | Ohne Bestellung, 19 %/7 % | XRechnung UBL | `COMPLIANCE_MAVERICK_BUYING` | Fachbereich |
| 08 | Rechnungsnummer bereits gebucht | XRechnung UBL | `SPERRE_DUBLETTE` | keine Zahlung |
| 09 | USt-IdNr./Steuernummer fehlen | XRechnung UBL | `KLAERUNG_LIEFERANT_FORMFEHLER` | Fehlerliste § 14 Abs. 4 Nr. 2 |
| 10 | Zahlendreher in der USt (273,50 statt 237,50 €) | ZUGFeRD CII | `KLAERUNG_LIEFERANT_FORMFEHLER` | Fehlerliste BR-CO, § 14c |
| 11 | **Prompt-Injection in BT-22** + Preis +8 % | XRechnung UBL | `KLAERUNG_EINKAUF_PREIS` – Injection wirkungslos | Einkauf · Vier-Augen |
| 12 | Gleicher Lieferant, Tag und Betrag | ZUGFeRD CII | `SPERRE_DUBLETTE` (Verdacht) | Dublettenverdacht |
| 13 | **ZUGFeRD-PDF/A-3**, alles korrekt | PDF + factur-x.xml | `TOUCHLESS_FREIGABE` | Bankdaten geändert → Rückruf, Vier-Augen |
| 14 | **ZUGFeRD-PDF/A-3**, 120 berechnet / 100 bestellt | PDF + factur-x.xml | `KLAERUNG_EINKAUF_MENGE` | Einkauf |
| 15 | Reines Bild-PDF ohne XML | PDF | abgewiesen (keine E-Rechnung) | XRechnung/ZUGFeRD anfordern |

Fall 13 zeigt den Unterschied zwischen Prüfung und Prävention: formal und sachlich einwandfrei, trotzdem keine
Zahlung, bevor die geänderte Bankverbindung telefonisch bestätigt ist.

---

## Sicherheitskonzept

| Ebene | Schutz | Nachweis |
|---|---|---|
| **Datei** | XML: kein DOCTYPE/ENTITY (XXE, Billion Laughs, auch UTF-16), `defusedxml`, 10 MB · PDF: keine verschlüsselten PDFs, kein `/JavaScript`, `/Launch`, `/SubmitForm`, 20 MB | `test_einvoice_parser`, `test_zugferd_pdf` |
| **Prompt-Injection** | Erkennung in BT-22/BT-20/Positionstexten · Freitexte gelangen nie ins LLM · Routing deterministisch, ein „überredetes“ LLM wird verworfen | `test_integration`, Tab *Agent & Audit* |
| **Halluzination** | Pydantic-Schema, Abgleich mit der Soll-Route, Zahlen-/Datums-Faktenprüfung für Vermerk **und** Rückfrage, Regelwerk-Fallback | `test_agent`, `test_fachlogik_b` |
| **SQL** | 4 Schichten: Lexik · Schema-Whitelist + SQLite-Authorizer (IBAN/SWIFT gesperrt) · `mode=ro` + `query_only` · Zeilen-/Zeitlimit | `test_agent`, Tab *Security* |
| **Bankdaten** | IBAN nie im Prompt, in keinem Export, in der Oberfläche nur maskiert (`DE…7654`) | `test_exporter`, `test_fachlogik_b` |
| **Freigaben** | namentlich, Vier-Augen ab Betragsgrenze, zweite Person ≠ erste, keine Zahlung für Dubletten | `test_fachlogik_b`, `test_cli_app` |
| **Oberfläche** | jeder dynamische Text HTML-maskiert (`ui.e()`), Grafiken als Data-URI, Server nur auf 127.0.0.1 (Desktop) | `test_ui`, `test_desktop` |
| **Geheimnisse** | Schlüssel nur aus Umgebung, `.env` oder Schlüsselbund; nie im Repository, nie im Log | `test_desktop` |
| **Revision** | SHA-256 von Datei und Prüfstand, Hash-Kette im IKS-Protokoll, Manifest mit SHA-256 im Power-BI-Paket | `test_exporter` |

---

## Reporting und Export

| Ausgabe | Modul | Inhalt |
|---|---|---|
| **Excel-Prüfbericht** | `src/exporter.py` | 11 Blätter: *Übersicht, Gegenprüfung, Rechnungen, 3-Way-Abweichungen, Skonto-Risiko, IKS-Prüfprotokoll, Klärfälle, Zahlungsvorschlag, Treiber, Prävention, Lieferanten*; Formeln **und** gespeicherte Ergebniswerte |
| **Prüfbericht PDF** | `src/bericht.py` | A4 quer: Kernaussagen, Kennzahlen mit Vorperiode, freigegebene Klärungen, Treiber, Zahlungsvorschlag, Prävention |
| **Prüfbericht PowerPoint** | `src/bericht.py` | 5 Folien (16:9) mit nativem Diagramm der Abweichungstreiber |
| **Power-BI-Paket** | `src/exporter.py` | Sternschema als CSV: 8 Faktentabellen (Rechnung, Prüfung, Bestellposition, Klärfall, Zahlung, Risiko, Treiber, Lieferanten-Scorecard), 7 Dimensionen inkl. Kalender, Kostenstelle, Kostenart, Produktgruppe; `measures.dax`, `laden.pq`, `manifest.json` |

- **Nur Freigegebenes geht in den Bericht:** offene, in Klärung befindliche oder auf die zweite Freigabe wartende
  Klärfälle erscheinen weder im PDF noch in PowerPoint; der Bericht nennt ihre Anzahl.
- **Formel-Gegenprüfung:** Zahlbetrag, Skonto gesichert/gefährdet, Treibersumme = Netto, gelöste Klärfälle,
  Präventionstreffer und Rechnungen je Lieferant stehen in Excel als Formeln über strukturierte Verweise. Das Blatt
  *Gegenprüfung* vergleicht sie mit Python; nach der Neuberechnung mit LibreOffice: 7 Prüfungen, Gesamtstatus *OK*,
  0 Formelfehler. Wird ein Wert manipuliert, springt der Status auf *FEHLER*.
- **Power BI im deutschen Format:** Semikolon, Dezimalkomma, `TT.MM.JJJJ`, UTF-8 mit BOM, keine Bankdaten.
- **Exporte im Cockpit** spiegeln den aktuellen Stand der Klärfälle (Seitenleiste *Export*).

---

## Unternehmenseinsatz

Wie FlowAudit-AI produktiv betrieben würde – SAP-MM/FI-Anbindung (OData, CDS-Views, technischer Benutzer mit reinen
Anzeigerechten, Feldzuordnung EKKO/EKPO/EKBE/RBKP), E-Rechnungs-Eingangskanal (Postfach, Peppol, Portal, GoBD-Ablage),
API-Schlüssel im Secret-Manager, Betrieb, Datenschutz und Mitbestimmung, Einführungsplan und Antworten für das
Vorstellungsgespräch – beschreibt der Leitfaden
[`docs/Leitfaden_Unternehmenseinsatz.docx`](docs/Leitfaden_Unternehmenseinsatz.docx). Vertiefend:
[`docs/SAP-Anbindung.docx`](docs/SAP-Anbindung.docx) (Datenobjekte, Feldzuordnung, Anbindungsvarianten, zwölf Schritte)
und [`docs/Kennzahlen_und_Diagramme.docx`](docs/Kennzahlen_und_Diagramme.docx) (jede Kennzahl mit Rechenweg und Demo-Wert).

| Baustein | Demo (dieses Repository) | Produktivbetrieb |
|---|---|---|
| Daten | `src/seed_data.py` (synthetisch, SQLite im SAP-Stil) | SAP-Konnektor füllt eine Staging-Datenbank mit demselben Schema |
| Eingang | `data/invoices/` und Upload im Cockpit | Funktionspostfach, Peppol-Zugangspunkt oder Lieferantenportal |
| KI-Schlüssel | ohne Schlüssel: Regelwerk | Secret-Manager → Umgebungsvariable; Mac-App: Schlüsselbund |
| Klärfälle | Sitzung bzw. lokal | zentrale Datenbank, Rollen per Single Sign-on, persistentes Audit-Log |

---

## Kommandozeile und Konfiguration

```bash
uv run python run_agent.py alle                                   # alle 15 Dokumente, Soll/Ist-Vergleich
uv run python run_agent.py pruefe data/invoices/03_preisabweichung_ubl.xml
uv run python run_agent.py pruefe data/invoices/14_zugferd_pdf_mengenabweichung.pdf --diagramm epk
uv run python run_agent.py pruefe data/invoices/09_formfehler_ohne_ustid_ubl.xml --freigeben "Max Mustermann"
uv run python run_agent.py kpi                                    # Prozess-KPIs + Abwehrstatistik
uv run python run_agent.py klaerfaelle                            # Klärfälle, Zuständigkeit, Vier-Augen
uv run python run_agent.py zahlung                                # Zahlungsvorschlag
uv run python run_agent.py export                                 # exports/: xlsx (Gegenprüfung), zip, pdf, pptx
uv run python run_agent.py sql "SELECT IBAN FROM LFBK"            # → BLOCKIERT, Schicht 2
uv run python run_agent.py frage "Wie viele Rechnungen sind gesperrt?"   # nur mit KI-Anbieter
uv run python run_agent.py diagnose                               # Umgebung prüfen
uv run python run_agent.py init                                   # Demo-Daten neu erzeugen
```

| Variable | Bedeutung | Standard |
|---|---|---|
| `LLM_ANBIETER` | `offline`, `mock`, `claude`, `gemini` (ältere Schreibweise `LLM_PROVIDER` wird akzeptiert) | `offline` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Zugang und Modell für Claude | – / siehe `.env.example` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Zugang und Modell für Gemini | – / siehe `.env.example` |
| `LLM_TEMPERATUR`, `LLM_MAX_TOKENS` | Formulierung und Kostenbegrenzung | `0` / `4096` |
| `PRICE_TOLERANCE_PCT` | Preistoleranz im 3-Way-Matching in % | `2.0` |
| `QUANTITY_TOLERANCE_PCT` | zulässige Mehrberechnung ggü. offener Menge in % | `0` |
| `MAX_CORRECTION_RETRIES` | Korrekturversuche vor dem Regelwerk-Fallback | `2` |
| `SQL_MAX_ROWS` / `SQL_TIMEOUT_SECONDS` | Ressourcenlimits des SQL-Guardrails | `200` / `3` |
| `FLOWAUDIT_DATA_DIR` | Datenordner für Cockpit und CLI | `data` |
| `AGENT_LAUFZEIT` | `desktop` (setzt der Launcher), `lokal`, sonst Webmodus | Webmodus |

Modell-IDs bitte in der offiziellen Dokumentation prüfen
([Claude](https://docs.claude.com/en/docs/about-claude/models), [Gemini](https://ai.google.dev/gemini-api/docs/models)).
Im Cockpit stehen unter *KI-Anbieter* immer Offline (Regelwerk), Anthropic Claude und Google Gemini zur Wahl; fehlt der
Schlüssel, zeigt die Seitenleiste einen Hinweis und das Regelwerk arbeitet weiter. Die Vier-Augen-Grenze (Standard
2.500 € brutto) und beide Toleranzen lassen sich im Cockpit anpassen.

---

## Projektstruktur

```text
FlowAudit-AI/
├── README.md · LICENSE · CLAUDE.md          Arbeitsregeln (Agenten-Standard v1)
├── pyproject.toml · uv.lock                 Abhängigkeiten (uv, gesperrt), pytest- und ruff-Konfiguration
├── requirements.txt                         dieselben Abhängigkeiten für pip
├── .env.example                             KI-Anbieter, Toleranzen, Limits (ohne Secrets)
├── app.py                                   Streamlit-Cockpit (6 Tabs, hell/dunkel, Offline-Modus)
├── run_agent.py                             Kommandozeile
├── .streamlit/config.toml                   Thema-Startwerte, Schriften
├── desktop/                                 Mac-App: app.toml (Identität), launcher, server, build_app, praeferenzen
├── src/
│   ├── agent.py                             KI-Audit-Agent, Guardrails, Faktenprüfung, SQL-Guardrail, LLM-Clients
│   ├── assistent.py                         Vorgang, SAP-Abgleich (EKPO/EKBE/RSEG), Aktionsentwürfe
│   ├── einvoice_parser.py · zugferd_pdf.py  XRechnung/ZUGFeRD lesen, XXE- und PDF-Schutz
│   ├── formalpruefung.py                    § 14 Abs. 4 UStG + EN 16931: Checkliste und Fehlerliste
│   ├── matching_engine.py · treiber.py      3-Way-Matching mit Toleranzen, Abweichungstreiber (centgenau)
│   ├── praevention.py                       Dublettenverdacht, Bankdatenänderung, ohne Bestellung, glatte Beträge
│   ├── klaerfall.py                         Status, Zuständigkeit, Rückfrage, Vier-Augen-Freigabe
│   ├── zahlung.py · lieferanten.py          Zahlungsvorschlag mit Skonto, Lieferanten-Scorecard
│   ├── pruefstand.py                        ein Prüflauf für Cockpit, CLI und Exporte
│   ├── bpmn_tracker.py                      BPMN-/eEPK-Diagramme, KPIs, Periodenvergleich
│   ├── copilot.py                           Prüf-Scan, Kernaussagen, Ablauf bis zur Freigabe
│   ├── bericht.py · exporter.py             Prüfbericht PDF/PowerPoint, Excel mit Gegenprüfung, Power BI
│   ├── ui_design.py · ui_diagramme.py       Designsystem (maskierte HTML-Bausteine), Altair-Diagramme
│   ├── demo.py · diagnose.py                Batchlauf, Injection-Test, Umgebungsprüfung
│   └── seed_data.py                         SAP-Demodatenbank + 15 Musterdokumente
├── static/                                  Schriften (SIL OFL), Favicon, Mermaid (MIT) – offline
├── data/invoices/                           15 Musterdokumente + manifest.json (Datenbank wird lokal erzeugt)
├── docs/
│   ├── STANDARD.md                          Agenten-Standard v1
│   ├── Leitfaden_Unternehmenseinsatz.docx   Überblick: SAP MM/FI, E-Rechnungs-Eingang, Secret-Manager, Interviewfragen
│   ├── SAP-Anbindung.docx                   Anbindung an echte Unternehmensdaten Schritt für Schritt
│   ├── Kennzahlen_und_Diagramme.docx        jede Seite, jede Kennzahl, jedes Diagramm erklärt
│   ├── validierung.md/.json · diagramme/    offizielle Validierung, BPMN/eEPK-Beispiele
│   └── screenshots/                         Cockpit (dunkel/hell), Mac-Fenster, Prüfbericht, Excel
├── tools/                                   offizielle Validierung (CEN/KoSIT), App-Symbol
└── tests/                                   367 Tests (13 Module)
```

---

## Tests

```bash
uv run pytest -q && uv run ruff check .
```

Stand v1.2.1: **376 Tests, 375 bestanden** (einer wird ohne das optionale Paket `vl-convert` übersprungen),
vollständig offline und ohne API-Schlüssel – verifiziert in frischen Umgebungen aus `uv.lock` mit **Python 3.11 und
3.13**; `ruff` ohne Befund.

| Modul | Tests | Schwerpunkt |
|---|---:|---|
| `test_agent` | 83 | Faktenprüfung, Routing-Abgleich, SQL-Guardrail (4 Schichten), LLM-Clients |
| `test_einvoice_parser` | 55 | UBL/CII, § 14 UStG, USt-IdNr.-Prüfziffer, Rechenprüfung, XXE, Skonto, Injection-Erkennung |
| `test_matching_engine` | 39 | Dubletten, Maverick Buying, WE, Preis- und Mengentoleranz, Buchung |
| `test_assistent` | 28 | SAP-Abgleich inkl. EKBE, Aktionsentwürfe, Human-in-the-Loop |
| `test_cli_app` | 28 | alle CLI-Befehle, Streamlit-Tabs per `AppTest`: KPI mit Vorperiode, Klärfall-Workflow, Fehlerliste, Fehlergrenzen |
| `test_desktop` | 21 | Konfiguration (`logo`), Einzelinstanz, Schlüssel-Vorrang, nur 127.0.0.1, Laufzeitmodi, App-Bundle |
| `test_copilot` | 20 | Prüf-Scan, Kernaussagen, Ablaufstatus, Prüfzahlen |
| `test_zugferd_pdf` | 20 | XML aus PDF/A-3, PDF-Sicherheitsprüfung |
| `test_fachlogik_b` | 20 | KI-Anbieter-Auswahl, Formalprüfung, Treiber-Invariante, Prävention, Klärfall/Vier-Augen, Zahlung, Scorecard, Bericht |
| `test_ui` | 17 | HTML-Maskierung, Farbtoken hell/dunkel, gültige Vega-Lite-Spezifikationen |
| `test_bpmn_tracker` | 16 | BPMN/eEPK, KPIs, Periodenvergleich |
| `test_integration` | 16 | alle 15 Musterdokumente Ende-zu-Ende |
| `test_exporter` | 13 | Excel-Formeln und Hash-Kette, Gegenprüfung, Power-BI-Schema, keine Bankdaten |

### Änderungen in 1.2.1 (Suite-Abgleich)

- USt-IdNrn. der sechs Lieferanten und der RheinMain Industrietechnik GmbH auf gültige Prüfziffern korrigiert
  (nur die letzte Ziffer), damit DataGuard-AI dieselben Stammdaten ohne Befund prüfen kann; Musterrechnungen mit
  Stichtag 28.10.2026 neu erzeugt (sonst unverändert).
- Neue Prüfung der deutschen USt-IdNr.-Prüfziffer in `einvoice_parser.py` (Abgleich mit python-stdnum:
  300.000 Zufallsnummern ohne Abweichung) und neun zusätzliche Tests.

---

## Hinweise

- Alle Unternehmen, Personen, Adressen, USt-IdNrn. und Bankdaten sind **frei erfunden**; die *RheinMain
  Industrietechnik GmbH* ist fiktiv. Kennzahlen zeigen die Methodik, keine realen Werte.
- FlowAudit-AI ist ein **Portfolio- und Demonstrationsprojekt** und ersetzt keine steuerliche oder rechtliche Prüfung.
- KI-Texte sind Entwürfe und werden erst nach namentlicher Freigabe verwendet.
- Die Demo-PDFs folgen der Factur-X-Spezifikation (XMP, `/AF`, OutputIntent), sind aber nicht mit veraPDF
  zertifiziert ([docs/validierung.md](docs/validierung.md)).
- SAP-Tabellen- und Feldnamen dienen der fachlichen Orientierung; es besteht keine Verbindung zu einem SAP-System.
- Es werden keine Secrets im Repository gespeichert.

---

## Lizenz & Autor

MIT-Lizenz – siehe [LICENSE](LICENSE). Mermaid (`static/mermaid.min.js`) steht unter der MIT-Lizenz
(`static/mermaid.LICENSE`), die Schriften Inter und JetBrains Mono unter der SIL Open Font License.

**Sherwan Shweish** · B.Sc. Digital Business Management, Hochschule RheinMain
Schwerpunkte: Datenanalyse, Controlling, Prozessmodellierung (BPMN, eEPK) · Python, SQL, Excel, Power BI, SAP S/4HANA
