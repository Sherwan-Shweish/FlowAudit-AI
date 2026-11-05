"""Klärfall-Workflow: Status, Zuständigkeit, Rückfrage und namentliche Freigabe – mit Vier-Augen-Prinzip ab Betragsgrenze.

Ablauf (deterministisch, append-only):

    offen ──Rückfrage freigegeben (Name)──▶ in Klärung ──Lösung + Freigabe (Name)──▶ gelöst
                                                     └─ ab Betragsgrenze: zweite Freigabe durch eine andere Person

* Ein Klärfall entsteht für jede Rechnung, die nicht touchless läuft, für abgewiesene Dateien und für Rechnungen
  mit einem zahlungssperrenden Präventionstreffer (z. B. kürzlich geänderte Bankverbindung).
* Die Rückfrage an Lieferant bzw. Einkauf formuliert das Sprachmodell (oder – offline – das Regelwerk). Vor dem
  Versand wird **jede Zahl und jedes Datum** gegen Prüfprotokoll und SAP-Belege geprüft; ein bearbeiteter Text
  wird erneut geprüft. Unbelegte Zahlen blockieren den Versand.
* Jede Zustandsänderung braucht einen Namen und wird als Ereignis protokolliert; Ereignisse werden nie geändert.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from .agent import (
    EMPFAENGER_JE_SCHRITT,
    URTEIL_JE_SCHRITT,
    Empfaenger,
    FaktencheckErgebnis,
    FaktenPruefer,
    WorkflowSchritt,
    fmt_de,
)
from .assistent import SAP_KONSTANTEN, Vorgang
from .praevention import Risikohinweis

Status = Literal["offen", "in Klärung", "gelöst"]
STATUS: tuple[Status, ...] = ("offen", "in Klärung", "gelöst")
VIER_AUGEN_GRENZE = Decimal("2500")   # € brutto; Demo-Wert – in der Praxis meist 5.000–10.000 €

ZUSTAENDIG = {
    Empfaenger.LIEFERANT: "Kreditorenbuchhaltung (Rückfrage an Lieferant)",
    Empfaenger.EINKAUF: "Einkauf",
    Empfaenger.FACHABTEILUNG: "Fachbereich / Bedarfsträger",
    Empfaenger.WARENEINGANG: "Wareneingang / Logistik",
}
GRUND = {
    "SPERRE_DUBLETTE": "Dublette – Rechnung bereits gebucht",
    "KLAERUNG_LIEFERANT_FORMFEHLER": "Formfehler (§ 14 UStG / EN 16931)",
    "COMPLIANCE_MAVERICK_BUYING": "Rechnung ohne Bestellung (Maverick Buying)",
    "SPERRE_FEHLENDER_WARENEINGANG": "Wareneingang fehlt oder unvollständig",
    "KLAERUNG_EINKAUF_PREIS": "Preisabweichung außerhalb der Toleranz",
    "KLAERUNG_EINKAUF_MENGE": "Mehrmenge gegenüber Bestellung",
    "ABGELEHNT": "Keine verarbeitbare E-Rechnung",
    "PRAEVENTION": "Präventionsregel – Zahlung gesperrt bis zur dokumentierten Prüfung",
}


class FreigabeFehler(ValueError):
    """Unzulässiger Schritt im Klärfall (fehlender Name, gleiche Person bei Vier-Augen, falscher Status)."""


@dataclass(frozen=True)
class Ereignis:
    zeit: datetime
    art: str
    person: str
    text: str


@dataclass(frozen=True)
class Klaerfall:
    id: str                       # Dateiname des Belegs
    rechnungsnummer: str
    lieferant: str
    brutto: Decimal
    grund_code: str               # Workflow-Schritt, ABGELEHNT oder PRAEVENTION
    zustaendig: str
    vier_augen: bool
    rueckfrage_empfaenger: str
    rueckfrage_betreff: str
    rueckfrage_text: str
    rueckfrage_quelle: str        # "LLM · <Modell>" oder "Regelwerk"
    risiken: tuple[str, ...] = ()
    status: Status = "offen"
    freigaben: tuple[tuple[str, datetime], ...] = ()
    loesung: str | None = None
    verlauf: tuple[Ereignis, ...] = field(default_factory=tuple)

    @property
    def grund(self) -> str:
        return GRUND.get(self.grund_code, self.grund_code)

    @property
    def wartet_auf_zweite_freigabe(self) -> bool:
        return self.status == "in Klärung" and self.vier_augen and len(self.freigaben) == 1

    @property
    def freigegeben_von(self) -> list[str]:
        return [name for name, _ in self.freigaben]

    def _mit(self, art: str, person: str, text: str, **aenderungen) -> Klaerfall:
        ereignis = Ereignis(datetime.now(UTC), art, person, text)
        return replace(self, verlauf=(*self.verlauf, ereignis), **aenderungen)


def _name(person: str | None) -> str:
    if not person or not person.strip():
        raise FreigabeFehler("Freigabe nur mit Namen – bitte den eigenen Namen eintragen (Human-in-the-Loop).")
    return person.strip()


def lege_an(v: Vorgang, risiken: list[Risikohinweis] | None = None,
            grenze: Decimal = VIER_AUGEN_GRENZE) -> Klaerfall | None:
    """Klärfall für einen geprüften Beleg – ``None``, wenn die Rechnung ohne Eingriff bezahlt werden darf."""
    sperrend = [h for h in (risiken or []) if h.sperrt_zahlung]
    if v.abgelehnt or v.fakten is None:
        aktion = v.aktionen[0] if v.aktionen else None
        return Klaerfall(
            id=v.datei, rechnungsnummer=v.datei, lieferant="Absender der Datei", brutto=Decimal("0"),
            grund_code="ABGELEHNT", zustaendig="Kreditorenbuchhaltung", vier_augen=False,
            rueckfrage_empfaenger=aktion.empfaenger if aktion else "Absender",
            rueckfrage_betreff=aktion.betreff if aktion else "E-Rechnung erforderlich",
            rueckfrage_text=aktion.text if aktion else "", rueckfrage_quelle="Regelwerk",
            verlauf=(Ereignis(datetime.now(UTC), "ANGELEGT", "System", v.ablehnungsgrund or ""),))
    if v.schritt is WorkflowSchritt.TOUCHLESS_FREIGABE and not sperrend:
        return None
    r = v.fakten.rechnung
    brutto = r.summe_brutto or Decimal("0")
    if v.schritt is WorkflowSchritt.TOUCHLESS_FREIGABE:          # nur Präventionstreffer
        grund, zustaendig = "PRAEVENTION", "Kreditorenbuchhaltung (Rückruf beim Lieferanten)"
        betreff = f"Bestätigung Ihrer Bankverbindung – Rechnung {r.rechnungsnummer}"
        text = (f"Guten Tag,\n\nzu Ihrer Rechnung {r.rechnungsnummer} über {fmt_de(brutto)} EUR liegt uns eine kürzlich "
                "geänderte Bankverbindung vor. Bitte bestätigen Sie die Änderung telefonisch unter der uns bekannten "
                "Rufnummer; bis dahin ist die Zahlung angehalten.\n\nMit freundlichen Grüßen\nKreditorenbuchhaltung")
        empfaenger, quelle = "Lieferant (telefonischer Rückruf)", "Regelwerk"
    else:
        grund = v.schritt.value
        ziel = EMPFAENGER_JE_SCHRITT.get(v.schritt)
        zustaendig = ZUSTAENDIG.get(ziel, "Kreditorenbuchhaltung") if ziel else "Kreditorenbuchhaltung"
        rf = v.audit.bericht.rueckfrage if v.audit else None
        aktion = v.aktionen[0] if v.aktionen else None
        betreff = rf.betreff if rf else (aktion.betreff if aktion else f"Rückfrage zu Rechnung {r.rechnungsnummer}")
        text = rf.text if rf else (aktion.text if aktion else "")
        empfaenger = rf.empfaenger.value.title() if rf else (aktion.empfaenger if aktion else zustaendig)
        quelle = f"LLM · {v.audit.modell}" if v.audit and v.audit.quelle == "LLM" else "Regelwerk"
    return Klaerfall(
        id=v.datei, rechnungsnummer=r.rechnungsnummer, lieferant=r.lieferant_name, brutto=brutto, grund_code=grund,
        zustaendig=zustaendig, vier_augen=brutto >= grenze, rueckfrage_empfaenger=empfaenger,
        rueckfrage_betreff=betreff, rueckfrage_text=text, rueckfrage_quelle=quelle,
        risiken=tuple(h.titel for h in risiken or []),
        verlauf=(Ereignis(datetime.now(UTC), "ANGELEGT", "System", GRUND.get(grund, grund)),),
    )


#: Feste Bezugsgrößen des Rechtsrahmens, die in Standardtexten vorkommen dürfen
#: (Empfangspflicht für E-Rechnungen seit 01.01.2025, Containerformat PDF/A-3).
RECHTSRAHMEN_DATEN = (date(2025, 1, 1),)
RECHTSRAHMEN_ZAHLEN = (Decimal(3),)


def pruefe_rueckfrage(v: Vorgang, text: str) -> FaktencheckErgebnis:
    """Faktenprüfung des (ggf. bearbeiteten) Rückfragetextes gegen Prüfprotokoll und SAP-Belege."""
    if v.fakten is None:
        quellen: tuple = (v.datei, [d.isoformat() for d in RECHTSRAHMEN_DATEN])
        zusatz = [*SAP_KONSTANTEN, *RECHTSRAHMEN_ZAHLEN]
    else:
        daten = v.fakten.model_dump()
        for p in daten["pruefungen"]:
            p.pop("meldung", None)
        quellen = (daten, v.sap.model_dump() if v.sap else {})
        zusatz = [Decimal(len(v.fakten.pruefungen)), Decimal(len(v.fakten.rechnung.positionen)), *SAP_KONSTANTEN]
    return FaktenPruefer.aus_quelldaten(*quellen, zusatzzahlen=zusatz).pruefe_text(text, "rueckfrage")


def sende_rueckfrage(fall: Klaerfall, person: str, text: str, faktencheck: FaktencheckErgebnis) -> Klaerfall:
    name = _name(person)
    if fall.status != "offen":
        raise FreigabeFehler(f"Rückfrage nur im Status „offen“ möglich (aktuell: {fall.status}).")
    if not faktencheck.bestanden:
        raise FreigabeFehler("Rückfrage enthält nicht belegte Zahlen oder Daten: " + faktencheck.als_feedback())
    return fall._mit("RÜCKFRAGE FREIGEGEBEN", name, f"an {fall.rueckfrage_empfaenger}: {fall.rueckfrage_betreff}",
                     status="in Klärung", rueckfrage_text=text)


def loese(fall: Klaerfall, person: str, loesung: str) -> Klaerfall:
    """Erste (bzw. einzige) Freigabe mit Lösungsvermerk."""
    name = _name(person)
    if fall.status != "in Klärung" or fall.freigaben:
        raise FreigabeFehler("Lösung nur im Status „in Klärung“ und vor der ersten Freigabe möglich.")
    if not loesung or len(loesung.strip()) < 10:
        raise FreigabeFehler("Bitte die Lösung in einem Satz dokumentieren (mindestens 10 Zeichen).")
    freigaben = ((name, datetime.now(UTC)),)
    if fall.vier_augen:
        return fall._mit("FREIGABE 1/2", name, loesung.strip(), freigaben=freigaben, loesung=loesung.strip())
    return fall._mit("GELÖST", name, loesung.strip(), freigaben=freigaben, loesung=loesung.strip(), status="gelöst")


def zweite_freigabe(fall: Klaerfall, person: str) -> Klaerfall:
    name = _name(person)
    if not fall.wartet_auf_zweite_freigabe:
        raise FreigabeFehler("Keine zweite Freigabe ausstehend.")
    if name.casefold() == fall.freigaben[0][0].casefold():
        raise FreigabeFehler("Vier-Augen-Prinzip: Die zweite Freigabe muss eine andere Person erteilen.")
    return fall._mit("FREIGABE 2/2 · GELÖST", name, f"bestätigt Lösung von {fall.freigaben[0][0]}",
                     freigaben=(*fall.freigaben, (name, datetime.now(UTC))), status="gelöst")


def zahlungsfreigabe(fall: Klaerfall | None) -> bool:
    """Darf die Rechnung bezahlt werden? Ohne Klärfall: ja; sonst nur gelöst – Dubletten und abgewiesene nie."""
    if fall is None:
        return True
    return fall.status == "gelöst" and fall.grund_code not in ("SPERRE_DUBLETTE", "ABGELEHNT")


def urteil(fall: Klaerfall) -> str:
    if fall.grund_code in ("ABGELEHNT",):
        return "ABGELEHNT"
    if fall.grund_code == "PRAEVENTION":
        return "SPERRE"
    return URTEIL_JE_SCHRITT[WorkflowSchritt(fall.grund_code)].value


__all__ = ["STATUS", "VIER_AUGEN_GRENZE", "Klaerfall", "Ereignis", "FreigabeFehler", "lege_an", "pruefe_rueckfrage",
           "sende_rueckfrage", "loese", "zweite_freigabe", "zahlungsfreigabe", "urteil"]
