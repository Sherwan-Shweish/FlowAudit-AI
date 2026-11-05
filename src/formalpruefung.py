"""Formalprüfung als verständliche Checkliste: Pflichtangaben nach § 14 Abs. 4 UStG und EN-16931-Pflichtfelder.

Die eigentliche Prüfung (Routing, Prüfprotokoll) bleibt in ``einvoice_parser.validiere_rechnung``. Dieses Modul
übersetzt sie in eine Liste, die eine Kreditorenbuchhaltung ohne XML-Kenntnisse lesen kann:

* je Pflichtangabe: Rechtsgrundlage, Fundstelle (Business Term, BT) und Befund in Klartext,
* je Fehler: Folge („Vorsteuerabzug gefährdet“) und konkrete Korrekturbitte an den Lieferanten.

Deterministisch, ohne LLM. Stand der Rechtsgrundlagen: siehe README („Stand prüfen“) – keine Steuerberatung.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .agent import fmt_de
from .einvoice_parser import GeparsteRechnung

Status = Literal["erfuellt", "fehlt", "ungueltig", "hinweis", "entfaellt"]

_UST_ID = re.compile(r"[A-Z]{2}[0-9A-Z+*.]{2,12}")
_FOLGE_VORSTEUER = "Ohne diese Angabe ist der Vorsteuerabzug aus der Rechnung gefährdet."


@dataclass(frozen=True)
class Pflichtangabe:
    gruppe: Literal["§ 14 Abs. 4 UStG", "EN 16931", "XRechnung (CIUS)"]
    nummer: str                 # z. B. "Nr. 2" oder "BR-09"
    bezeichnung: str
    fundstelle: str             # Business Terms der EN 16931
    status: Status
    befund: str                 # Klartext, was gefunden wurde
    folge: str = ""             # nur bei Fehlern: warum das wichtig ist
    korrektur: str = ""         # nur bei Fehlern: was der Lieferant tun soll

    @property
    def ist_fehler(self) -> bool:
        return self.status in ("fehlt", "ungueltig")


def _eur(x: Decimal | None) -> str:
    return f"{fmt_de(x)} €" if x is not None else "–"


def _satz(x: Decimal | None) -> str:
    return "–" if x is None else (fmt_de(x, 0) if x == x.to_integral_value() else fmt_de(x, 1))


def _leer(wert: object) -> bool:
    return wert is None or (isinstance(wert, str) and not wert.strip())


def _eintrag(gruppe, nummer, bezeichnung, fundstelle, ok: bool, befund_ok: str, befund_fehler: str,  # noqa: ANN001
             folge: str = _FOLGE_VORSTEUER, korrektur: str = "", status_fehler: Status = "fehlt") -> Pflichtangabe:
    if ok:
        return Pflichtangabe(gruppe, nummer, bezeichnung, fundstelle, "erfuellt", befund_ok)
    return Pflichtangabe(gruppe, nummer, bezeichnung, fundstelle, status_fehler, befund_fehler, folge,
                         korrektur or f"Berichtigte Rechnung mit {bezeichnung} anfordern.")


def _adresse_ok(adresse) -> bool:  # noqa: ANN001
    return not any(_leer(x) for x in (adresse.strasse, adresse.plz, adresse.ort))


def _ustg(r: GeparsteRechnung) -> list[Pflichtangabe]:
    g = "§ 14 Abs. 4 UStG"
    v, k = r.verkaeufer, r.kaeufer
    liste = [
        _eintrag(g, "Nr. 1", "Name und Anschrift des Lieferanten", "BT-27, BG-5",
                 not _leer(v.name) and _adresse_ok(v.adresse),
                 f"{v.name}, {v.adresse.plz or ''} {v.adresse.ort or ''}".strip(", "),
                 "Name oder Anschrift des Lieferanten unvollständig."),
        _eintrag(g, "Nr. 1", "Name und Anschrift des Leistungsempfängers", "BT-44, BG-8",
                 not _leer(k.name) and _adresse_ok(k.adresse),
                 f"{k.name}, {k.adresse.plz or ''} {k.adresse.ort or ''}".strip(", "),
                 "Name oder Anschrift des Rechnungsempfängers unvollständig."),
    ]
    uid = (v.ust_id or "").replace(" ", "").upper()
    if uid and not _UST_ID.fullmatch(uid):
        liste.append(Pflichtangabe(g, "Nr. 2", "USt-IdNr. oder Steuernummer", "BT-31 / BT-32", "ungueltig",
                                   f"USt-IdNr. „{v.ust_id}“ hat kein gültiges Format.", _FOLGE_VORSTEUER,
                                   "Lieferant um Bestätigung der korrekten USt-IdNr. und berichtigte Rechnung bitten."))
    else:
        liste.append(_eintrag(g, "Nr. 2", "USt-IdNr. oder Steuernummer", "BT-31 / BT-32",
                              bool(uid or v.steuernummer),
                              f"USt-IdNr. {v.ust_id}" if uid else f"Steuernummer {v.steuernummer}",
                              "Weder USt-IdNr. noch Steuernummer des Lieferanten angegeben.",
                              korrektur="Berichtigte Rechnung mit USt-IdNr. oder Steuernummer anfordern."))
    liste += [
        _eintrag(g, "Nr. 3", "Ausstellungsdatum", "BT-2", r.rechnungsdatum is not None,
                 f"{r.rechnungsdatum:%d.%m.%Y}" if r.rechnungsdatum else "", "Rechnungsdatum fehlt."),
        _eintrag(g, "Nr. 4", "Fortlaufende Rechnungsnummer", "BT-1", not _leer(r.rechnungsnummer),
                 r.rechnungsnummer or "", "Rechnungsnummer fehlt."),
    ]
    unvollstaendig = [p.id for p in r.positionen if _leer(p.bezeichnung) or p.menge is None]
    liste.append(_eintrag(g, "Nr. 5", "Menge und Art der Leistung", "BG-25: BT-129, BT-153",
                          bool(r.positionen) and not unvollstaendig,
                          f"{len(r.positionen)} Position(en) mit Menge und Bezeichnung",
                          "Keine Positionen vorhanden." if not r.positionen
                          else f"Menge oder Bezeichnung fehlt in Position {', '.join(unvollstaendig)}."))
    zeitraum = r.leistungszeitraum is not None and any(r.leistungszeitraum)
    liste.append(_eintrag(g, "Nr. 6", "Zeitpunkt der Lieferung oder Leistung", "BT-72 / BG-14",
                          r.leistungsdatum is not None or zeitraum,
                          f"Leistungsdatum {r.leistungsdatum:%d.%m.%Y}" if r.leistungsdatum else "Leistungszeitraum",
                          "Weder Leistungsdatum noch Leistungszeitraum angegeben."))
    liste.append(_eintrag(g, "Nr. 7", "Entgelt nach Steuersätzen aufgeschlüsselt", "BG-23: BT-116", bool(r.steuern),
                          f"{len(r.steuern)} Steuersatz/-sätze aufgeschlüsselt",
                          "Keine Aufschlüsselung des Entgelts nach Steuersätzen."))
    if r.skonto:
        liste.append(Pflichtangabe(g, "Nr. 7", "Im Voraus vereinbarte Entgeltminderung (Skonto)", "BT-20", "erfuellt",
                                   f"{_satz(r.skonto.prozent)} % Skonto innerhalb {r.skonto.tage} Tagen vereinbart"))
    saetze_ok = bool(r.steuern) and all(s.satz is not None and s.betrag is not None for s in r.steuern)
    liste.append(_eintrag(g, "Nr. 8", "Steuersatz und Steuerbetrag", "BT-119, BT-117", saetze_ok,
                          ", ".join(f"{_satz(s.satz)} % = {_eur(s.betrag)}" for s in r.steuern),
                          "Steuersatz oder Steuerbetrag fehlt in der Aufschlüsselung."))
    liste.append(Pflichtangabe(g, "Nr. 9–10", "Aufbewahrungshinweis bzw. Angabe „Gutschrift“", "–", "entfaellt",
                               "nicht einschlägig (Eingangsrechnung zwischen Unternehmen, keine Gutschrift)"))
    return liste


def _en16931(r: GeparsteRechnung, rechenfehler: bool) -> list[Pflichtangabe]:
    g = "EN 16931"
    hinweis = "Die Rechnung entspricht nicht der Norm und gilt nicht als E-Rechnung im Sinne des § 14 UStG."
    pruefungen: list[tuple[str, str, str, Callable[[], bool], str]] = [
        ("BR-01", "Spezifikationskennung", "BT-24", lambda: not _leer(r.spezifikation), r.spezifikation or ""),
        ("BR-04", "Rechnungsart", "BT-3", lambda: not _leer(r.rechnungsart), r.rechnungsart or ""),
        ("BR-05", "Währung", "BT-5", lambda: not _leer(r.waehrung), r.waehrung or ""),
        ("BR-09", "Land des Lieferanten", "BT-40", lambda: not _leer(r.verkaeufer.adresse.land),
         r.verkaeufer.adresse.land or ""),
        ("BR-11", "Land des Käufers", "BT-55", lambda: not _leer(r.kaeufer.adresse.land), r.kaeufer.adresse.land or ""),
        ("BR-13/14", "Gesamtbeträge netto und brutto", "BT-109, BT-112",
         lambda: r.summen.netto is not None and r.summen.brutto is not None,
         f"netto {_eur(r.summen.netto)}, brutto {_eur(r.summen.brutto)}"),
        ("BR-15", "Zahlbetrag", "BT-115", lambda: r.summen.zahlbetrag is not None or r.summen.brutto is not None,
         _eur(r.summen.zahlbetrag if r.summen.zahlbetrag is not None else r.summen.brutto)),
        ("BR-21–26", "Positionsdaten (Kennung, Menge, Einheit, Betrag, Bezeichnung, Preis)",
         "BT-126, BT-129, BT-130, BT-131, BT-153, BT-146",
         lambda: bool(r.positionen) and all(
             p.id and p.menge is not None and p.einheit_code and p.nettobetrag is not None and p.bezeichnung
             and p.preis is not None for p in r.positionen),
         f"{len(r.positionen)} vollständige Position(en)"),
    ]
    liste = [_eintrag(g, nr, bez, bt, pruef(), ok_text, f"{bez} fehlt oder ist unvollständig.", hinweis)
             for nr, bez, bt, pruef, ok_text in pruefungen]
    liste.append(_eintrag(
        g, "BR-CO-10/13/15", "Rechnerische Richtigkeit", "BT-106, BT-109, BT-110, BT-112", not rechenfehler,
        "Positionen, Steuer und Summen stimmen auf den Cent", "Summen der Rechnung sind rechnerisch nicht stimmig.",
        "Ein zu hoch ausgewiesener Steuerbetrag wird nach § 14c Abs. 1 UStG geschuldet; Vorsteuer ist nur in "
        "gesetzlicher Höhe abziehbar.", "Berichtigte Rechnung mit korrekt berechneter Steuer und Gesamtsumme anfordern.",
        status_fehler="ungueltig"))
    return liste


def _xrechnung(r: GeparsteRechnung) -> list[Pflichtangabe]:
    if not r.ist_xrechnung:
        return [Pflichtangabe("XRechnung (CIUS)", "BR-DE-15", "Käuferreferenz / Leitweg-ID", "BT-10",
                              "erfuellt" if r.kaeuferreferenz else "hinweis",
                              r.kaeuferreferenz or "ZUGFeRD: nur Pflicht, wenn der Empfänger sie vorgibt")]
    return [_eintrag("XRechnung (CIUS)", "BR-DE-15", "Käuferreferenz / Leitweg-ID", "BT-10",
                     not _leer(r.kaeuferreferenz), r.kaeuferreferenz or "",
                     "Käuferreferenz fehlt – für XRechnung verpflichtend.",
                     "Ohne Käuferreferenz kann die Rechnung nicht automatisch zugeordnet werden.",
                     "Rechnung mit unserer Käuferreferenz (BT-10) erneut senden.")]


def pflichtangaben(r: GeparsteRechnung, rechenfehler: bool = False) -> list[Pflichtangabe]:
    """Vollständige Checkliste (§ 14 Abs. 4 UStG, EN 16931, XRechnung-CIUS) für eine geparste E-Rechnung."""
    return _ustg(r) + _en16931(r, rechenfehler) + _xrechnung(r)


def fehlerliste(angaben: list[Pflichtangabe]) -> list[str]:
    """Verständliche Fehlerliste (ein Satz je Fehler) – Grundlage der Rückfrage an den Lieferanten."""
    return [f"{a.befund} ({a.gruppe} {a.nummer}, {a.fundstelle}) – {a.folge} {a.korrektur}".strip()
            for a in angaben if a.ist_fehler]


def zusammenfassung(angaben: list[Pflichtangabe]) -> tuple[int, int, int]:
    """(erfüllt, Fehler, geprüft) – nicht einschlägige Angaben zählen nicht mit."""
    geprueft = [a for a in angaben if a.status != "entfaellt"]
    fehler = sum(a.ist_fehler for a in geprueft)
    return sum(a.status == "erfuellt" for a in geprueft), fehler, len(geprueft)


__all__ = ["Pflichtangabe", "pflichtangaben", "fehlerliste", "zusammenfassung"]
