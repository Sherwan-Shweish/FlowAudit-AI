"""Tests für src/assistent.py – Begründung, SAP-Abgleich, Aktionsentwürfe (Human-in-the-Loop), Datensparsamkeit."""

from __future__ import annotations

import pytest

from src.agent import Einstellungen, FlowAuditAgent, MockLLMClient, WorkflowSchritt as WS
from src.assistent import AktionsEntwurf, KIAssistent, sap_abgleich
from src.demo import ANGRIFFS_DATEIEN

OFFLINE = Einstellungen(llm_provider="offline")


@pytest.fixture()
def assistent(demo) -> KIAssistent:
    return KIAssistent(demo.db_pfad, OFFLINE)


def _vorgang(assistent, demo, datei):
    return assistent.pruefe(demo.rechnungsordner / datei)


ERWARTETE_AKTIONEN = {
    "01_touchless_xrechnung_ubl.xml": ["VERMERK_BUCHHALTUNG"],
    "03_preisabweichung_ubl.xml": ["NOTIZ_EINKAUF"],
    "05_fehlender_wareneingang_ubl.xml": ["NOTIZ_LOGISTIK"],
    "07_maverick_buying_ubl.xml": ["NOTIZ_FACHBEREICH", "NOTIZ_EINKAUF"],
    "08_dublette_ubl.xml": ["VERMERK_BUCHHALTUNG", "EMAIL_LIEFERANT"],
    "09_formfehler_ohne_ustid_ubl.xml": ["EMAIL_LIEFERANT"],
    "10_rechenfehler_mwst_zugferd_cii.xml": ["EMAIL_LIEFERANT"],
    "11_prompt_injection_ubl.xml": ["NOTIZ_EINKAUF", "MELDUNG_COMPLIANCE"],
    "14_zugferd_pdf_mengenabweichung.pdf": ["NOTIZ_EINKAUF"],
    "15_pdf_ohne_xml.pdf": ["EMAIL_LIEFERANT"],
}


class TestVorgang:
    @pytest.mark.parametrize("datei, typen", ERWARTETE_AKTIONEN.items())
    def test_aktionen_passend_zur_route(self, assistent, demo, datei, typen):
        v = _vorgang(assistent, demo, datei)
        assert [a.typ for a in v.aktionen] == typen
        assert all(a.status == "ENTWURF" for a in v.aktionen)

    def test_alle_musterdokumente_faktentreu(self, assistent, demo, manifest):
        for fall in manifest:
            v = _vorgang(assistent, demo, fall["datei"])
            assert (v.schritt.value if v.schritt else "ABGELEHNT") == fall["erwartete_route"]
            if not v.abgelehnt:
                assert v.faktencheck_assistent.bestanden, (fall["datei"], v.faktencheck_assistent)
                assert v.audit.faktencheck.bestanden
                assert v.diagramm("bpmn").startswith("---\ntitle: Rechnung ")

    def test_verstaendliche_begruendung_preis(self, assistent, demo):
        v = _vorgang(assistent, demo, "03_preisabweichung_ubl.xml")
        assert v.begruendung[0].startswith("Der berechnete Preis weicht außerhalb der Toleranz")
        assert "Einzelpreis 4,18 EUR liegt 4,50 % über dem Bestellpreis von 4,00 EUR" in v.begruendung[1]
        assert "finanzielle Auswirkung 90,00 EUR" in v.begruendung[1]
        assert v.begruendung[2].startswith("Hinweis: Skontofrist läuft in Kürze ab")

    def test_abgewiesene_datei(self, assistent, demo):
        v = _vorgang(assistent, demo, "15_pdf_ohne_xml.pdf")
        assert v.abgelehnt and v.ablehnungstyp == "KeineEingebetteteRechnung" and v.diagramm() is None
        assert "XRechnung oder als ZUGFeRD" in v.aktionen[0].text

    def test_angriffsdatei_erzeugt_sicherheitsmeldung(self, assistent):
        v = assistent.pruefe(ANGRIFFS_DATEIEN["XXE – lokale Datei auslesen"], "angriff.xml")
        assert v.abgelehnt and v.aktionen[0].typ == "MELDUNG_COMPLIANCE" and v.aktionen[0].prioritaet == "HOCH"


