"""Tests für src/zugferd_pdf.py – XML aus PDF/A-3 extrahieren, Sicherheit, Erzeugung, Pipeline-Anbindung."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from src.agent import PruefCode, WorkflowSchritt as WS, ermittle_soll_route
from src.einvoice_parser import EInvoiceFehler, lese_rechnung
from src.matching_engine import MatchingEngine, verarbeite_datei
from src.zugferd_pdf import (
    KeineEingebetteteRechnung,
    PDFSicherheitsFehler,
    erzeuge_zugferd_pdf,
    extrahiere_xml,
    ist_pdf,
)


def _pdf(demo, name):
    return demo.rechnungsordner / name


def _pdf_mit_anhang(name: str, inhalt: bytes) -> bytes:
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.add_attachment(name, inhalt)
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


class TestExtraktion:
    def test_factur_x_anhang_mit_metadaten(self, demo):
        anhang = extrahiere_xml(_pdf(demo, "13_zugferd_pdf_touchless.pdf"))
        assert anhang.name == "factur-x.xml"
        assert anhang.beziehung == "Alternative"
        assert anhang.profil == "EN 16931" and anhang.pdfa_teil == "3"
        assert anhang.xml.startswith(b"<?xml") and b"CrossIndustryInvoice" in anhang.xml
        assert anhang.beschreibung == "PDF/A-3 · factur-x.xml · Profil EN 16931"

    def test_pdf_struktur_nach_factur_x(self, demo):
        katalog = PdfReader(_pdf(demo, "13_zugferd_pdf_touchless.pdf")).trailer["/Root"]
        spec = katalog["/AF"][0].get_object()
        assert spec["/AFRelationship"] == "/Alternative"
        assert spec["/EF"]["/F"].get_object()["/Subtype"] == "/text/xml"
        assert "/OutputIntents" in katalog and "/Metadata" in katalog
        xmp = katalog["/Metadata"].get_object().get_data()
        assert b"urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#" in xmp and b"pdfaExtension" in xmp

    @pytest.mark.parametrize("name", ["zugferd-invoice.xml", "ZUGFeRD-invoice.xml", "xrechnung.xml"])
    def test_alternative_anhangnamen(self, demo, name):
        xml = (demo.rechnungsordner / "02_touchless_zugferd_cii.xml").read_bytes()
        assert extrahiere_xml(_pdf_mit_anhang(name, xml)).name == name

    def test_prioritaet_factur_x_vor_anderen_anhaengen(self, demo):
        xml = (demo.rechnungsordner / "02_touchless_zugferd_cii.xml").read_bytes()
        w = PdfWriter()
        w.add_blank_page(200, 200)
        w.add_attachment("lieferschein.xml", b"<lieferschein/>")
        w.add_attachment("factur-x.xml", xml)
        puffer = io.BytesIO()
        w.write(puffer)
        assert extrahiere_xml(puffer.getvalue()).name == "factur-x.xml"

    def test_pdf_ohne_xml_ist_keine_e_rechnung(self, demo):
        with pytest.raises(KeineEingebetteteRechnung, match="§ 14 Abs. 1 UStG"):
            extrahiere_xml(_pdf(demo, "15_pdf_ohne_xml.pdf"))

    def test_ist_pdf(self, demo):
        assert ist_pdf(_pdf(demo, "13_zugferd_pdf_touchless.pdf").read_bytes())
        assert not ist_pdf(b"<?xml version='1.0'?><x/>")


class TestSicherheit:
    def test_javascript_wird_abgelehnt(self):
        with pytest.raises(PDFSicherheitsFehler, match="JavaScript"):
            extrahiere_xml(b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj")

    def test_launch_aktion_wird_abgelehnt(self):
        with pytest.raises(PDFSicherheitsFehler, match="Launch"):
            extrahiere_xml(b"%PDF-1.7\n1 0 obj << /S /Launch /F (calc.exe) >> endobj")

    def test_verschluesseltes_pdf_wird_abgelehnt(self, demo):
        w = PdfWriter(clone_from=_pdf(demo, "13_zugferd_pdf_touchless.pdf"))
        w.encrypt("geheim")
        puffer = io.BytesIO()
        w.write(puffer)
        with pytest.raises(PDFSicherheitsFehler, match="Verschlüsselte"):
            extrahiere_xml(puffer.getvalue())

    def test_xxe_im_eingebetteten_xml_wird_abgelehnt(self):
        boese = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        with pytest.raises(EInvoiceFehler, match="DOCTYPE"):
            lese_rechnung(_pdf_mit_anhang("factur-x.xml", boese))

    def test_kein_pdf(self):
        with pytest.raises(EInvoiceFehler, match="kein PDF"):
            extrahiere_xml(b"hallo welt")

    def test_beschaedigtes_pdf(self):
        with pytest.raises(EInvoiceFehler):
            extrahiere_xml(b"%PDF-1.7\n" + b"\x00garbage" * 50)


class TestPipeline:
    def test_lese_rechnung_erkennt_pdf_automatisch(self, demo):
        r = lese_rechnung(_pdf(demo, "13_zugferd_pdf_touchless.pdf"))
        assert r.format == "ZUGFeRD" and r.rechnungsnummer == "RLV-2026-0512"
        assert r.container == "PDF/A-3 · factur-x.xml · Profil EN 16931"
        assert r.quelle == "13_zugferd_pdf_touchless.pdf" and len(r.sha256) == 64

    def test_pdf_touchless(self, demo):
        e = verarbeite_datei(_pdf(demo, "13_zugferd_pdf_touchless.pdf"), MatchingEngine(demo.db_pfad))
        assert e.schritt is WS.TOUCHLESS_FREIGABE

    def test_pdf_mengenabweichung(self, demo):
        e = verarbeite_datei(_pdf(demo, "14_zugferd_pdf_mengenabweichung.pdf"), MatchingEngine(demo.db_pfad))
        assert e.schritt is WS.KLAERUNG_EINKAUF_MENGE
        (b,) = [p for p in e.fakten.fehler if p.code is PruefCode.MENGENABWEICHUNG]
        assert (b.soll, b.ist) == (100, 120)

    def test_reines_pdf_wird_abgewiesen(self, demo):
        e = verarbeite_datei(_pdf(demo, "15_pdf_ohne_xml.pdf"), MatchingEngine(demo.db_pfad))
        assert e.abgelehnt and "KeineEingebetteteRechnung" in e.fehler

    def test_xml_und_pdf_derselben_rechnung_ergeben_gleiche_fakten(self, demo):
        xml = (demo.rechnungsordner / "02_touchless_zugferd_cii.xml").read_bytes()
        r_xml = lese_rechnung(xml)
        r_pdf = lese_rechnung(erzeuge_zugferd_pdf(xml, r_xml))
        engine = MatchingEngine(demo.db_pfad)
        f_xml, f_pdf = engine.pruefe(r_xml), engine.pruefe(r_pdf)
        assert f_xml.eingabe_hash() == f_pdf.eingabe_hash()
        assert ermittle_soll_route(f_pdf)[0] is WS.TOUCHLESS_FREIGABE

    def test_rechnungsbild_ist_lesbar(self, demo):
        text = PdfReader(_pdf(demo, "14_zugferd_pdf_mengenabweichung.pdf")).pages[0].extract_text()
        assert "Rechnung ODW-55160" in text and "4500000114" in text and "factur-x.xml" in text
