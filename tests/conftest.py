"""Gemeinsame Test-Fixtures: SAP-Demodatenbank, XML-Musterrechnungen und Prüfstände."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import (  # noqa: E402
    AuditFakten,
    Pruefergebnis,
    PruefCode,
    Pruefstatus,
    Rechnungsfakten,
    Rechnungsposition,
    ermittle_soll_route,
)
from src.seed_data import DemoDaten, erzeuge_demo_daten  # noqa: E402

STICHTAG = date(2026, 10, 28)


@pytest.fixture(scope="session")
def _demo_vorlage(tmp_path_factory) -> DemoDaten:
    """Einmal erzeugte Demo-Daten (SAP-Datenbank + 12 XML-Rechnungen) mit festem Stichtag."""
    return erzeuge_demo_daten(tmp_path_factory.mktemp("vorlage"), referenzdatum=STICHTAG)


@pytest.fixture()
def demo(_demo_vorlage: DemoDaten, tmp_path: Path) -> DemoDaten:
    """Frische Kopie je Test – Tests dürfen buchen, ohne sich gegenseitig zu beeinflussen."""
    ziel = tmp_path / "data"
    shutil.copytree(_demo_vorlage.db_pfad.parent, ziel)
    return DemoDaten(
        db_pfad=ziel / "flowaudit_erp.db",
        rechnungsordner=ziel / "invoices",
        manifest_pfad=ziel / "invoices" / "manifest.json",
        referenzdatum=STICHTAG,
        anzahl_rechnungen=_demo_vorlage.anzahl_rechnungen,
        anzahl_historie=_demo_vorlage.anzahl_historie,
    )


@pytest.fixture()
def db_pfad(demo: DemoDaten) -> Path:
    return demo.db_pfad


@pytest.fixture()
def manifest(demo: DemoDaten) -> list[dict]:
    return json.loads(demo.manifest_pfad.read_text(encoding="utf-8"))["rechnungen"]


def _rechnung(**over) -> Rechnungsfakten:
    daten = dict(
        rechnungsnummer="RE-2026-10471",
        rechnungsdatum=date(2026, 10, 20),
        format="XRechnung-UBL",
        lieferant_id="100234",
        lieferant_name="Rhein-Main Industriebedarf GmbH",
        ust_id="DE123456789",
        leitweg_id="991-12345-67",
        bestellnummer="4500012345",
        summe_netto=Decimal("5150.00"),
        summe_steuer=Decimal("978.50"),
        summe_brutto=Decimal("6128.50"),
        skonto_prozent=Decimal("2"),
        skonto_frist=date(2026, 11, 3),
        positionen=[
            Rechnungsposition(
                position=10, material="M-4711", beschreibung="Hydraulikpumpe HP-200",
                menge=Decimal("20"), einzelpreis_netto=Decimal("257.50"), steuersatz=Decimal("19"),
                betrag_netto=Decimal("5150.00"), bestellposition=10,
            )
        ],
    )
    daten.update(over)
    return Rechnungsfakten(**daten)


def _ok(code: PruefCode) -> Pruefergebnis:
    return Pruefergebnis(code=code, status=Pruefstatus.OK, meldung="Prüfung ohne Befund.")


PREIS_FEHLER = Pruefergebnis(
    code=PruefCode.PREISABWEICHUNG, status=Pruefstatus.FEHLER,
    meldung="Einzelpreis über Bestellpreis, außerhalb der Toleranz.",
    soll=Decimal("250.00"), ist=Decimal("257.50"), abweichung=Decimal("150.00"),
    abweichung_prozent=Decimal("3.00"), referenz="EKPO 4500012345/00010",
)


@pytest.fixture()
def fakten_touchless() -> AuditFakten:
    return AuditFakten(
        rechnung=_rechnung(),
        pruefungen=[_ok(c) for c in (PruefCode.DUBLETTE, PruefCode.SUMMENFEHLER, PruefCode.PREISABWEICHUNG,
                                       PruefCode.MENGENABWEICHUNG, PruefCode.KEIN_WARENEINGANG)],
    )


@pytest.fixture()
def fakten_preis() -> AuditFakten:
    return AuditFakten(
        rechnung=_rechnung(),
        pruefungen=[_ok(PruefCode.DUBLETTE), _ok(PruefCode.SUMMENFEHLER), PREIS_FEHLER],
    )


@pytest.fixture()
def fakten_dublette_und_preis() -> AuditFakten:
    return AuditFakten(
        rechnung=_rechnung(),
        pruefungen=[
            Pruefergebnis(code=PruefCode.DUBLETTE, status=Pruefstatus.FEHLER,
                          meldung="Rechnungsnummer bereits gebucht.", referenz="RBKP 5105600123"),
            PREIS_FEHLER,
        ],
    )


@pytest.fixture()
def fakten_maverick() -> AuditFakten:
    return AuditFakten(
        rechnung=_rechnung(rechnungsnummer="TO-55821", lieferant_id="100777",
                           lieferant_name="Taunus Office Supplies KG", bestellnummer=None, format="PDF",
                           summe_netto=Decimal("349.90"), summe_steuer=Decimal("66.48"),
                           summe_brutto=Decimal("416.38"), positionen=[], skonto_prozent=None, skonto_frist=None),
        pruefungen=[
            Pruefergebnis(code=PruefCode.KEINE_BESTELLUNG, status=Pruefstatus.FEHLER,
                          meldung="Keine Bestellung referenziert (Maverick Buying)."),
            Pruefergebnis(code=PruefCode.LEITWEG_ID_FEHLT, status=Pruefstatus.WARNUNG,
                          meldung="Käuferreferenz fehlt (nur Hinweis bei PDF)."),
        ],
    )


def llm_json(fakten: AuditFakten, *, vermerk: str, naechster: str = "Einkauf klärt die Abweichung mit dem Lieferanten.",
             feststellungen: list[dict] | None = None, rueckfrage: dict | None | str = "auto",
             schritt: str | None = None, pfad: list[str] | None = None, urteil: str | None = None,
             rechnungsnummer: str | None = None) -> str:
    """Baut eine (gültige oder gezielt manipulierte) Mock-LLM-Antwort."""
    from src.agent import EMPFAENGER_JE_SCHRITT, URTEIL_JE_SCHRITT

    soll, soll_pfad = ermittle_soll_route(fakten)
    if rueckfrage == "auto":
        rueckfrage = None
        if soll in EMPFAENGER_JE_SCHRITT:
            rueckfrage = {
                "empfaenger": EMPFAENGER_JE_SCHRITT[soll].value,
                "betreff": f"Rückfrage zu Rechnung {fakten.rechnung.rechnungsnummer}",
                "text": "Guten Tag, wir bitten um Klärung der festgestellten Punkte. Mit freundlichen Grüßen [Kreditorenbuchhaltung]",
            }
    if feststellungen is None:
        feststellungen = [
            {"code": p.code.value, "beschreibung": f"Befund {p.code.value} laut Prüfprotokoll.", "betrag": None}
            for p in fakten.auffaellige_pruefungen
        ]
    return "```json\n" + json.dumps(
        {
            "rechnungsnummer": rechnungsnummer or fakten.rechnung.rechnungsnummer,
            "gesamturteil": urteil or URTEIL_JE_SCHRITT[soll].value,
            "workflow_schritt": schritt or soll.value,
            "bpmn_pfad": pfad or [s.value for s in soll_pfad],
            "audit_vermerk": vermerk,
            "feststellungen": feststellungen,
            "naechster_schritt": naechster,
            "rueckfrage": rueckfrage,
        },
        ensure_ascii=False,
    ) + "\n```"