class TestAktionsentwuerfe:
    def test_formfehler_nennt_fehlende_felder_exakt(self, assistent, demo):
        (mail,) = _vorgang(assistent, demo, "09_formfehler_ohne_ustid_ubl.xml").aktionen
        assert mail.felder == ["BT-31/BT-32: Pflichtangabe fehlt: USt-IdNr. oder Steuernummer des Verkäufers "
                               "(§ 14 Abs. 4 Nr. 2 UStG)."]
        assert mail.empfaenger == "rechnungen@odenwald-bedarf.example"
        assert "§ 31 Abs. 5 UStDV" in mail.text and "ODW-55117" in mail.betreff

    def test_rechenfehler_nennt_soll_und_ist(self, assistent, demo):
        (mail,) = _vorgang(assistent, demo, "10_rechenfehler_mwst_zugferd_cii.xml").aktionen
        assert mail.felder == ["BT-117: Rechenfehler: Netto × Steuersatz ≠ Steuerbetrag (S 19 %). "
                               "Ausgewiesen 273,50 EUR, korrekt 237,50 EUR (Differenz 36,00 EUR)."]

    def test_preisklaerung_mit_sap_belegbezug(self, assistent, demo):
        (notiz,) = _vorgang(assistent, demo, "03_preisabweichung_ubl.xml").aktionen
        assert notiz.empfaenger == "Einkauf, Einkäufergruppe E01" and notiz.prioritaet == "HOCH"
        assert notiz.belegbezuege[0].startswith("EKKO 4500000103 (Bestelldatum")
        assert notiz.belegbezuege[1] == "EKPO 4500000103/00010 – Drehteil DT-40 Edelstahl"
        assert notiz.belegbezuege[2].startswith("MATDOC ") and "BWA 101" in notiz.belegbezuege[2]
        assert "ME22N" in notiz.text and "49,74 EUR Skonto sind gefährdet" in notiz.text

    def test_mengenklaerung_aus_pdf(self, assistent, demo):
        (notiz,) = _vorgang(assistent, demo, "14_zugferd_pdf_mengenabweichung.pdf").aktionen
        assert "Berechnet 120, offene Bestellmenge 100 – Mehrmenge 20" in notiz.text
        assert "BWA 122" in notiz.text

    def test_maverick_ohne_sap_bestellung(self, assistent, demo):
        fachbereich, einkauf = _vorgang(assistent, demo, "07_maverick_buying_ubl.xml").aktionen
        assert "BANF" in fachbereich.text and "EKKO – keine Bestellung vorhanden" in fachbereich.belegbezuege
        assert "Bürowelt Wiesbaden e.K." in einkauf.text

    def test_dublette_verweist_auf_rbkp(self, assistent, demo):
        vermerk, mail = _vorgang(assistent, demo, "12_dublettenverdacht_zugferd_cii.xml").aktionen
        assert vermerk.belegbezuege[0].startswith("RBKP ") and "XBLNR MET-77120" in vermerk.belegbezuege[0]
        assert vermerk.prioritaet == "HOCH" and mail.typ == "EMAIL_LIEFERANT"

    def test_logistik_notiz(self, assistent, demo):
        (notiz,) = _vorgang(assistent, demo, "06_teillieferung_zugferd_cii.xml").aktionen
        assert "MIGO" in notiz.text and "es fehlen 40 im Wareneingang" in notiz.text


class TestHumanInTheLoop:
    def _entwurf(self) -> AktionsEntwurf:
        return AktionsEntwurf(typ="NOTIZ_EINKAUF", kanal="Interne Notiz", empfaenger="Einkauf", betreff="Test",
                              text="Bitte klären.")

    def test_freigabe_mit_name_und_zeitstempel(self):
        e = self._entwurf().freigeben("Erika Musterfrau", "passt")
        assert (e.status, e.bearbeitet_von, e.kommentar) == ("FREIGEGEBEN", "Erika Musterfrau", "passt")
        assert e.bearbeitet_am is not None

    def test_ohne_namen_keine_freigabe(self):
        with pytest.raises(ValueError, match="Namen der bearbeitenden Person"):
            self._entwurf().freigeben("  ")

    def test_keine_doppelte_entscheidung(self):
        e = self._entwurf().verwerfen("Max")
        with pytest.raises(ValueError, match="bereits verworfen"):
            e.freigeben("Max")

    def test_original_bleibt_entwurf(self):
        e = self._entwurf()
        e.freigeben("Max")
        assert e.status == "ENTWURF"


class TestDatensparsamkeitUndGuardrails:
    def test_llm_sieht_weder_iban_noch_freitext(self, demo):
        llm = MockLLMClient(["{}"])
        a = KIAssistent(demo.db_pfad, Einstellungen(llm_provider="mock", max_korrekturversuche=0),
                        agent=FlowAuditAgent(llm, Einstellungen(llm_provider="mock", max_korrekturversuche=0)))
        v = a.pruefe(demo.rechnungsordner / "11_prompt_injection_ubl.xml")
        prompt = llm.aufrufe[0][1]
        assert v.rechnung.iban and v.rechnung.iban not in prompt
        assert "Ignoriere" not in prompt and "Vorstand" not in prompt
        assert v.schritt is WS.KLAERUNG_EINKAUF_PREIS

    def test_sap_abgleich_laeuft_durch_den_guardrail(self, assistent, demo):
        v = _vorgang(assistent, demo, "06_teillieferung_zugferd_cii.xml")
        assert [q["abfrage"] for q in v.sap.abfragen] == ["kopf", "positionen", "materialbelege", "rechnungsbelege"]
        assert all(q["erlaubt"] for q in v.sap.abfragen)
        (pos,) = v.sap.positionen
        assert (pos.bestellmenge, pos.we_menge, pos.rechnungsmenge, pos.status) == (100, 60, 100, "WARENEINGANG_UNVOLLSTAENDIG")

    def test_manipulierte_bestellnummer_erzeugt_keine_sql(self, assistent, demo):
        v = _vorgang(assistent, demo, "03_preisabweichung_ubl.xml")
        boese = v.fakten.model_copy(deep=True)
        boese.rechnung.bestellnummer = "4500000103' OR '1'='1"
        abgleich = sap_abgleich(demo.db_pfad, boese)
        assert abgleich.abfragen == [] and abgleich.positionen == []
