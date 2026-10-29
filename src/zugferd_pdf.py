"""
FlowAudit-AI · ZUGFeRD / Factur-X: XML aus PDF/A-3 extrahieren und erzeugen
==========================================================================

ZUGFeRD 2.x bzw. Factur-X ist ein *hybrides* Format: ein menschenlesbares
PDF/A-3 mit eingebetteter CII-XML-Rechnung (``factur-x.xml``, bei älteren
Versionen ``zugferd-invoice.xml``, bei XRechnung-in-PDF ``xrechnung.xml``).
Rechtlich maßgeblich ist der strukturierte XML-Teil – genau den liest dieses
Modul aus und übergibt ihn an ``einvoice_parser``.

Sicherheit beim Einlesen:

* Größenlimit, Prüfung des PDF-Headers,
* Ablehnung verschlüsselter PDFs und von PDFs mit aktiven Inhalten
  (``/JavaScript``, ``/JS``, ``/Launch``, ``/SubmitForm``),
* das PDF wird nie gerendert – es wird nur der Dateianhang gelesen, der
  anschließend dieselbe XXE-geschützte XML-Prüfung durchläuft wie jede XML-Datei.

Zum Erzeugen der Demo-PDFs (``erzeuge_zugferd_pdf``) wird das sichtbare
Rechnungsbild mit *fpdf2* gesetzt und die XML mit *pypdf* als Associated File
(``/AFRelationship /Alternative``) samt Factur-X-XMP-Metadaten, PDF/A-3-
Kennung und sRGB-OutputIntent eingebettet.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .einvoice_parser import MAX_DATEIGROESSE, EInvoiceFehler, XMLSicherheitsFehler

__all__ = [
    "ANHANG_NAMEN",
    "ZugferdAnhang",
    "PDFSicherheitsFehler",
    "KeineEingebetteteRechnung",
    "ist_pdf",
    "extrahiere_xml",
    "erzeuge_zugferd_pdf",
]

#: Zulässige Namen des Rechnungsanhangs – in dieser Priorität.
ANHANG_NAMEN = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml")
MAX_PDF_GROESSE = 20 * 1024 * 1024
_AKTIVE_INHALTE = re.compile(rb"/(?:JavaScript|JS|Launch|SubmitForm|ImportData)\b")


class PDFSicherheitsFehler(XMLSicherheitsFehler):
    """PDF ist verschlüsselt, zu groß oder enthält aktive Inhalte."""


class KeineEingebetteteRechnung(EInvoiceFehler):
    """PDF ohne strukturierte Rechnung – also keine E-Rechnung im Sinne von § 14 UStG."""


@dataclass(frozen=True)
class ZugferdAnhang:
    name: str
    xml: bytes
    beziehung: Optional[str] = None       # /AFRelationship, z. B. "Alternative"
    profil: Optional[str] = None          # fx:ConformanceLevel aus den XMP-Metadaten
    pdfa_teil: Optional[str] = None       # pdfaid:part (3 = PDF/A-3)

    @property
    def beschreibung(self) -> str:
        teile = [f"PDF/A-{self.pdfa_teil}" if self.pdfa_teil else "PDF", self.name]
        if self.profil:
            teile.append(f"Profil {self.profil}")
        return " · ".join(teile)


def ist_pdf(daten: bytes) -> bool:
    return b"%PDF-" in daten[:1024]


def _laden(quelle: Path | str | bytes) -> bytes:
    if isinstance(quelle, bytes):
        daten = quelle
    else:
        pfad = Path(quelle)
        if pfad.stat().st_size > MAX_PDF_GROESSE:
            raise PDFSicherheitsFehler("PDF überschreitet das Größenlimit von 20 MB.")
        daten = pfad.read_bytes()
    if len(daten) > MAX_PDF_GROESSE:
        raise PDFSicherheitsFehler("PDF überschreitet das Größenlimit von 20 MB.")
    if not ist_pdf(daten):
        raise EInvoiceFehler("Datei ist kein PDF (Header %PDF- fehlt).")
    return daten


def extrahiere_xml(quelle: Path | str | bytes) -> ZugferdAnhang:
    """Liefert die eingebettete Rechnungs-XML eines ZUGFeRD-/Factur-X-PDFs."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    daten = _laden(quelle)
    if m := _AKTIVE_INHALTE.search(daten):
        raise PDFSicherheitsFehler(
            f"PDF enthält aktive Inhalte ({m.group(0).decode()}) – Verarbeitung aus Sicherheitsgründen abgelehnt."
        )
    try:
        leser = PdfReader(io.BytesIO(daten), strict=False)
        if leser.is_encrypted:
            raise PDFSicherheitsFehler("Verschlüsselte PDFs werden nicht verarbeitet.")
        anhaenge = {name: inhalt for name, inhalt in leser.attachments.items()}
        katalog = leser.trailer["/Root"]
    except PdfReadError as exc:
        raise EInvoiceFehler(f"PDF ist beschädigt oder nicht lesbar: {exc}") from exc

    namen_klein = {n.lower(): n for n in anhaenge}
    gewaehlt = next((namen_klein[n] for n in ANHANG_NAMEN if n in namen_klein), None)
    if gewaehlt is None:
        gewaehlt = next((n for n in anhaenge if n.lower().endswith(".xml")), None)
    if gewaehlt is None:
        raise KeineEingebetteteRechnung(
            "PDF ohne eingebettete strukturierte Rechnung (kein factur-x.xml/zugferd-invoice.xml/xrechnung.xml). "
            "Ein reines PDF ist keine E-Rechnung im Sinne von § 14 Abs. 1 UStG – bitte beim Lieferanten eine "
            "XRechnung oder ZUGFeRD-Rechnung anfordern."
        )
    xml = anhaenge[gewaehlt][0]
    if len(xml) > MAX_DATEIGROESSE:
        raise PDFSicherheitsFehler("Eingebettete XML überschreitet das Größenlimit.")

    beziehung = None
    for ref in katalog.get("/AF", []) or []:
        spec = ref.get_object()
        if spec.get("/UF", spec.get("/F")) == gewaehlt and "/AFRelationship" in spec:
            beziehung = str(spec["/AFRelationship"]).lstrip("/")

    profil = pdfa = None
    if "/Metadata" in katalog:
        try:
            xmp = katalog["/Metadata"].get_object().get_data()
            if m := re.search(rb"<fx:ConformanceLevel>([^<]{1,40})</fx:ConformanceLevel>", xmp):
                profil = m.group(1).decode("utf-8", "replace").strip()
            if m := re.search(rb"<pdfaid:part>(\d)</pdfaid:part>", xmp):
                pdfa = m.group(1).decode()
        except Exception:  # Metadaten sind optional – Fehler dort blockieren nicht
            pass
    return ZugferdAnhang(gewaehlt, xml, beziehung, profil, pdfa)


