// FlowAudit-AI – Power Query (M): CSV mit deutscher Kultur laden
// Erweiterter Editor: Pfad anpassen; je Tabelle eine Abfrage (Dateiname austauschen).
let
    Ordner = "C:\\FlowAudit\\powerbi\\",
    Quelle = Csv.Document(File.Contents(Ordner & "fact_rechnung.csv"),
        [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Kopf = Table.PromoteHeaders(Quelle, [PromoteAllScalars = true]),
    Typen = Table.TransformColumnTypes(Kopf, {
        {"GJAHR", Int64.Type}, {"RECHNUNGSDATUM_ID", Int64.Type}, {"EINGANG_DATUM_ID", Int64.Type},
        {"EINGANG", type datetime}, {"ABSCHLUSS", type datetime}, {"DURCHLAUFZEIT_H", type number},
        {"NETTO", Currency.Type}, {"STEUER", Currency.Type}, {"BRUTTO", Currency.Type},
        {"SKONTO_PROZ", type number}, {"SKONTO_BETRAG", Currency.Type}, {"TOUCHLESS", Int64.Type}
    }, "de-DE")
in
    Typen
