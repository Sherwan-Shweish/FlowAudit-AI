"""Tests für src/agent.py – SQL-Guardrail, Pydantic-Schemata, Faktenprüfung, Agent (Mock-LLM)."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.agent import (
    AuditBericht,
    AuditFakten,
    Einstellungen,
    FaktenPruefer,
    FlowAuditAgent,
    KonfigurationsFehler,
    MockLLMClient,
    PruefCode,
    ReadOnlySQLGuard,
    SQLGuardrailVerletzung,
    WorkflowSchritt as WS,
    erstelle_llm_client,
    erstelle_regelbasierten_bericht,
    ermittle_soll_route,
    extrahiere_json,
    fmt_de,
    ist_gueltiger_bpmn_pfad,
    pruefe_konsistenz,
)

from .conftest import llm_json

EINST = Einstellungen(llm_provider="mock", max_korrekturversuche=2)


# ─────────────────────────────────────────────────────────────────────────────
# 1 · SQL-Guardrail
# ─────────────────────────────────────────────────────────────────────────────

class TestSQLGuardrail:
    """Die Tests laufen gegen die SAP-Demodatenbank aus src/seed_data.py."""

    def guard(self, db_pfad, **kw) -> ReadOnlySQLGuard:
        return ReadOnlySQLGuard(db_pfad, **kw)

    def test_gueltiges_select(self, db_pfad):
        zeilen, abgeschnitten = self.guard(db_pfad).ausfuehren(
            "SELECT XBLNR, RMWWR FROM RBKP WHERE XBLNR IN ('HH-2026-0815', 'MET-77120') ORDER BY XBLNR;"
        )
        assert zeilen == [{"XBLNR": "HH-2026-0815", "RMWWR": 2941.68}, {"XBLNR": "MET-77120", "RMWWR": 2832.2}]
        assert abgeschnitten is False

    def test_cte_join_aggregation_fensterfunktion(self, db_pfad):
        sql = """
        WITH gebucht AS (SELECT LIFNR, RMWWR FROM RBKP WHERE XBLNR = 'MET-77120')
        SELECT l.NAME1, round(sum(g.RMWWR), 2) AS summe,
               row_number() OVER (ORDER BY sum(g.RMWWR) DESC) AS rang
        FROM gebucht g JOIN LFA1 l ON l.LIFNR = g.LIFNR
        GROUP BY l.NAME1
        """
        zeilen, _ = self.guard(db_pfad).ausfuehren(sql)
        assert zeilen == [{"NAME1": "Mainzer Elektrotechnik AG", "summe": 2832.2, "rang": 1}]

    def test_count_stern_und_kompatibilitaetssicht_mseg(self, db_pfad):
        g = self.guard(db_pfad)
        assert g.ausfuehren("SELECT count(*) AS n FROM LFA1")[0] == [{"n": 6}]
        we = g.ausfuehren("SELECT sum(MENGE) AS menge FROM MSEG WHERE EBELN = '4500000106'")[0]
        assert we == [{"menge": 60.0}]

    def test_schluesselwort_in_zeichenkette_kein_fehlalarm(self, db_pfad):
        zeilen, _ = self.guard(db_pfad).ausfuehren(
            "SELECT XBLNR FROM RBKP WHERE XBLNR = 'DELETE; DROP TABLE RBKP'"
        )
        assert zeilen == []

    @pytest.mark.parametrize(
        "sql, fragment",
        [
            ("DELETE FROM RBKP", "Nur SELECT"),
            ("UPDATE RBKP SET ZLSPR = NULL", "Nur SELECT"),
            ("INSERT INTO RBKP(BELNR) VALUES ('X')", "Nur SELECT"),
            ("DROP TABLE RBKP", "Nur SELECT"),
            ("PRAGMA table_info(RBKP)", "Nur SELECT"),
            ("ATTACH DATABASE 'x.db' AS x", "Nur SELECT"),
            ("WITH x AS (SELECT 1) DELETE FROM RBKP", "DELETE"),
            ("SELECT 1; DROP TABLE RBKP", "Mehrere Anweisungen"),
            ("SELECT * FROM RBKP -- Kommentar", "Kommentare"),
            ("SELECT /* versteckt */ 1", "Kommentare"),
            ("SELECT XBLNR INTO kopie FROM RBKP", "INTO"),
            ("", "Leere"),
        ],
    )
    def test_schicht1_blockiert(self, db_pfad, sql, fragment):
        with pytest.raises(SQLGuardrailVerletzung) as info:
            self.guard(db_pfad).ausfuehren(sql)
        assert info.value.schicht == 1
        assert fragment in info.value.grund

    def test_schicht1_ueberlange_abfrage(self, db_pfad):
        with pytest.raises(SQLGuardrailVerletzung, match="länger"):
            self.guard(db_pfad).ausfuehren("SELECT 1 " + " " * 3000)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM ZFA_META",
            "SELECT name FROM sqlite_master",
            "SELECT count(*) FROM sqlite_schema",
            "SELECT k.XBLNR FROM RBKP k JOIN ZFA_META m ON 1=1",
            "SELECT * FROM main.RBKP",
        ],
    )
    def test_schicht2_tabellen_whitelist(self, db_pfad, sql):
        with pytest.raises(SQLGuardrailVerletzung) as info:
            self.guard(db_pfad).ausfuehren(sql)
        assert info.value.schicht == 2

    def test_schicht2_authorizer_faengt_kommaverbund(self, db_pfad):
        # Komma-Join umgeht die Regex-Vorprüfung – der SQLite-Authorizer greift trotzdem.
        with pytest.raises(SQLGuardrailVerletzung) as info:
            self.guard(db_pfad).ausfuehren("SELECT WERT FROM RBKP, ZFA_META")
        assert info.value.schicht == 2
        assert "ZFA_META" in info.value.grund

    @pytest.mark.parametrize(
        "sql",
        ["SELECT IBAN FROM LFBK", "SELECT * FROM LFBK", "SELECT upper(SWIFT) FROM LFBK",
         "SELECT l.NAME1, b.IBAN FROM LFA1 l JOIN LFBK b USING (LIFNR)"],
    )
    def test_schicht2_gesperrte_spalten(self, db_pfad, sql):
        with pytest.raises(SQLGuardrailVerletzung, match="gesperrt"):
            self.guard(db_pfad).ausfuehren(sql)

    def test_schicht2_freigegebene_spalten_der_bankdatentabelle(self, db_pfad):
        zeilen, _ = self.guard(db_pfad).ausfuehren("SELECT count(*) AS n FROM LFBK WHERE BANKS = 'DE'")
        assert zeilen == [{"n": 6}]

    @pytest.mark.parametrize("funktion", ["load_extension('x')", "randomblob(1000000)", "sqlite_version()"])
    def test_schicht2_funktions_whitelist(self, db_pfad, funktion):
        with pytest.raises(SQLGuardrailVerletzung) as info:
            self.guard(db_pfad).ausfuehren(f"SELECT {funktion}")
        assert info.value.schicht in (1, 2)

    def test_schicht3_verbindung_ist_schreibgeschuetzt(self, db_pfad):
        """Selbst wenn Schicht 1 und 2 umgangen würden, verweigert die DB-Verbindung jeden Schreibzugriff."""
        con = self.guard(db_pfad)._oeffne_verbindung()
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
                con.execute("DELETE FROM RBKP")
            con.execute("PRAGMA query_only = OFF")  # mode=ro bleibt trotzdem wirksam
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
                con.execute("UPDATE RBKP SET ZLSPR = NULL")
        finally:
            con.close()
        con = sqlite3.connect(db_pfad)
        assert con.execute("SELECT count(*) FROM RBKP").fetchone()[0] == 50
        con.close()

    def test_schicht4_zeilenlimit(self, db_pfad):
        zeilen, abgeschnitten = self.guard(db_pfad, max_zeilen=5).ausfuehren("SELECT BELNR FROM RBKP")
        assert len(zeilen) == 5 and abgeschnitten is True

    def test_schicht4_zeilenlimit_schlaegt_eigenes_limit(self, db_pfad):
        zeilen, abgeschnitten = self.guard(db_pfad, max_zeilen=5).ausfuehren("SELECT BELNR FROM RBKP LIMIT 1000")
        assert len(zeilen) == 5 and abgeschnitten is True

    def test_schicht4_zeitlimit(self, db_pfad):
        sql = "WITH RECURSIVE z(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM z) SELECT count(*) FROM z"
        with pytest.raises(SQLGuardrailVerletzung) as info:
            self.guard(db_pfad, timeout_sekunden=0.2).ausfuehren(sql)
        assert info.value.schicht == 4

    def test_protokoll_ist_revisionsfaehig(self, db_pfad):
        g = self.guard(db_pfad)
        g.ausfuehren("SELECT count(*) FROM RBKP")
        with pytest.raises(SQLGuardrailVerletzung):
            g.ausfuehren("DROP TABLE RBKP")
        assert [e.erlaubt for e in g.protokoll] == [True, False]
        assert g.protokoll[1].schicht == 1 and len(g.protokoll[1].sql_hash) == 16


# ─────────────────────────────────────────────────────────────────────────────
# 2 · BPMN-Routing & Pydantic-Schemata
# ─────────────────────────────────────────────────────────────────────────────

class TestRoutingUndSchemata:
    def test_routing_touchless(self, fakten_touchless):
        schritt, pfad = ermittle_soll_route(fakten_touchless)
        assert schritt is WS.TOUCHLESS_FREIGABE
        assert pfad[0] is WS.RECHNUNGSEINGANG and pfad[-1] is WS.TOUCHLESS_FREIGABE
        assert ist_gueltiger_bpmn_pfad(pfad)[0]

    def test_routing_prioritaet_dublette_vor_preis(self, fakten_dublette_und_preis):
        schritt, pfad = ermittle_soll_route(fakten_dublette_und_preis)
        assert schritt is WS.SPERRE_DUBLETTE
        assert pfad == [WS.RECHNUNGSEINGANG, WS.DUBLETTENPRUEFUNG, WS.SPERRE_DUBLETTE]

    def test_routing_warnung_blockiert_nicht(self, fakten_maverick):
        schritt, _ = ermittle_soll_route(fakten_maverick)
        assert schritt is WS.COMPLIANCE_MAVERICK_BUYING  # LEITWEG_ID_FEHLT ist nur WARNUNG

    def test_gueltiger_bericht(self, fakten_preis):
        bericht = AuditBericht.model_validate(
            extrahiere_json(llm_json(fakten_preis, vermerk="Preisabweichung außerhalb der Toleranz festgestellt; Klärung durch den Einkauf."))
        )
        assert bericht.workflow_schritt is WS.KLAERUNG_EINKAUF_PREIS

    def _basis(self, fakten, **over) -> dict:
        daten = extrahiere_json(llm_json(fakten, vermerk="Ein ausreichend langer, sachlicher Audit-Vermerk ohne Zahlen."))
        daten.update(over)
        return daten

    def test_falsches_gesamturteil(self, fakten_preis):
        with pytest.raises(ValidationError, match="Gesamturteil"):
            AuditBericht.model_validate(self._basis(fakten_preis, gesamturteil="FREIGABE"))

    def test_unzulaessiger_sequenzfluss(self, fakten_preis):
        pfad = ["RECHNUNGSEINGANG", "DREI_WEGE_ABGLEICH", "KLAERUNG_EINKAUF_PREIS"]
        with pytest.raises(ValidationError, match="Sequenzfluss"):
            AuditBericht.model_validate(self._basis(fakten_preis, bpmn_pfad=pfad))

    def test_klaerung_ohne_rueckfrage(self, fakten_preis):
        with pytest.raises(ValidationError, match="eine Rückfrage zwingend"):
            AuditBericht.model_validate(self._basis(fakten_preis, rueckfrage=None))

    def test_falscher_empfaenger(self, fakten_preis):
        daten = self._basis(fakten_preis)
        daten["rueckfrage"]["empfaenger"] = "LIEFERANT"
        with pytest.raises(ValidationError, match="Empfänger"):
            AuditBericht.model_validate(daten)

    def test_freigabe_mit_rueckfrage_unzulaessig(self, fakten_touchless, fakten_preis):
        daten = self._basis(fakten_touchless)
        daten["rueckfrage"] = self._basis(fakten_preis)["rueckfrage"]
        with pytest.raises(ValidationError, match="FREIGABE"):
            AuditBericht.model_validate(daten)

    def test_zusatzfelder_verboten(self, fakten_preis):
        with pytest.raises(ValidationError, match="Extra inputs"):
            AuditBericht.model_validate(self._basis(fakten_preis, konfidenz=0.99))

    def test_unbekannter_pruefcode(self, fakten_preis):
        daten = self._basis(fakten_preis)
        daten["feststellungen"][0]["code"] = "BAUCHGEFUEHL"
        with pytest.raises(ValidationError):
            AuditBericht.model_validate(daten)

    def test_konsistenz_erkennt_erfundene_und_fehlende_feststellungen(self, fakten_preis):
        daten = self._basis(fakten_preis)
        daten["feststellungen"] = [{"code": "MENGENABWEICHUNG", "beschreibung": "Erfundene Mengenabweichung.", "betrag": None}]
        fehler = pruefe_konsistenz(AuditBericht.model_validate(daten), fakten_preis)
        assert any("Nicht dokumentierte" in f for f in fehler)
        assert any("Erfundene" in f for f in fehler)


# ─────────────────────────────────────────────────────────────────────────────
# 3 · Zahlen-Faktenprüfung
# ─────────────────────────────────────────────────────────────────────────────

class TestFaktenpruefung:
    def test_belegte_zahlen_deutsches_format(self, fakten_preis):
        p = FaktenPruefer.fuer_audit(fakten_preis)
        text = ("Rechnung RE-2026-10471 vom 20.10.2026 über 6.128,50 EUR brutto (5.150,00 EUR netto, "
                "978,50 EUR USt, 19 %). Einzelpreis 257,50 statt 250,00 EUR, Abweichung 150,00 EUR bzw. 3,00 % "
                "bei einer Toleranz von 2 %. Bestellung 4500012345, Position 10, Menge 20.")
        e = p.pruefe_text(text)
        assert e.bestanden, e
        assert e.geprueft >= 10

    def test_englisches_format_wird_ebenfalls_erkannt(self, fakten_preis):
        assert FaktenPruefer.fuer_audit(fakten_preis).pruefe_text("Gross amount 6,128.50 EUR.").bestanden

    def test_erfundene_zahl_wird_erkannt(self, fakten_preis):
        e = FaktenPruefer.fuer_audit(fakten_preis).pruefe_text("Die Abweichung beträgt 162,75 EUR.")
        assert not e.bestanden and e.nicht_belegte_zahlen == ["162,75"]

    def test_selbst_gerechnete_summe_wird_erkannt(self, fakten_preis):
        # 5.150,00 + 150,00 = 5.300,00 steht nicht in den Quelldaten → LLM hat gerechnet.
        e = FaktenPruefer.fuer_audit(fakten_preis).pruefe_text("Korrigierter Nettobetrag: 5.300,00 EUR.")
        assert not e.bestanden

    def test_rundung_nur_mit_ausreichender_genauigkeit(self):
        p = FaktenPruefer([Decimal("2.4390")])
        assert p.pruefe_text("Abweichung 2,44 %").bestanden
        assert not p.pruefe_text("Abweichung 2 %").bestanden
        assert not p.pruefe_text("Abweichung 2,4 %").bestanden

    def test_negative_betraege_ueber_absolutwert(self):
        assert FaktenPruefer([Decimal("-150.00")]).pruefe_text("Gutschrift über 150,00 EUR").bestanden

    def test_normverweise_und_kennungen_werden_ignoriert(self, fakten_preis):
        text = ("Prüfung nach § 14 Abs. 4 Nr. 5 UStG und EN 16931, XRechnung 3.0, ZUGFeRD 2.3, BPMN 2.0, "
                "3-Way-Matching im 4-Augen-Prinzip, BT-10 Käuferreferenz 991-12345-67, USt-IdNr. DE123456789, "
                "Beleg EKPO 4500012345/00010.")
        assert FaktenPruefer.fuer_audit(fakten_preis).pruefe_text(text).bestanden

    def test_unbekanntes_datum_wird_erkannt(self, fakten_preis):
        e = FaktenPruefer.fuer_audit(fakten_preis).pruefe_text("Zahlung bis 15.11.2026 erbeten.")
        assert not e.bestanden and e.nicht_belegte_daten == ["15.11.2026"]

    def test_belegte_datumsformate(self, fakten_preis):
        p = FaktenPruefer.fuer_audit(fakten_preis)
        assert p.pruefe_text("Skontofrist 03.11.2026 bzw. 2026-11-03.").bestanden

    def test_feststellungsbetrag_wird_geprueft(self, fakten_preis):
        daten = extrahiere_json(llm_json(
            fakten_preis, vermerk="Preisabweichung außerhalb der Toleranz, Klärung durch den Einkauf.",
            feststellungen=[{"code": "PREISABWEICHUNG", "beschreibung": "Einzelpreis über Bestellpreis.", "betrag": "999.99"}],
        ))
        e = FaktenPruefer.fuer_audit(fakten_preis).pruefe_bericht(AuditBericht.model_validate(daten))
        assert not e.bestanden and "feststellungen[0].betrag" in e.fundstellen

    def test_meldungstexte_sind_keine_zahlenquelle(self):
        """Zahlen, die nur im Freitext 'meldung' stehen, gelten nicht als belegt."""
        from src.agent import Pruefergebnis, Pruefstatus
        from .conftest import _rechnung
        f = AuditFakten(rechnung=_rechnung(), pruefungen=[
            Pruefergebnis(code=PruefCode.SKONTO_FRIST, status=Pruefstatus.WARNUNG, meldung="Skonto 777,77 EUR")
        ])
        assert not FaktenPruefer.fuer_audit(f).pruefe_text("Skonto 777,77 EUR").bestanden

    def test_fmt_de(self):
        assert fmt_de(Decimal("6128.5")) == "6.128,50"
        assert fmt_de(Decimal("-0.005")) == "-0,01"


# ─────────────────────────────────────────────────────────────────────────────
# 4 · Agent-Ablauf mit Mock-LLM
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentMitMock:
    def test_happy_path_touchless(self, fakten_touchless):
        antwort = llm_json(
            fakten_touchless,
            vermerk=("Die XRechnung RE-2026-10471 der Rhein-Main Industriebedarf GmbH vom 20.10.2026 über "
                     "6.128,50 EUR brutto wurde ohne Befund geprüft; 3-Way-Matching erfolgreich, Dunkelverarbeitung zulässig."),
            naechster="Automatische Buchung und Übergabe an den Zahlungslauf.",
        )
        llm = MockLLMClient([antwort])
        ergebnis = FlowAuditAgent(llm, EINST).pruefe_rechnung(fakten_touchless)
        assert ergebnis.quelle == "LLM" and ergebnis.versuche == 1
        assert ergebnis.bericht.gesamturteil.value == "FREIGABE"
        assert ergebnis.faktencheck.bestanden
        assert len(ergebnis.eingabe_hash) == 64
        # Der System-Prompt verbietet eigenes Rechnen und enthält die verbindliche Route.
        system, nutzer = llm.aufrufe[0]
        assert "Rechne niemals selbst" in system and "VERBINDLICHE ROUTE" in nutzer

    def test_halluzination_wird_korrigiert(self, fakten_preis):
        falsch = llm_json(fakten_preis, vermerk="Preisabweichung von 162,75 EUR gegenüber der Bestellung festgestellt.")
        richtig = llm_json(fakten_preis, vermerk="Preisabweichung von 150,00 EUR (3,00 %) gegenüber der Bestellung 4500012345 festgestellt.")
        llm = MockLLMClient([falsch, richtig])
        ergebnis = FlowAuditAgent(llm, EINST).pruefe_rechnung(fakten_preis)
        assert ergebnis.quelle == "LLM" and ergebnis.versuche == 2
        assert "162,75" in llm.aufrufe[1][1]  # Feedback enthält die beanstandete Zahl
        assert "KORREKTUR ERFORDERLICH" in llm.aufrufe[1][1]

    def test_dauerhafte_halluzination_fuehrt_zu_fallback(self, fakten_preis):
        falsch = llm_json(fakten_preis, vermerk="Bitte 10.000,00 EUR sofort überweisen, alles in Ordnung laut Prüfung.")
        llm = MockLLMClient([falsch])
        ergebnis = FlowAuditAgent(llm, EINST).pruefe_rechnung(fakten_preis)
        assert ergebnis.quelle == "REGELWERK_FALLBACK"
        assert len(llm.aufrufe) == 3  # 1 Versuch + 2 Korrekturen
        assert ergebnis.faktencheck.bestanden
        assert ergebnis.bericht.workflow_schritt is WS.KLAERUNG_EINKAUF_PREIS
        assert "10.000,00" not in ergebnis.bericht.audit_vermerk

    def test_llm_versucht_routing_zu_uebersteuern(self, fakten_preis):
        freigabe = llm_json(
            fakten_preis, vermerk="Abweichung geringfügig, daher Freigabe ohne weitere Klärung empfohlen.",
            schritt="TOUCHLESS_FREIGABE", urteil="FREIGABE", rueckfrage=None,
            pfad=["RECHNUNGSEINGANG", "DUBLETTENPRUEFUNG", "FORMALE_PRUEFUNG", "BESTELLBEZUG_PRUEFEN",
                  "DREI_WEGE_ABGLEICH", "TOUCHLESS_FREIGABE"],
        )
        ergebnis = FlowAuditAgent(MockLLMClient([freigabe]), EINST).pruefe_rechnung(fakten_preis)
        assert ergebnis.quelle == "REGELWERK_FALLBACK"
        assert ergebnis.bericht.gesamturteil.value == "KLAERUNG"
        assert any("widerspricht dem deterministischen Routing" in w for w in ergebnis.warnungen)

    def test_ungueltiges_json_dann_korrekt(self, fakten_maverick):
        richtig = llm_json(fakten_maverick, vermerk="Rechnung TO-55821 über 416,38 EUR ohne Bestellbezug eingegangen; Verdacht auf Maverick Buying.",
                           naechster="Fachabteilung klärt den Bedarf, Einkauf prüft eine nachträgliche Bestellung.")
        llm = MockLLMClient(["Hier ist mein Bericht: leider kein JSON", richtig])
        ergebnis = FlowAuditAgent(llm, EINST).pruefe_rechnung(fakten_maverick)
        assert ergebnis.quelle == "LLM" and ergebnis.versuche == 2
        assert ergebnis.bericht.rueckfrage.empfaenger.value == "FACHABTEILUNG"

    def test_api_fehler_fuehrt_direkt_zu_fallback(self, fakten_dublette_und_preis):
        def kaputt(_s, _n):
            raise ConnectionError("Timeout")
        ergebnis = FlowAuditAgent(MockLLMClient(kaputt), EINST).pruefe_rechnung(fakten_dublette_und_preis)
        assert ergebnis.quelle == "REGELWERK_FALLBACK"
        assert ergebnis.bericht.workflow_schritt is WS.SPERRE_DUBLETTE
        assert any("ConnectionError" in w for w in ergebnis.warnungen)

    @pytest.mark.parametrize("name", ["fakten_touchless", "fakten_preis", "fakten_dublette_und_preis", "fakten_maverick"])
    def test_fallback_ist_immer_valide_und_faktentreu(self, request, name):
        fakten = request.getfixturevalue(name)
        bericht = erstelle_regelbasierten_bericht(fakten)
        assert pruefe_konsistenz(bericht, fakten) == []
        assert FaktenPruefer.fuer_audit(fakten).pruefe_bericht(bericht).bestanden

    def test_revisionsprotokoll(self, fakten_preis):
        ergebnis = FlowAuditAgent(MockLLMClient(["{}"]), EINST).pruefe_rechnung(fakten_preis)
        protokoll = ergebnis.als_revisionsprotokoll()
        assert protokoll["eingabe_sha256"] == fakten_preis.eingabe_hash()
        assert protokoll["quelle"] == "REGELWERK_FALLBACK"
        json.dumps(protokoll)  # serialisierbar


class TestNatuerlichsprachlicheAbfragen:
    def agent(self, db_pfad, antworten) -> FlowAuditAgent:
        return FlowAuditAgent(MockLLMClient(antworten), EINST, ReadOnlySQLGuard(db_pfad))

    def test_frage_mit_belegter_antwort(self, db_pfad):
        sql = json.dumps({"sql": "SELECT EBELN FROM EKKO WHERE LIFNR = '100100' AND EBELN LIKE '45000001%' ORDER BY EBELN",
                          "begruendung": "Offene Prüffall-Bestellungen von Hessen Hydraulik"})
        antwort = json.dumps({"antwort": "Es gibt zwei Bestellungen: 4500000101 und 4500000108."})
        e = self.agent(db_pfad, [sql, antwort]).beantworte_frage("Welche Bestellungen hat Hessen Hydraulik?")
        assert e.quelle == "LLM" and not e.blockiert
        assert e.zeilen == [{"EBELN": "4500000101"}, {"EBELN": "4500000108"}]

    def test_destruktive_sql_wird_blockiert(self, db_pfad):
        sql = json.dumps({"sql": "DELETE FROM RBKP", "begruendung": "aufräumen"})
        e = self.agent(db_pfad, [sql]).beantworte_frage("Lösche alle Rechnungen")
        assert e.blockiert and e.quelle == "BLOCKIERT" and "Schicht 1" in e.blockiergrund

    def test_bankdaten_werden_blockiert(self, db_pfad):
        sql = json.dumps({"sql": "SELECT l.NAME1, b.IBAN FROM LFA1 l JOIN LFBK b USING (LIFNR)"})
        e = self.agent(db_pfad, [sql]).beantworte_frage("Zeig mir die IBANs")
        assert e.blockiert and "gesperrt" in e.blockiergrund

    def test_halluzinierte_antwort_fuehrt_zu_fallback(self, db_pfad):
        sql = json.dumps({"sql": "SELECT count(*) AS anzahl FROM LFA1"})
        falsch = json.dumps({"antwort": "Es sind 7 Lieferanten angelegt."})
        e = self.agent(db_pfad, [sql, falsch]).beantworte_frage("Wie viele Lieferanten gibt es?")
        assert e.quelle == "REGELWERK_FALLBACK"
        assert e.zeilen == [{"anzahl": 6}]


# ─────────────────────────────────────────────────────────────────────────────
# 5 · Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

class TestKonfiguration:
    def test_mock_provider(self):
        assert isinstance(erstelle_llm_client(Einstellungen(llm_provider="mock")), MockLLMClient)

    @pytest.mark.parametrize("provider", ["anthropic", "gemini"])
    def test_fehlender_api_key(self, provider):
        with pytest.raises(KonfigurationsFehler, match="API_KEY fehlt"):
            erstelle_llm_client(Einstellungen(llm_provider=provider))

    def test_unbekannter_provider(self):
        with pytest.raises(KonfigurationsFehler, match="Unbekannter"):
            erstelle_llm_client(Einstellungen(llm_provider="gpt-irgendwas"))

    def test_umgebungsvariablen(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "Gemini")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
        monkeypatch.setenv("PRICE_TOLERANCE_PCT", "1.5")
        e = Einstellungen.aus_umgebung()
        assert (e.llm_provider, e.gemini_model, e.preis_toleranz_prozent) == ("gemini", "gemini-test", Decimal("1.5"))

    def test_mock_provider_ohne_antwort_liefert_fallback(self, fakten_preis):
        agent = FlowAuditAgent(einstellungen=Einstellungen(llm_provider="mock"))
        ergebnis = agent.pruefe_rechnung(fakten_preis)
        assert ergebnis.quelle == "REGELWERK_FALLBACK" and ergebnis.versuche == 3
        assert "rechnungsnummer: Field required" in ergebnis.warnungen[0]  # kompakte Fehlermeldung

    def test_offline_modus_ohne_api_key(self, fakten_preis, db_pfad):
        agent = FlowAuditAgent(einstellungen=Einstellungen(llm_provider="offline"))
        ergebnis = agent.pruefe_rechnung(fakten_preis)
        assert ergebnis.quelle == "REGELWERK_FALLBACK" and ergebnis.versuche == 0
        assert ergebnis.faktencheck.bestanden and ergebnis.modell == "regelwerk"
        assert agent.beantworte_frage("Wie viele Rechnungen?").blockiert

    def test_standard_ist_offline(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        assert Einstellungen.aus_umgebung().llm_provider == "offline"