# ═════════════════════════════════════════════════════════════════════════════
# Erzeugung (Demo-Daten)
# ═════════════════════════════════════════════════════════════════════════════

def _latin1(text: object) -> str:
    t = str(text)
    for alt, neu in {"–": "-", "—": "-", "„": '"', "“": '"', "”": '"', "€": "EUR", "…": "...", "·": "-"}.items():
        t = t.replace(alt, neu)
    return t.encode("latin-1", "replace").decode("latin-1")


def _de(x: Optional[Decimal], stellen: int = 2) -> str:
    if x is None:
        return "-"
    roh = f"{x:,.{stellen}f}"
    return roh.replace(",", "§").replace(".", ",").replace("§", ".")


def _rechnungsbild(r, ohne_xml_hinweis: bool = False) -> bytes:
    """Setzt das sichtbare Rechnungsbild (A4) aus einer GeparsteRechnung."""
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_title(_latin1(f"Rechnung {r.rechnungsnummer}"))
    pdf.set_author(_latin1(r.verkaeufer.name or ""))
    pdf.set_creator("FlowAudit-AI Demo-Generator")
    pdf.add_page()
    v, k = r.verkaeufer, r.kaeufer

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, _latin1(v.name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _latin1(f"{v.adresse.strasse} - {v.adresse.plz} {v.adresse.ort} - USt-IdNr. {v.ust_id or '-'}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 10)
    for zeile in (k.name, k.adresse.strasse, f"{k.adresse.plz} {k.adresse.ort}"):
        pdf.cell(0, 5, _latin1(zeile), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, _latin1(f"Rechnung {r.rechnungsnummer}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    meta = [
        ("Rechnungsdatum", r.rechnungsdatum.strftime("%d.%m.%Y") if r.rechnungsdatum else "-"),
        ("Leistungsdatum", r.leistungsdatum.strftime("%d.%m.%Y") if r.leistungsdatum else "-"),
        ("Ihre Bestellung", r.bestellnummer or "-"),
        ("Käuferreferenz", r.kaeuferreferenz or "-"),
    ]
    for bez, wert in meta:
        pdf.cell(40, 5, _latin1(bez + ":"))
        pdf.cell(0, 5, _latin1(wert), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    breiten = (12, 78, 22, 14, 28, 30)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(235, 240, 248)
    for text, b in zip(("Pos.", "Bezeichnung", "Menge", "Einh.", "Einzelpreis", "Betrag netto"), breiten):
        pdf.cell(b, 7, _latin1(text), border=1, fill=True, align="R" if text not in ("Bezeichnung",) else "L")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for p in r.positionen:
        werte = (p.id, (p.bezeichnung or "")[:48], _de(p.menge, 0), p.einheit, _de(p.preis), _de(p.nettobetrag))
        for i, (text, b) in enumerate(zip(werte, breiten)):
            pdf.cell(b, 6, _latin1(text), border=1, align="L" if i == 1 else "R")
        pdf.ln()
    pdf.ln(3)
    s = r.summen
    zeilen = [("Summe netto", s.netto)]
    zeilen += [(f"Umsatzsteuer {st.satz:.0f} % auf {_de(st.basis)}", st.betrag) for st in r.steuern]
    zeilen += [("Rechnungsbetrag (EUR)", s.brutto)]
    for i, (bez, wert) in enumerate(zeilen):
        pdf.set_font("Helvetica", "B" if i == len(zeilen) - 1 else "", 10)
        pdf.cell(sum(breiten) - 30, 6, _latin1(bez), align="R")
        pdf.cell(30, 6, _latin1(_de(wert)), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9)
    bedingungen = (r.zahlungsbedingungen or "").split("#SKONTO#")[0].strip()
    if r.skonto:
        bedingungen += f" Bei Zahlung innerhalb von {r.skonto.tage} Tagen {_de(r.skonto.prozent)} % Skonto."
    pdf.multi_cell(0, 5, _latin1(bedingungen.strip()), new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 5, _latin1(f"Bankverbindung: IBAN {r.iban or '-'}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    hinweis = ("Dieses Dokument ist ein reines PDF ohne strukturierte Rechnungsdaten."
               if ohne_xml_hinweis else
               "Hybride E-Rechnung nach ZUGFeRD 2.x / Factur-X (EN 16931): Die rechtlich maßgebliche XML-Rechnung "
               "ist als Anhang factur-x.xml in diesem PDF/A-3 eingebettet.")
    pdf.multi_cell(0, 4, _latin1(hinweis + " - Synthetische Demo-Daten (FlowAudit-AI)."))
    return bytes(pdf.output())


def _xmp(titel: str, ersteller: str, jetzt: str, anhang: str, profil: str) -> bytes:
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{titel}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{ersteller}</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>FlowAudit-AI Demo-Generator</xmp:CreatorTool>
   <xmp:CreateDate>{jetzt}</xmp:CreateDate>
   <xmp:ModifyDate>{jetzt}</xmp:ModifyDate>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
   <pdf:Producer>FlowAudit-AI (fpdf2 + pypdf)</pdf:Producer>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">
   <fx:DocumentType>INVOICE</fx:DocumentType>
   <fx:DocumentFileName>{anhang}</fx:DocumentFileName>
   <fx:Version>1.0</fx:Version>
   <fx:ConformanceLevel>{profil}</fx:ConformanceLevel>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
      xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#" xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
   <pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource">
    <pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
    <pdfaSchema:namespaceURI>urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#</pdfaSchema:namespaceURI>
    <pdfaSchema:prefix>fx</pdfaSchema:prefix>
    <pdfaSchema:property><rdf:Seq>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentFileName</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Name of the embedded XML invoice file</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentType</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>INVOICE</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>Version</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Version of the Factur-X XML schema</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>ConformanceLevel</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Conformance level of the embedded XML</pdfaProperty:description></rdf:li>
    </rdf:Seq></pdfaSchema:property>
   </rdf:li></rdf:Bag></pdfaExtension:schemas>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""".encode("utf-8")


def erzeuge_zugferd_pdf(
    xml: Optional[bytes],
    rechnung,
    anhang_name: str = "factur-x.xml",
    profil: str = "EN 16931",
) -> bytes:
    """Erzeugt ein hybrides ZUGFeRD-/Factur-X-PDF (oder mit ``xml=None`` ein reines Bild-PDF)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    bild = _rechnungsbild(rechnung, ohne_xml_hinweis=xml is None)
    if xml is None:
        return bild

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(bild)))
    datei = writer.add_attachment(anhang_name, xml)
    datei.subtype = NameObject("/text/xml")  # wird als /text#2Fxml geschrieben
    datei.description = TextStringObject("Factur-X/ZUGFeRD Invoice")
    datei.associated_file_relationship = NameObject("/Alternative")
    jetzt = datetime.now(timezone.utc)
    datei.modification_date = jetzt

    katalog = writer._root_object
    katalog[NameObject("/AF")] = ArrayObject([datei.pdf_object.indirect_reference or writer._add_object(datei.pdf_object)])

    titel = _latin1(f"Rechnung {rechnung.rechnungsnummer}")
    meta = DecodedStreamObject()
    meta.set_data(_xmp(titel, _latin1(rechnung.verkaeufer.name or ""), jetzt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       anhang_name, profil))
    meta.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    katalog[NameObject("/Metadata")] = writer._add_object(meta)

    try:  # sRGB-OutputIntent (PDF/A verlangt ein Ausgabeprofil)
        from PIL import ImageCms

        icc = DecodedStreamObject()
        icc.set_data(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        icc[NameObject("/N")] = NumberObject(3)
        intent = DictionaryObject({
            NameObject("/Type"): NameObject("/OutputIntent"),
            NameObject("/S"): NameObject("/GTS_PDFA1"),
            NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB"),
            NameObject("/DestOutputProfile"): writer._add_object(icc),
        })
        katalog[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(intent)])
    except ImportError:  # pragma: no cover
        pass
    writer.add_metadata({"/Title": titel, "/Author": _latin1(rechnung.verkaeufer.name or ""),
                         "/Creator": "FlowAudit-AI Demo-Generator"})
    puffer = io.BytesIO()
    writer.write(puffer)
    return puffer.getvalue()
