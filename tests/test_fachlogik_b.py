"""Schritt B: Vorperiodenvergleich, Formalprüfung, Toleranzen und Treiber, Prävention, Klärfälle, Skonto, Scorecard."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.agent import WorkflowSchritt
from src.assistent import KIAssistent
from src.bpmn_tracker import Vorgang, berechne_kpis, lade_vorgaenge, periodenvergleich
from src.demo import batchlauf, einstellungen_fuer, stelle_demodaten_sicher
from src.einvoice_parser import lese_rechnung, validiere_rechnung
from src.formalpruefung import fehlerliste, pflichtangaben, zusammenfassung
from src.klaerfall import (
    FreigabeFehler,
    lege_an,
    loese,
    pruefe_rueckfrage,
    sende_rueckfrage,
    zahlungsfreigabe,
    zweite_freigabe,
)
from src.lieferanten import scorecards
from src.matching_engine import MatchingEngine
from src.praevention import Bankaenderung, Beleg, nummer_aehnlichkeit, pruefe_beleg, pruefe_bestand, pruefe_vorgang
from src.treiber import TREIBER, rechnungstreiber, treiber_gesamt
from src.zahlung import letzter_zahllauf, skonto_jahreszins, zahllaeufe, zahlungsvorschlag

WS = WorkflowSchritt


@pytest.fixture()
def demodaten(demo):
    return stelle_demodaten_sicher(demo.db_pfad.parent)


@pytest.fixture()
def assistent(demodaten):
    return KIAssistent(demodaten.db_pfad, einstellungen_fuer("offline"), stichtag=demodaten.stichtag)


@pytest.fixture()
def lauf(demodaten, tmp_path):
    return batchlauf(demodaten, Decimal("2.0"), tmp_path)


def _datei(demodaten, praefix: str):
    return demodaten.ordner / next(n for n in demodaten.manifest if n.startswith(praefix))


@pytest.fixture()
def alle(assistent, demodaten):
    """(Vorgang, Klärfall, Risikohinweise) für alle Musterbelege."""
    ergebnis = {}
    for name in demodaten.manifest:
        v = assistent.pruefe(demodaten.ordner / name)
        risiken = pruefe_vorgang(demodaten.db_pfad, v, demodaten.stichtag)
        ergebnis[name[:2]] = (v, lege_an(v, risiken), risiken)
    return ergebnis


# --- Stammdaten wie VarianceAI ----------------------------------------------------------------------------------


def test_stammdaten_und_ekbe(demodaten):
    con = sqlite3.connect(demodaten.db_pfad)
    try:
        kostl = {z[0] for z in con.execute("SELECT DISTINCT KOSTL FROM EKPO")}
        assert kostl <= {"4100", "4200", "4300", "4400", "4500"} and {"4100", "4200"} <= kostl
        assert {z[0] for z in con.execute("SELECT DISTINCT SAKTO FROM EKPO")} == {"400000"}
        assert con.execute("SELECT count(*) FROM EKPO WHERE EINDT IS NULL OR WERKS <> '1000'").fetchone()[0] == 0
        assert con.execute("SELECT DISTINCT BUKRS FROM EKKO").fetchall() == [("1000",)]
        we = con.execute("SELECT sum(MENGE) FROM EKBE WHERE EBELN = '4500000106' AND VGABE = '1'").fetchone()[0]
        assert we == 60                                                # Teil-Lieferung 06: 60 von 100 geliefert
        assert con.execute("SELECT count(*) FROM EKBE WHERE VGABE = '2'").fetchone()[0] > 0
        assert con.execute("SELECT count(*) FROM RBKP").fetchone()[0] == 50   # Historie unverändert
    finally:
        con.close()


# --- B.1 Vorperiodenvergleich ----------------------------------------------------------------------------------


@pytest.mark.invariante
def test_periodenvergleich_entspricht_fenstern(lauf, demodaten):
    vorgaenge = lade_vorgaenge(lauf.db_kopie)
    p = periodenvergleich(vorgaenge, demodaten.stichtag, 28)
    assert p.aktuell.bis == demodaten.stichtag and p.vorperiode.bis == p.aktuell.von == demodaten.stichtag - timedelta(28)
    k = berechne_kpis(vorgaenge, demodaten.stichtag)
    assert p.aktuell.volumen_offen_eur == k.volumen_offen_eur and p.aktuell.skonto_gefaehrdet_eur == k.skonto_gefaehrdet_eur
    assert p.delta("eingegangen") == p.aktuell.eingegangen - p.vorperiode.eingegangen
    assert p.delta("touchless_quote_prozent") == p.aktuell.touchless_quote_prozent - p.vorperiode.touchless_quote_prozent


def test_delta_prozent_ohne_basis():
    t = datetime(2026, 10, 27, 9)
    v = [Vorgang("A", "1", WS.TOUCHLESS_FREIGABE, Decimal("10"), t, t)]
    p = periodenvergleich(v, date(2026, 10, 28), 7)
    assert p.vorperiode.eingegangen == 0 and p.delta_prozent("eingegangen") is None and p.delta("eingegangen") == 1
    with pytest.raises(ValueError):
        periodenvergleich(v, date(2026, 10, 28), 0)


# --- B.2 Formalprüfung -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("praefix, erwartet", [
    ("01_", None), ("09_", "Nr. 2"), ("10_", "BR-CO-10/13/15"),
])
def test_pflichtangaben_verstaendliche_fehlerliste(demodaten, praefix, erwartet):
    r = lese_rechnung(_datei(demodaten, praefix))
    rechenfehler = any(p.code.value == "SUMMENFEHLER" and p.status.value == "FEHLER" for p in validiere_rechnung(r))
    angaben = pflichtangaben(r, rechenfehler)
    gruppen = {a.gruppe for a in angaben}
    assert {"§ 14 Abs. 4 UStG", "EN 16931", "XRechnung (CIUS)"} <= gruppen
    assert {a.nummer for a in angaben if a.gruppe == "§ 14 Abs. 4 UStG"} >= {f"Nr. {i}" for i in range(1, 9)}
    fehler = [a for a in angaben if a.ist_fehler]
    if erwartet is None:
        assert not fehler and not fehlerliste(angaben)
    else:
        (f,) = fehler
        assert f.nummer == erwartet and f.folge and f.korrektur
        assert fehlerliste(angaben)[0].startswith(f.befund)
    erfuellt, n_fehler, geprueft = zusammenfassung(angaben)
    assert erfuellt + n_fehler <= geprueft and n_fehler == len(fehler)


def test_formalpruefung_konsistent_zum_routing(assistent, demodaten):
    """Fehler in der Checkliste ⇔ Formfehler-Route (außer Dubletten, die vorher stoppen)."""
    for name in demodaten.manifest:
        if name.startswith("15_"):
            continue
        v = assistent.pruefe(demodaten.ordner / name)
        rechenfehler = any(p.code.value == "SUMMENFEHLER" and p.status.value == "FEHLER" for p in v.fakten.pruefungen)
        hat_fehler = any(a.ist_fehler for a in pflichtangaben(v.rechnung, rechenfehler))
        assert hat_fehler == (v.schritt is WS.KLAERUNG_LIEFERANT_FORMFEHLER), name


# --- B.3 Toleranzen und Treiber --------------------------------------------------------------------------------


def test_mengentoleranz_konfigurierbar(demodaten):
    r = lese_rechnung(_datei(demodaten, "14_"))            # Mehrmenge 120 statt 100 (Überlieferung)
    streng = MatchingEngine(demodaten.db_pfad, Decimal("2"), demodaten.stichtag).pruefe(r)
    grosszuegig = MatchingEngine(demodaten.db_pfad, Decimal("2"), demodaten.stichtag,
                                 mengen_toleranz_prozent=Decimal("25")).pruefe(r)
    fehler = {p.code.value for p in streng.fehler}
    assert "MENGENABWEICHUNG" in fehler
    assert "MENGENABWEICHUNG" not in {p.code.value for p in grosszuegig.fehler}


def test_preistoleranz_aendert_route(demodaten):
    r = lese_rechnung(_datei(demodaten, "03_"))            # +4,5 %
    from src.agent import ermittle_soll_route

    assert ermittle_soll_route(MatchingEngine(demodaten.db_pfad, Decimal("2"), demodaten.stichtag).pruefe(r))[0] \
        is WS.KLAERUNG_EINKAUF_PREIS
    assert ermittle_soll_route(MatchingEngine(demodaten.db_pfad, Decimal("5"), demodaten.stichtag).pruefe(r))[0] \
        is WS.TOUCHLESS_FREIGABE


@pytest.mark.invariante
def test_treiber_schliessen_centgenau(alle):
    treiber = []
    for kurz, (v, _, _) in alle.items():
        t = rechnungstreiber(v)
        if v.abgelehnt:
            assert t is None
            continue
        assert t.netto == t.gedeckt + sum(t.treiber.values()) == v.fakten.rechnung.summe_netto, kurz
        assert set(t.treiber) == set(TREIBER)
        treiber.append(t)
    erwartet = {"03_": "Preis", "05_": "fehlender WE", "06_": "Teil-Lieferung", "07_": "ohne Bestellung",
                "08_": "Dublette", "14_": "Menge"}
    for kurz, grund in erwartet.items():
        t = rechnungstreiber(alle[kurz[:2]][0])
        assert max(t.treiber, key=lambda g: abs(t.treiber[g])) == grund, kurz
    gesamt = {s.treiber: s for s in treiber_gesamt(treiber)}
    assert gesamt["Preis"].anzahl_rechnungen == 3 and gesamt["Dublette"].betrag == Decimal("4852.00")


# --- B.4 Prävention --------------------------------------------------------------------------------------------


def test_praeventionsregeln_im_bestand(lauf, demodaten):
    hinweise = pruefe_bestand(lauf.db_kopie, demodaten.stichtag)
    regeln = {h.regel for h in hinweise}
    assert regeln == {"DUBLETTENVERDACHT", "BANKDATEN_GEAENDERT", "OHNE_BESTELLUNG", "GLATTER_BETRAG"}
    assert all(h.begruendung and h.belege and h.massnahme for h in hinweise)
    bank = {h.xblnr for h in hinweise if h.regel == "BANKDATEN_GEAENDERT"}
    assert bank == {"RLV-2026-0447", "RLV-2026-0512"}                   # Lieferant 100400, Änderung vor 5 Tagen
    glatt = [h for h in hinweise if h.regel == "GLATTER_BETRAG"]
    assert len(glatt) == 1 and "2.500,00 €" in glatt[0].begruendung
    assert any(h.xblnr == "MET-77120-K" for h in hinweise if h.regel == "DUBLETTENVERDACHT")


def test_regeln_einzeln():
    t = date(2026, 10, 20)
    alt = Beleg("RE-1001", "100100", t, t, Decimal("1190.00"), Decimal("1000.00"), "4500000001", (Decimal(10),), "1")
    neu = Beleg("RE-1001A", "100100", t + timedelta(days=40), t + timedelta(days=40), Decimal("1190.00"),
                Decimal("1000.00"), "4500000002", (Decimal(10),), "2")
    (h,) = pruefe_beleg(neu, [alt, neu], [], t)                        # Nummer ähnlich trotz 40 Tagen Abstand
    assert h.regel == "DUBLETTENVERDACHT" and "ähnelt" in h.begruendung and h.sperrt_zahlung
    anderer = Beleg("XY-9", "100200", t, t, Decimal("1190.00"), Decimal("1000.00"), "4500000003", (Decimal(10),), "3")
    assert pruefe_beleg(anderer, [alt, anderer], [], t) == []
    bank = Bankaenderung("100200", t - timedelta(days=31), "U", "DE…1", "DE…2", "1")
    assert pruefe_beleg(anderer, [anderer], [bank], t) == []           # 31 Tage: außerhalb des Fensters
    pauschale = Beleg("P-1", "100600", t, t, Decimal("5950.00"), Decimal("5000.00"), None, (Decimal(1),), "4")
    assert {h.regel for h in pruefe_beleg(pauschale, [pauschale], [], t)} == {"OHNE_BESTELLUNG", "GLATTER_BETRAG"}
    menge_mal_preis = Beleg("M-1", "100100", t, t, Decimal("5950.00"), Decimal("5000.00"), "4500000004",
                            (Decimal(20),), "5")                       # 20 × 250 € aus Bestellung: kein Treffer
    assert pruefe_beleg(menge_mal_preis, [menge_mal_preis], [], t) == []
    assert nummer_aehnlichkeit("MET-77120", "MET 77120-K") > 0.85


# --- B.5 Klärfälle ---------------------------------------------------------------------------------------------


def test_klaerfaelle_entstehen_richtig(alle):
    ohne = {k for k, (_, f, _) in alle.items() if f is None}
    assert ohne == {"01", "02", "04"}                                   # 13: touchless, aber Bankdaten geändert
    assert alle["13"][1].grund_code == "PRAEVENTION" and alle["15"][1].grund_code == "ABGELEHNT"
    assert alle["03"][1].zustaendig == "Einkauf" and alle["05"][1].zustaendig.startswith("Wareneingang")
    assert alle["11"][1].vier_augen and not alle["03"][1].vier_augen   # Grenze 2.500 € brutto


def test_klaerfall_workflow_mit_vier_augen(alle):
    v, fall, _ = alle["11"]
    assert fall.status == "offen" and not zahlungsfreigabe(fall)
    with pytest.raises(FreigabeFehler, match="nur mit Namen"):
        sende_rueckfrage(fall, " ", fall.rueckfrage_text, pruefe_rueckfrage(v, fall.rueckfrage_text))
    manipuliert = fall.rueckfrage_text + "\nBitte überweisen Sie 1.234,56 EUR."
    with pytest.raises(FreigabeFehler, match="nicht belegte Zahlen"):
        sende_rueckfrage(fall, "Erika", manipuliert, pruefe_rueckfrage(v, manipuliert))
    fall = sende_rueckfrage(fall, "Erika", fall.rueckfrage_text, pruefe_rueckfrage(v, fall.rueckfrage_text))
    assert fall.status == "in Klärung"
    with pytest.raises(FreigabeFehler, match="mindestens 10 Zeichen"):
        loese(fall, "Erika", "ok")
    fall = loese(fall, "Erika", "Lieferant schickt Gutschrift über die Preisdifferenz.")
    assert fall.status == "in Klärung" and fall.wartet_auf_zweite_freigabe and not zahlungsfreigabe(fall)
    with pytest.raises(FreigabeFehler, match="andere Person"):
        zweite_freigabe(fall, "erika ")
    fall = zweite_freigabe(fall, "Max")
    assert fall.status == "gelöst" and fall.freigegeben_von == ["Erika", "Max"] and zahlungsfreigabe(fall)
    assert [e.art for e in fall.verlauf] == ["ANGELEGT", "RÜCKFRAGE FREIGEGEBEN", "FREIGABE 1/2",
                                              "FREIGABE 2/2 · GELÖST"]


def test_klaerfall_ohne_vier_augen_und_dublette(alle):
    v, fall, _ = alle["03"]
    fall = sende_rueckfrage(fall, "Erika", fall.rueckfrage_text, pruefe_rueckfrage(v, fall.rueckfrage_text))
    fall = loese(fall, "Erika", "Preiserhöhung vom Einkauf bestätigt, Bestellung angepasst.")
    assert fall.status == "gelöst" and zahlungsfreigabe(fall)
    v8, dublette, _ = alle["08"]
    dublette = sende_rueckfrage(dublette, "A", dublette.rueckfrage_text, pruefe_rueckfrage(v8, dublette.rueckfrage_text))
    dublette = zweite_freigabe(loese(dublette, "A", "Dublette bestätigt, Beleg storniert."), "B")
    assert dublette.status == "gelöst" and not zahlungsfreigabe(dublette)   # Dubletten werden nie bezahlt


# --- B.6 Skonto-Optimierung ------------------------------------------------------------------------------------


def test_zahllaeufe_und_jahreszins():
    stichtag = date(2026, 10, 28)                                       # Mittwoch
    assert zahllaeufe(stichtag, date(2026, 11, 6)) == [date(2026, 10, 30), date(2026, 11, 3), date(2026, 11, 6)]
    assert letzter_zahllauf(stichtag, date(2026, 10, 29)) is None
    assert skonto_jahreszins(Decimal(2), 14, 30) == Decimal("45.92")
    assert skonto_jahreszins(Decimal(2), 30, 30) is None


def test_zahlungsvorschlag(alle, demodaten):
    z = zahlungsvorschlag(alle.values(), demodaten.stichtag)
    pos = {p.rechnungsnummer: p for p in z.positionen}
    assert pos["HH-2026-0931"].zustand == "Zahlung" and pos["HH-2026-0931"].skonto_status == "gesichert"
    assert pos["HH-2026-0931"].zahltag <= pos["HH-2026-0931"].skonto_frist
    assert pos["HH-2026-0931"].zahlbetrag == pos["HH-2026-0931"].brutto - pos["HH-2026-0931"].skonto_betrag
    assert pos["RLV-2026-0512"].zustand == "gesperrt" and pos["RLV-2026-0512"].skonto_status == "gefährdet"
    assert pos["TPK-2026-118"].skonto_status == "gefährdet" and pos["RLV-2026-0447"].skonto_status == "verloren"
    assert all(p.zahltag.weekday() in (1, 4) for p in z.positionen if p.zustand == "Zahlung")
    assert z.zahlbetrag == sum(z.je_zahllauf().values())
    assert z.skonto_gesichert + z.skonto_gefaehrdet + z.skonto_verloren == sum(p.skonto_betrag for p in z.positionen)


def test_geloester_klaerfall_wird_zahlbar(alle, demodaten):
    v, fall, risiken = alle["03"]
    fall = loese(sende_rueckfrage(fall, "E", fall.rueckfrage_text, pruefe_rueckfrage(v, fall.rueckfrage_text)),
                 "E", "Preis bestätigt durch Einkauf.")
    z = zahlungsvorschlag([(v, fall, risiken)], demodaten.stichtag)
    (p,) = z.positionen
    assert p.zustand in ("Zahlung", "Eilzahlung") and p.skonto_status == "gesichert"


# --- B.7 Lieferanten-Scorecard ---------------------------------------------------------------------------------


def test_scorecard(lauf, demodaten):
    karten = {k.lifnr: k for k in scorecards(lauf.db_kopie, risiken=pruefe_bestand(lauf.db_kopie, demodaten.stichtag))}
    assert sum(k.rechnungen for k in karten.values()) == berechne_kpis(lade_vorgaenge(lauf.db_kopie)).anzahl_rechnungen
    buero = karten["100600"]
    assert buero.liefertreue_prozent is None and buero.fehlerquote_prozent == Decimal("100.00")
    assert buero.bewertung == "C" and "ohne Bestellungen" in buero.begruendung
    assert karten["100200"].preisabweichungen >= 2                        # Fälle 03 und 11 (Taunus Präzisionsteile)
    for k in karten.values():
        assert k.bewertung in "ABC" and k.fehler <= k.rechnungen
        if k.liefertreue_prozent is not None:
            assert 0 <= k.puenktlich <= k.positionen_geliefert


# ---------------------------------------------------------------------------
# KI-Anbieter: Auswahl immer vollständig, wirksamer Anbieter nur mit Schlüssel
# ---------------------------------------------------------------------------


def test_anbieterwahl_mit_und_ohne_schluessel():
    from dataclasses import replace

    from src.agent import Einstellungen
    from src.demo import ANBIETER_NAMEN, anbieterwahl, standardwahl

    assert list(ANBIETER_NAMEN) == ["offline", "claude", "gemini"]
    leer = replace(Einstellungen(), anthropic_api_key=None, gemini_api_key=None)
    ohne = anbieterwahl("claude", leer)
    assert ohne.offline and ohne.wahl == "claude" and "ANTHROPIC_API_KEY" in ohne.hinweis
    assert anbieterwahl("gemini", leer).hinweis.startswith("Kein GEMINI_API_KEY")
    assert anbieterwahl("offline", leer).hinweis is None and anbieterwahl("offline", leer).anzeige == "Offline · Regelwerk"
    mit = anbieterwahl("claude", replace(leer, anthropic_api_key="sk-test-geheim", anthropic_model="claude-test"))
    assert (mit.wirksam, mit.modell, mit.anzeige, mit.hinweis) == ("anthropic", "claude-test", "Claude · claude-test", None)
    assert "sk-test-geheim" not in repr(mit)                          # Schlüsselwerte werden nie weitergegeben
    assert standardwahl(replace(leer, llm_provider="anthropic")) == "claude"
    assert standardwahl(replace(leer, llm_provider="gemini")) == "gemini"
    assert standardwahl(replace(leer, llm_provider="mock")) == "offline"
