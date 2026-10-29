# Offizielle Validierung der Musterrechnungen

Die 15 Musterdokumente aus `src/seed_data.py` wurden mit `tools/offizielle_validierung.py` gegen die öffentlichen Regelwerke geprüft. Bei den ZUGFeRD-PDFs wird die eingebettete `factur-x.xml` zuvor mit `src/zugferd_pdf.py` extrahiert.

| Regelwerk | Quelle | Stand |
|---|---|---|
| EN 16931 Schematron (UBL & CII) | [ConnectingEurope/eInvoicing-EN16931](https://github.com/ConnectingEurope/eInvoicing-EN16931) | Commit `b17b269` (26.10.2026) |
| XRechnung-CIUS Schematron (BR-DE-*) | [itplr-kosit/xrechnung-schematron](https://github.com/itplr-kosit/xrechnung-schematron) | Commit `7e3ddbe` (04.09.2026) |
| UN/CEFACT CII D16B XSD | aus dem CEN-Repository | – |

| Datei | Syntax | CII-XSD | EN 16931 (CEN) | XRechnung (KoSIT) |
|---|---|---|---|---|
| `01_touchless_xrechnung_ubl.xml` | UBL | – | ✅ | ✅ |
| `02_touchless_zugferd_cii.xml` | CII | ✅ | ✅ | ⚠️ BR-DE-21 |
| `03_preisabweichung_ubl.xml` | UBL | – | ✅ | ✅ |
| `04_preis_in_toleranz_xrechnung_cii.xml` | CII | ✅ | ✅ | ✅ |
| `05_fehlender_wareneingang_ubl.xml` | UBL | – | ✅ | ✅ |
| `06_teillieferung_zugferd_cii.xml` | CII | ✅ | ✅ | ⚠️ BR-DE-21 |
| `07_maverick_buying_ubl.xml` | UBL | – | ✅ | ✅ |
| `08_dublette_ubl.xml` | UBL | – | ✅ | ✅ |
| `09_formfehler_ohne_ustid_ubl.xml` | UBL | – | ❌ BR-S-02, ❌ BR-CO-26 | ❌ BR-DE-16 |
| `10_rechenfehler_mwst_zugferd_cii.xml` | CII | ✅ | ❌ BR-CO-17, ❌ BR-S-09 | ⚠️ BR-DE-21 |
| `11_prompt_injection_ubl.xml` | UBL | – | ✅ | ✅ |
| `12_dublettenverdacht_zugferd_cii.xml` | CII | ✅ | ✅ | ⚠️ BR-DE-21 |
| `13_zugferd_pdf_touchless.pdf` | CII (aus PDF) | ✅ | ✅ | ⚠️ BR-DE-21 |
| `14_zugferd_pdf_mengenabweichung.pdf` | CII (aus PDF) | ✅ | ✅ | ⚠️ BR-DE-21 |
| `15_pdf_ohne_xml.pdf` | PDF | – | ❌ keine XML | ❌ keine XML |

Das Ergebnis liegt zusätzlich maschinenlesbar in `docs/validierung.json` und wird im Cockpit (Tab „Live-Rechnungsprüfung“) je Rechnung angezeigt.

## Einordnung

- **Alle fachlich korrekten Rechnungen bestehen die EN-16931-Regeln** – auch die beiden aus den ZUGFeRD-PDFs extrahierten XML-Dateien. Die XRechnung-Dateien bestehen zusätzlich die KoSIT-Regeln.
- **Die beiden Formfehler-Fälle scheitern genau an den beabsichtigten Stellen** – dieselben Befunde erkennt auch `einvoice_parser.py`:
  - `09` – fehlende USt-IdNr./Steuernummer: BR-CO-26, BR-S-02, BR-DE-16 ↔ `PFLICHTFELD_FEHLT` (BT-31/BT-32)
  - `10` – Zahlendreher in der Umsatzsteuer (273,50 € statt 237,50 €): BR-CO-17, BR-S-09 ↔ `SUMMENFEHLER` (BT-117)
- **`15` ist bewusst keine E-Rechnung:** ein reines PDF ohne strukturierte Daten. FlowAudit-AI weist es ab und erzeugt einen E-Mail-Entwurf mit der Bitte um XRechnung/ZUGFeRD.
- **BR-DE-21 (Warnung)** betrifft nur die ZUGFeRD-Dateien: Sie tragen bewusst die EN-16931-Kennung statt der XRechnung-Kennung. Für ZUGFeRD im B2B-Verkehr ist das korrekt.

## Grenzen

- Eine **UBL-XSD-Prüfung** ist nicht enthalten, weil das OASIS-Schema nicht im CEN-Repository liegt. Die volle Prüfung inkl. XSD bietet der [KoSIT-Validator](https://github.com/itplr-kosit/validator).
- Die **PDF/A-3-Konformität** der Demo-PDFs (XMP-Metadaten, Factur-X-Erweiterungsschema, sRGB-OutputIntent, `/AF`-Verknüpfung) ist nach der Factur-X-Spezifikation aufgebaut, wurde aber **nicht mit veraPDF geprüft**. Für den Produktiveinsatz würde man die PDFs mit einer zertifizierten Bibliothek erzeugen; zum *Einlesen* echter ZUGFeRD-PDFs ist das unerheblich.
- Die Prüfung ist ein Entwicklungswerkzeug. Die Laufzeitprüfung in FlowAudit-AI ist bewusst eigenständig, deterministisch und ohne Java/XSLT.
