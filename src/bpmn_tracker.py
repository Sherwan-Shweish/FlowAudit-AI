"""
FlowAudit-AI · BPMN-2.0- & eEPK-Prozesstracker mit KPI-Engine
=============================================================

1. **Prozessdiagramme** als Mermaid.js-Code – wahlweise

   * im **BPMN-2.0-Stil**: Start-Ereignis, Tasks, XOR-Gateways, End-Ereignisse,
   * im klassischen **eEPK-Stil**: Ereignis → Funktion → Konnektor (XOR/AND)
     → Ereignis, ergänzt um Organisationseinheiten und Informationsobjekte.

   Der durchlaufene Pfad einer Rechnung wird hervorgehoben, der aktuelle
   Haltepunkt je nach Gesamturteil grün (Freigabe), gelb (Klärung) oder rot
   (Sperre) eingefärbt. Der Pfad stammt aus ``ermittle_soll_route`` und ist
   damit deterministisch.

2. **Prozess-KPIs** über den gesamten Rechnungsbestand (``RBKP``):
   Dunkelverarbeitungsquote, Durchlaufzeiten, Verteilung der Sperrgründe,
   Maverick-Buying-Quote sowie gesicherter, gefährdeter und verlorener Skonto.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal, Optional, Sequence

from pydantic import BaseModel

from .agent import (
    URTEIL_JE_SCHRITT,
    AuditFakten,
    Gesamturteil,
    WorkflowSchritt,
    ermittle_soll_route,
    fmt_de,
)

WS = WorkflowSchritt
Stil = Literal["bpmn", "epk"]

__all__ = ["prozessdiagramm", "Vorgang", "ProzessKPIs", "lade_vorgaenge", "berechne_kpis", "kpi_bericht"]


# ═════════════════════════════════════════════════════════════════════════════
# 1 · Prozessmodelle
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Knoten:
    id: str
    form: str      # Mermaid-Form mit Platzhalter {}
    text: str
    art: str = "prozess"  # prozess | org | info


@dataclass(frozen=True)
class Kante:
    von: str
    nach: str
    text: str = ""
    art: str = "fluss"    # fluss | zuordnung


@dataclass(frozen=True)
class Prozessmodell:
    richtung: str
    knoten: tuple[Knoten, ...]
    kanten: tuple[Kante, ...]
    besitz: dict  # WorkflowSchritt → Knoten-IDs, die bei Durchlauf markiert werden


def _k(id_: str, form: str, text: str, art: str = "prozess") -> Knoten:
    return Knoten(id_, form, text, art)


# Mermaid-Formen
START, TASK, GATEWAY, ENDE = '(("{}"))', '["{}"]', '{{"{}"}}', '((("{}")))'
EREIGNIS, FUNKTION, KONNEKTOR = '{{{{"{}"}}}}', '("{}")', '(("{}"))'
ORG, INFO = '(["{}"])', '[("{}")]'

BPMN = Prozessmodell(
    richtung="LR",
    knoten=(
        _k("S", START, "Rechnung<br/>eingegangen"),
        _k("T1", TASK, "Dublettenprüfung<br/>(RBKP)"),
        _k("G1", GATEWAY, "✕ Dublette?"),
        _k("E1", ENDE, "Sperre:<br/>Dublette"),
        _k("T2", TASK, "Formale Prüfung<br/>(§ 14 UStG · EN 16931)"),
        _k("G2", GATEWAY, "✕ Formfehler?"),
        _k("E2", ENDE, "Klärung<br/>Lieferant"),
        _k("T3", TASK, "Bestellbezug prüfen<br/>(EKKO)"),
        _k("G3", GATEWAY, "✕ Bestellung?"),
        _k("E3", ENDE, "Compliance:<br/>Maverick Buying"),
        _k("T4", TASK, "3-Way-Matching<br/>(EKPO · MATDOC · RSEG)"),
        _k("G4", GATEWAY, "✕ Ergebnis"),
        _k("E4", ENDE, "Sperre:<br/>Wareneingang"),
        _k("E5", ENDE, "Klärung Einkauf:<br/>Preis"),
        _k("E6", ENDE, "Klärung Einkauf:<br/>Menge"),
        _k("E7", ENDE, "Touchless-<br/>Freigabe"),
    ),
    kanten=(
        Kante("S", "T1"), Kante("T1", "G1"), Kante("G1", "E1", "ja"), Kante("G1", "T2", "nein"),
        Kante("T2", "G2"), Kante("G2", "E2", "ja"), Kante("G2", "T3", "nein"),
        Kante("T3", "G3"), Kante("G3", "E3", "nein"), Kante("G3", "T4", "ja"),
        Kante("T4", "G4"), Kante("G4", "E4", "WE fehlt"), Kante("G4", "E5", "Preis > Toleranz"),
        Kante("G4", "E6", "Menge > Bestellung"), Kante("G4", "E7", "alles OK"),
    ),
    besitz={
        WS.RECHNUNGSEINGANG: ["S"],
        WS.DUBLETTENPRUEFUNG: ["T1", "G1"],
        WS.FORMALE_PRUEFUNG: ["T2", "G2"],
        WS.BESTELLBEZUG_PRUEFEN: ["T3", "G3"],
        WS.DREI_WEGE_ABGLEICH: ["T4", "G4"],
        WS.SPERRE_DUBLETTE: ["E1"],
        WS.KLAERUNG_LIEFERANT_FORMFEHLER: ["E2"],
        WS.COMPLIANCE_MAVERICK_BUYING: ["E3"],
        WS.SPERRE_FEHLENDER_WARENEINGANG: ["E4"],
        WS.KLAERUNG_EINKAUF_PREIS: ["E5"],
        WS.KLAERUNG_EINKAUF_MENGE: ["E6"],
        WS.TOUCHLESS_FREIGABE: ["E7"],
    },
)

EPK = Prozessmodell(
    richtung="TD",
    knoten=(
        _k("EV0", EREIGNIS, "Rechnung ist eingegangen"),
        _k("F1", FUNKTION, "Dublette prüfen"),
        _k("X1", KONNEKTOR, "XOR"),
        _k("EVd", EREIGNIS, "Dublette erkannt"),
        _k("Fd", FUNKTION, "Zahlung sperren"),
        _k("EVde", EREIGNIS, "Rechnung ist gesperrt"),
        _k("EV1", EREIGNIS, "Keine Dublette"),
        _k("F2", FUNKTION, "Formale Prüfung<br/>§ 14 UStG"),
        _k("X2", KONNEKTOR, "XOR"),
        _k("EVf", EREIGNIS, "Formfehler festgestellt"),
        _k("Ff", FUNKTION, "Korrektur anfordern"),
        _k("EVfe", EREIGNIS, "Korrektur ist angefordert"),
        _k("EV2", EREIGNIS, "Rechnung ist formal korrekt"),
        _k("F3", FUNKTION, "Bestellbezug prüfen"),
        _k("X3", KONNEKTOR, "XOR"),
        _k("EVm", EREIGNIS, "Keine Bestellung vorhanden"),
        _k("Fm", FUNKTION, "Bedarf nachträglich klären"),
        _k("EVme", EREIGNIS, "Compliance-Fall ist dokumentiert"),
        _k("EV3", EREIGNIS, "Bestellung ist vorhanden"),
        _k("F4", FUNKTION, "3-Way-Matching durchführen"),
        _k("X4", KONNEKTOR, "XOR"),
        _k("EVw", EREIGNIS, "Wareneingang fehlt"),
        _k("Fw", FUNKTION, "Zahlsperre setzen"),
        _k("EVwe", EREIGNIS, "Rechnung wartet auf WE"),
        _k("EVp", EREIGNIS, "Preis außerhalb Toleranz"),
        _k("Fp", FUNKTION, "Preis klären"),
        _k("EVpe", EREIGNIS, "Preisklärung ist angestoßen"),
        _k("EVq", EREIGNIS, "Menge übersteigt Bestellung"),
        _k("Fq", FUNKTION, "Menge klären"),
        _k("EVqe", EREIGNIS, "Mengenklärung ist angestoßen"),
        _k("EVok", EREIGNIS, "3-Way-Match erfolgreich"),
        _k("A1", KONNEKTOR, "AND"),
        _k("Fb", FUNKTION, "Rechnung buchen"),
        _k("Fz", FUNKTION, "Zahlungsvorschlag erstellen"),
        _k("A2", KONNEKTOR, "AND"),
        _k("EVend", EREIGNIS, "Rechnung ist zahlungsbereit"),
        # Organisationseinheiten & Informationsobjekte (erweiterte EPK)
        _k("OKB", ORG, "Kreditorenbuchhaltung", "org"),
        _k("OSY", ORG, "FlowAudit-AI", "org"),
        _k("OEK", ORG, "Einkauf", "org"),
        _k("OLO", ORG, "Logistik", "org"),
        _k("OFB", ORG, "Fachabteilung", "org"),
        _k("IX", INFO, "E-Rechnung XML", "info"),
        _k("IR", INFO, "RBKP", "info"),
        _k("IB", INFO, "EKKO / EKPO", "info"),
        _k("IW", INFO, "MATDOC", "info"),
    ),
    kanten=(
        Kante("EV0", "F1"), Kante("F1", "X1"), Kante("X1", "EVd"), Kante("EVd", "Fd"), Kante("Fd", "EVde"),
        Kante("X1", "EV1"), Kante("EV1", "F2"), Kante("F2", "X2"),
        Kante("X2", "EVf"), Kante("EVf", "Ff"), Kante("Ff", "EVfe"),
        Kante("X2", "EV2"), Kante("EV2", "F3"), Kante("F3", "X3"),
        Kante("X3", "EVm"), Kante("EVm", "Fm"), Kante("Fm", "EVme"),
        Kante("X3", "EV3"), Kante("EV3", "F4"), Kante("F4", "X4"),
        Kante("X4", "EVw"), Kante("EVw", "Fw"), Kante("Fw", "EVwe"),
        Kante("X4", "EVp"), Kante("EVp", "Fp"), Kante("Fp", "EVpe"),
        Kante("X4", "EVq"), Kante("EVq", "Fq"), Kante("Fq", "EVqe"),
        Kante("X4", "EVok"), Kante("EVok", "A1"), Kante("A1", "Fb"), Kante("A1", "Fz"),
        Kante("Fb", "A2"), Kante("Fz", "A2"), Kante("A2", "EVend"),
        Kante("OSY", "F1", art="zuordnung"), Kante("IR", "F1", art="zuordnung"),
        Kante("OSY", "F2", art="zuordnung"), Kante("IX", "F2", art="zuordnung"),
        Kante("OSY", "F3", art="zuordnung"), Kante("IB", "F3", art="zuordnung"),
        Kante("OSY", "F4", art="zuordnung"), Kante("IW", "F4", art="zuordnung"),
        Kante("OKB", "Fd", art="zuordnung"), Kante("OKB", "Ff", art="zuordnung"),
        Kante("OFB", "Fm", art="zuordnung"), Kante("OLO", "Fw", art="zuordnung"),
        Kante("OEK", "Fp", art="zuordnung"), Kante("OEK", "Fq", art="zuordnung"),
        Kante("OKB", "Fb", art="zuordnung"),
    ),
    besitz={
        WS.RECHNUNGSEINGANG: ["EV0"],
        WS.DUBLETTENPRUEFUNG: ["F1", "X1"],
        WS.FORMALE_PRUEFUNG: ["EV1", "F2", "X2"],
        WS.BESTELLBEZUG_PRUEFEN: ["EV2", "F3", "X3"],
        WS.DREI_WEGE_ABGLEICH: ["EV3", "F4", "X4"],
        WS.SPERRE_DUBLETTE: ["EVd", "Fd", "EVde"],
        WS.KLAERUNG_LIEFERANT_FORMFEHLER: ["EVf", "Ff", "EVfe"],
        WS.COMPLIANCE_MAVERICK_BUYING: ["EVm", "Fm", "EVme"],
        WS.SPERRE_FEHLENDER_WARENEINGANG: ["EVw", "Fw", "EVwe"],
        WS.KLAERUNG_EINKAUF_PREIS: ["EVp", "Fp", "EVpe"],
        WS.KLAERUNG_EINKAUF_MENGE: ["EVq", "Fq", "EVqe"],
        WS.TOUCHLESS_FREIGABE: ["EVok", "A1", "Fb", "Fz", "A2", "EVend"],
    },
)

MODELLE: dict[str, Prozessmodell] = {"bpmn": BPMN, "epk": EPK}

_FARBEN = {
    "besucht": "fill:#dbeafe,stroke:#1d4ed8,color:#0b1f4d",
    "aktiv_FREIGABE": "fill:#d1f2d9,stroke:#1a7f37,stroke-width:3px,color:#0b3d1a",
    "aktiv_KLAERUNG": "fill:#fff4cc,stroke:#bf8700,stroke-width:3px,color:#4a3800",
    "aktiv_SPERRE": "fill:#fde2e1,stroke:#cf222e,stroke-width:3px,color:#5c0a0f",
    "inaktiv": "fill:#f6f8fa,stroke:#d0d7de,color:#8c959f",
    "org": "fill:#ffffff,stroke:#8250df,color:#3e1f79",
    "info": "fill:#ffffff,stroke:#57606a,color:#24292f",
}
_KANTE_PFAD = "stroke:#1d4ed8,stroke-width:3px"
_KANTE_INAKTIV = "stroke:#d0d7de,stroke-width:1px"
_KANTE_ZUORDNUNG = "stroke:#8c959f,stroke-width:1px,stroke-dasharray:3 3"


def _als_pfad(quelle: AuditFakten | Sequence[WorkflowSchritt] | None) -> list[WorkflowSchritt]:
    if quelle is None:
        return []
    if isinstance(quelle, AuditFakten):
        return ermittle_soll_route(quelle)[1]
    return [WS(s) for s in quelle]


def prozessdiagramm(
    quelle: AuditFakten | Sequence[WorkflowSchritt] | None = None,
    stil: Stil = "bpmn",
    titel: Optional[str] = None,
    mit_organisation: bool = True,
) -> str:
    """Erzeugt Mermaid-Code; ``quelle`` = Prüfstand oder BPMN-Pfad einer Rechnung."""
    if stil not in MODELLE:
        raise ValueError(f"Unbekannter Stil {stil!r} – erlaubt: 'bpmn', 'epk'.")
    modell = MODELLE[stil]
    pfad = _als_pfad(quelle)
    besucht = {k for s in pfad for k in modell.besitz.get(s, [])}
    aktiv: set[str] = set(modell.besitz.get(pfad[-1], [])) if pfad else set()
    urteil = URTEIL_JE_SCHRITT.get(pfad[-1]) if pfad else None

    knoten = [k for k in modell.knoten if mit_organisation or k.art == "prozess"]
    ids = {k.id for k in knoten}
    kanten = [k for k in modell.kanten if k.von in ids and k.nach in ids]

    zeilen: list[str] = []
    if titel:
        zeilen += ["---", f"title: {titel}", "---"]
    zeilen.append(f"flowchart {modell.richtung}")
    for k in knoten:
        zeilen.append(f"    {k.id}{k.form.format(k.text)}")
    for k in kanten:
        pfeil = "-.-" if k.art == "zuordnung" else "-->"
        label = f'|"{k.text}"|' if k.text else ""
        zeilen.append(f"    {k.von} {pfeil}{label} {k.nach}")

    for name, stil_css in _FARBEN.items():
        zeilen.append(f"    classDef {name} {stil_css}")
    klassen: dict[str, list[str]] = {}
    for k in knoten:
        if k.art != "prozess":
            klasse = k.art
        elif not pfad:
            klasse = None  # neutral: Mermaid-Standardstil
        elif k.id in aktiv:
            klasse = f"aktiv_{urteil.value}"
        elif k.id in besucht:
            klasse = "besucht"
        else:
            klasse = "inaktiv"
        if klasse:
            klassen.setdefault(klasse, []).append(k.id)
    for klasse, liste in klassen.items():
        zeilen.append(f"    class {','.join(liste)} {klasse}")

    pfad_idx = [i for i, k in enumerate(kanten) if k.art == "fluss" and k.von in besucht and k.nach in besucht]
    zuo_idx = [i for i, k in enumerate(kanten) if k.art == "zuordnung"]
    rest_idx = [i for i, k in enumerate(kanten) if k.art == "fluss" and i not in pfad_idx]
    if pfad_idx:
        zeilen.append(f"    linkStyle {','.join(map(str, pfad_idx))} {_KANTE_PFAD}")
        zeilen.append(f"    linkStyle {','.join(map(str, rest_idx))} {_KANTE_INAKTIV}")
    if zuo_idx:
        zeilen.append(f"    linkStyle {','.join(map(str, zuo_idx))} {_KANTE_ZUORDNUNG}")
    return "\n".join(zeilen) + "\n"


# ═════════════════════════════════════════════════════════════════════════════
# 2 · KPI-Engine
# ═════════════════════════════════════════════════════════════════════════════

def _r2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Vorgang:
    rechnungsnummer: str
    lieferant_id: str
    schritt: WorkflowSchritt
    brutto: Decimal
    eingang: datetime
    abschluss: Optional[datetime] = None
    skonto_prozent: Optional[Decimal] = None
    skonto_frist: Optional[date] = None

    @property
    def touchless(self) -> bool:
        return self.schritt is WS.TOUCHLESS_FREIGABE

    @property
    def durchlaufzeit_stunden(self) -> Optional[Decimal]:
        if self.abschluss is None:
            return None
        return Decimal(str((self.abschluss - self.eingang).total_seconds() / 3600))


class ProzessKPIs(BaseModel):
    stichtag: date
    anzahl_rechnungen: int
    anzahl_touchless: int
    dunkelverarbeitungsquote_prozent: Decimal
    anzahl_abgeschlossen: int
    anzahl_offen: int
    durchlaufzeit_avg_stunden: Optional[Decimal]
    durchlaufzeit_median_stunden: Optional[Decimal]
    durchlaufzeit_touchless_avg_minuten: Optional[Decimal]
    durchlaufzeit_manuell_avg_stunden: Optional[Decimal]
    sperrgruende: dict[str, int]
    sperrgruende_anteil_prozent: dict[str, Decimal]
    anzahl_maverick: int
    maverick_quote_prozent: Decimal
    volumen_brutto_eur: Decimal
    volumen_offen_eur: Decimal
    skonto_gesichert_eur: Decimal
    skonto_gefaehrdet_eur: Decimal
    skonto_verloren_eur: Decimal


def _prozent(teil: int, ganz: int) -> Decimal:
    return _r2(Decimal(teil) * 100 / Decimal(ganz)) if ganz else Decimal("0.00")


def berechne_kpis(vorgaenge: Sequence[Vorgang], stichtag: Optional[date] = None) -> ProzessKPIs:
    stichtag = stichtag or date.today()
    n = len(vorgaenge)
    touchless = [v for v in vorgaenge if v.touchless]
    manuell = [v for v in vorgaenge if not v.touchless]
    abgeschlossen = [v for v in vorgaenge if v.abschluss is not None]
    dlz = [v.durchlaufzeit_stunden for v in abgeschlossen]
    dlz_t = [v.durchlaufzeit_stunden for v in touchless if v.abschluss is not None]
    dlz_m = [v.durchlaufzeit_stunden for v in manuell if v.abschluss is not None]

    sperr: dict[str, int] = {}
    for v in manuell:
        sperr[v.schritt.value] = sperr.get(v.schritt.value, 0) + 1
    sperr = dict(sorted(sperr.items(), key=lambda x: (-x[1], x[0])))

    gesichert = gefaehrdet = verloren = Decimal("0")
    for v in vorgaenge:
        if v.skonto_prozent is None or v.skonto_frist is None:
            continue
        betrag = _r2(v.brutto * v.skonto_prozent / 100)
        if v.abschluss is not None:
            if v.abschluss.date() <= v.skonto_frist:
                gesichert += betrag
            else:
                verloren += betrag
        elif stichtag > v.skonto_frist:
            verloren += betrag
        else:
            gefaehrdet += betrag

    maverick = sum(1 for v in vorgaenge if v.schritt is WS.COMPLIANCE_MAVERICK_BUYING)

    def mittel(werte: list[Decimal], faktor: Decimal = Decimal(1)) -> Optional[Decimal]:
        return _r2(sum(werte, Decimal(0)) / len(werte) * faktor) if werte else None

    return ProzessKPIs(
        stichtag=stichtag,
        anzahl_rechnungen=n,
        anzahl_touchless=len(touchless),
        dunkelverarbeitungsquote_prozent=_prozent(len(touchless), n),
        anzahl_abgeschlossen=len(abgeschlossen),
        anzahl_offen=n - len(abgeschlossen),
        durchlaufzeit_avg_stunden=mittel(dlz),
        durchlaufzeit_median_stunden=_r2(Decimal(statistics.median(dlz))) if dlz else None,
        durchlaufzeit_touchless_avg_minuten=mittel(dlz_t, Decimal(60)),
        durchlaufzeit_manuell_avg_stunden=mittel(dlz_m),
        sperrgruende=sperr,
        sperrgruende_anteil_prozent={k: _prozent(c, len(manuell)) for k, c in sperr.items()},
        anzahl_maverick=maverick,
        maverick_quote_prozent=_prozent(maverick, n),
        volumen_brutto_eur=_r2(sum((v.brutto for v in vorgaenge), Decimal(0))),
        volumen_offen_eur=_r2(sum((v.brutto for v in vorgaenge if v.abschluss is None), Decimal(0))),
        skonto_gesichert_eur=_r2(gesichert),
        skonto_gefaehrdet_eur=_r2(gefaehrdet),
        skonto_verloren_eur=_r2(verloren),
    )


def lade_vorgaenge(db_pfad: Path | str) -> list[Vorgang]:
    """Liest den Rechnungsbestand (ohne stornierte Belege) aus RBKP – schreibgeschützt."""
    con = sqlite3.connect(f"{Path(db_pfad).resolve().as_uri()}?mode=ro", uri=True)
    try:
        zeilen = con.execute(
            "SELECT XBLNR, LIFNR, ZZ_WF_SCHRITT, RMWWR, ZZ_EINGANG, ZZ_ABSCHLUSS, ZZ_SKONTO_PROZ, ZZ_SKONTO_FRIST "
            "FROM RBKP WHERE STBLG IS NULL AND ZZ_WF_SCHRITT IS NOT NULL ORDER BY ZZ_EINGANG"
        ).fetchall()
    finally:
        con.close()
    return [
        Vorgang(
            rechnungsnummer=x, lieferant_id=lif, schritt=WS(s),
            brutto=Decimal(str(round(b, 2))),
            eingang=datetime.fromisoformat(e),
            abschluss=datetime.fromisoformat(a) if a else None,
            skonto_prozent=Decimal(str(sp)) if sp is not None else None,
            skonto_frist=date.fromisoformat(sf) if sf else None,
        )
        for x, lif, s, b, e, a, sp, sf in zeilen
    ]


_SCHRITT_TEXT = {
    "SPERRE_DUBLETTE": "Sperre: Dublette",
    "KLAERUNG_LIEFERANT_FORMFEHLER": "Klärung Lieferant (Formfehler)",
    "COMPLIANCE_MAVERICK_BUYING": "Compliance: Maverick Buying",
    "SPERRE_FEHLENDER_WARENEINGANG": "Sperre: Wareneingang",
    "KLAERUNG_EINKAUF_PREIS": "Klärung Einkauf: Preis",
    "KLAERUNG_EINKAUF_MENGE": "Klärung Einkauf: Menge",
}


def kpi_bericht(k: ProzessKPIs) -> str:
    """Kompakter Markdown-Bericht der Prozess-KPIs."""
    def z(x: Optional[Decimal], einheit: str) -> str:
        return f"{fmt_de(x)} {einheit}" if x is not None else "–"

    zeilen = [
        f"**Prozess-KPIs Purchase-to-Pay** (Stichtag {k.stichtag:%d.%m.%Y}, {k.anzahl_rechnungen} Rechnungen)",
        "",
        "| Kennzahl | Wert |",
        "|---|---|",
        f"| Dunkelverarbeitungsquote | **{fmt_de(k.dunkelverarbeitungsquote_prozent)} %** ({k.anzahl_touchless} von {k.anzahl_rechnungen}) |",
        f"| Ø Durchlaufzeit gesamt / Median | {z(k.durchlaufzeit_avg_stunden, 'h')} / {z(k.durchlaufzeit_median_stunden, 'h')} |",
        f"| Ø Durchlaufzeit Touchless | {z(k.durchlaufzeit_touchless_avg_minuten, 'min')} |",
        f"| Ø Durchlaufzeit mit Klärung | {z(k.durchlaufzeit_manuell_avg_stunden, 'h')} |",
        f"| Offene Vorgänge (Volumen) | {k.anzahl_offen} ({fmt_de(k.volumen_offen_eur)} €) |",
        f"| Maverick-Buying-Quote | {fmt_de(k.maverick_quote_prozent)} % ({k.anzahl_maverick} Rechnungen) |",
        f"| Skonto gesichert / gefährdet / verloren | {fmt_de(k.skonto_gesichert_eur)} € / {fmt_de(k.skonto_gefaehrdet_eur)} € / {fmt_de(k.skonto_verloren_eur)} € |",
        "",
        "| Sperr-/Klärungsgrund | Anzahl | Anteil |",
        "|---|---|---|",
    ]
    for schritt, anzahl in k.sperrgruende.items():
        zeilen.append(f"| {_SCHRITT_TEXT.get(schritt, schritt)} | {anzahl} | {fmt_de(k.sperrgruende_anteil_prozent[schritt])} % |")
    return "\n".join(zeilen) + "\n"


def urteilsfarbe(urteil: Optional[Gesamturteil]) -> str:
    return {Gesamturteil.FREIGABE: "#1a7f37", Gesamturteil.KLAERUNG: "#bf8700",
            Gesamturteil.SPERRE: "#cf222e"}.get(urteil, "#57606a")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    from .matching_engine import lese_stichtag

    ap = argparse.ArgumentParser(description="FlowAudit-AI: Prozess-KPIs und Diagramme")
    ap.add_argument("--db", default="data/flowaudit_erp.db")
    ap.add_argument("--diagramm", help="XML-Rechnung, für die ein Diagramm erzeugt werden soll")
    ap.add_argument("--stil", choices=["bpmn", "epk"], default="bpmn")
    a = ap.parse_args()
    if a.diagramm:
        from .matching_engine import MatchingEngine, verarbeite_datei

        e = verarbeite_datei(Path(a.diagramm), MatchingEngine(a.db))
        print(prozessdiagramm(e.fakten, a.stil, titel=f"Rechnung {e.fakten.rechnung.rechnungsnummer}"))
    else:
        print(kpi_bericht(berechne_kpis(lade_vorgaenge(a.db), lese_stichtag(a.db))))
