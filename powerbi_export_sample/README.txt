FlowAudit-AI – Power-BI-Paket (synthetische Demo-Daten, Stichtag 28.10.2026)
==================================================================================

Import in Power BI Desktop
  1. Daten abrufen > Text/CSV > Datei wählen
  2. Dateiursprung 65001 (UTF-8), Trennzeichen Semikolon
  3. "Daten transformieren" > Datentyp mit Gebietsschema > Deutsch (Deutschland)
     (oder Skript laden.pq im Erweiterten Editor verwenden)

Sternschema (Beziehungen, Kardinalität n:1, Filterrichtung einfach)
  fact_rechnung[LIFNR]             -> dim_lieferant[LIFNR]
  fact_rechnung[WORKFLOW_SCHRITT]  -> dim_workflow_schritt[WORKFLOW_SCHRITT]
  fact_rechnung[EINGANG_DATUM_ID]  -> dim_datum[DATUM_ID]        (aktiv)
  fact_rechnung[RECHNUNGSDATUM_ID] -> dim_datum[DATUM_ID]        (inaktiv, USERELATIONSHIP)
  fact_pruefung[RECHNUNG_ID]       -> fact_rechnung[RECHNUNG_ID]
  fact_pruefung[PRUEFCODE]         -> dim_pruefcode[PRUEFCODE]

  dim_datum als Datumstabelle markieren (Spalte DATUM).

Measures: siehe measures.dax · Integrität: SHA-256 je Datei in manifest.json
Zahlenformat: Dezimalkomma, kein Tausendertrennzeichen · Datum TT.MM.JJJJ · Bankdaten sind nicht enthalten.
