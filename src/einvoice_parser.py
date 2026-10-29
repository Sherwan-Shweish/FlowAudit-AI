"""
FlowAudit-AI · E-Rechnungs-Parser & § 14 UStG-Validator
=======================================================

Liest strukturierte E-Rechnungen nach EN 16931 in beiden zulässigen Syntaxen

* **UBL 2.1** (``urn:oasis:names:specification:ubl:schema:xsd:Invoice-2``) –
  typisch für XRechnung,
* **UN/CEFACT CII D16B** (``CrossIndustryInvoice:100``) – typisch für ZUGFeRD /
  Factur-X und XRechnung-CII,

und prüft sie deterministisch:

1. **XML-Sicherheit** – Größenlimit, kein DOCTYPE/ENTITY (Schutz vor XXE und
   „Billion Laughs“), Parsing ausschließlich über ``defusedxml``.
2. **Pflichtangaben nach § 14 Abs. 4 UStG** – Name/Anschrift beider Parteien,
   USt-IdNr. oder Steuernummer, Ausstellungsdatum, Rechnungsnummer, Menge und
   Art der Leistung, Leistungsdatum, Steueraufschlüsselung; dazu die
   Käuferreferenz BT-10 (Leitweg-ID) für XRechnung.
3. **Rechnerische Prüfung** auf Positions- und Kopfebene mit kaufmännischer
   Rundungstoleranz von 0,01 €.
4. **Steuersatz-Logik** – zulässig sind 19 % / 7 % (Kategorie S) bzw. 0 %.
5. **Zahlungsbedingungen** – Fälligkeit und Skonto (KoSIT-Konvention
   ``#SKONTO#TAGE=14#PROZENT=2.00#``, CII-Discount-Terms, Freitext).
6. **Prompt-Injection-Erkennung** in Freitexten (BT-22, BT-20, Positionstexte).

Die BT-Nummern (Business Terms) beziehen sich auf das semantische Datenmodell
der EN 16931-1.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Literal, Optional
from xml.etree.ElementTree import Element, ParseError

from pydantic import BaseModel, Field

from .agent import Pruefergebnis, PruefCode, Pruefstatus

try:
    import defusedxml.ElementTree as SafeET
    from defusedxml import DefusedXmlException
except ImportError as exc:  # pragma: no cover
    raise ImportError("Paket 'defusedxml' fehlt: pip install defusedxml") from exc

__all__ = [
    "EInvoiceFehler",
    "XMLSicherheitsFehler",
    "UnbekanntesFormat",
    "GeparsteRechnung",
    "lese_rechnung",
    "validiere_rechnung",
    "erkenne_prompt_injection",
    "MAX_DATEIGROESSE",
    "RUNDUNGSTOLERANZ",
]

MAX_DATEIGROESSE = 10 * 1024 * 1024          # 10 MB
RUNDUNGSTOLERANZ = Decimal("0.01")           # kaufmännische Toleranz in EUR
ZULAESSIGE_SAETZE_S = {Decimal("19"), Decimal("7")}
NULLSATZ_KATEGORIEN = {"Z", "E", "AE", "K", "G", "O"}

NS_UBL = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
NS_CII = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

#: UN/ECE Rec. 20 → Einheit wie in SAP (MEINS)
EINHEITEN = {"H87": "ST", "C62": "ST", "XPP": "ST", "PR": "PAA", "HUR": "STD", "DAY": "TAG",
             "KGM": "KG", "MTR": "M", "LTR": "L", "MTK": "M2", "SET": "SET", "XPK": "PAK"}


# ═════════════════════════════════════════════════════════════════════════════
# Fehlerklassen
# ═════════════════════════════════════════════════════════════════════════════

class EInvoiceFehler(ValueError):
    """Datei ist keine verarbeitbare E-Rechnung."""


class XMLSicherheitsFehler(EInvoiceFehler):
    """Datei enthält potenziell gefährliche XML-Konstrukte (XXE, Entity-Bombe …)."""


class UnbekanntesFormat(EInvoiceFehler):
    """Wohlgeformtes XML, aber weder UBL-Invoice noch CII."""


# ═════════════════════════════════════════════════════════════════════════════
# Datenmodell (semantisch nach EN 16931)
# ═════════════════════════════════════════════════════════════════════════════

class Adresse(BaseModel):
    strasse: Optional[str] = None      # BT-35 / BT-50
    plz: Optional[str] = None          # BT-38 / BT-53
    ort: Optional[str] = None          # BT-37 / BT-52
    land: Optional[str] = None         # BT-40 / BT-55


class Partei(BaseModel):
    name: Optional[str] = None         # BT-27 / BT-44
    ust_id: Optional[str] = None       # BT-31 / BT-48
    steuernummer: Optional[str] = None # BT-32
    email: Optional[str] = None        # BT-34 / BT-49
    adresse: Adresse = Field(default_factory=Adresse)


class Position(BaseModel):
    id: str                                  # BT-126
    bezeichnung: Optional[str] = None        # BT-153
    artikelnummer: Optional[str] = None      # BT-155
    menge: Optional[Decimal] = None          # BT-129
    einheit_code: Optional[str] = None       # BT-130
    nettobetrag: Optional[Decimal] = None    # BT-131
    preis: Optional[Decimal] = None          # BT-146
    basismenge: Decimal = Decimal("1")       # BT-149
    steuerkategorie: Optional[str] = None    # BT-151
    steuersatz: Optional[Decimal] = None     # BT-152
    bestellposition: Optional[str] = None    # BT-132

    @property
    def einheit(self) -> str:
        return EINHEITEN.get(self.einheit_code or "", self.einheit_code or "ST")


class Steuerposten(BaseModel):
    kategorie: Optional[str] = None          # BT-118
    satz: Optional[Decimal] = None           # BT-119
    basis: Optional[Decimal] = None          # BT-116
    betrag: Optional[Decimal] = None         # BT-117


class Skonto(BaseModel):
    prozent: Decimal
    tage: int
    frist: Optional[date] = None
    basisbetrag: Optional[Decimal] = None


class Summen(BaseModel):
    positionen: Optional[Decimal] = None     # BT-106
    netto: Optional[Decimal] = None          # BT-109
    steuer: Optional[Decimal] = None         # BT-110
    brutto: Optional[Decimal] = None         # BT-112
    vorauszahlung: Decimal = Decimal("0")    # BT-113
    zahlbetrag: Optional[Decimal] = None     # BT-115


class GeparsteRechnung(BaseModel):
    syntax: Literal["UBL", "CII"]
    format: Literal["XRechnung-UBL", "XRechnung-CII", "ZUGFeRD"]
    spezifikation: Optional[str] = None      # BT-24
    rechnungsnummer: Optional[str] = None    # BT-1
    rechnungsdatum: Optional[date] = None    # BT-2
    rechnungsart: Optional[str] = None       # BT-3
    waehrung: Optional[str] = None           # BT-5
    faelligkeit: Optional[date] = None       # BT-9
    kaeuferreferenz: Optional[str] = None    # BT-10
    bestellnummer: Optional[str] = None      # BT-13
    leistungsdatum: Optional[date] = None    # BT-72
    leistungszeitraum: Optional[tuple[Optional[date], Optional[date]]] = None  # BG-14
    bemerkungen: list[str] = Field(default_factory=list)   # BT-22
    zahlungsbedingungen: Optional[str] = None               # BT-20
    skonto: Optional[Skonto] = None
    iban: Optional[str] = None               # BT-84
    verkaeufer: Partei = Field(default_factory=Partei)
    kaeufer: Partei = Field(default_factory=Partei)
    positionen: list[Position] = Field(default_factory=list)
    steuern: list[Steuerposten] = Field(default_factory=list)
    summen: Summen = Field(default_factory=Summen)
    quelle: Optional[str] = None
    container: Optional[str] = None          # z. B. "PDF/A-3 · factur-x.xml · Profil EN 16931"
    sha256: str

    @property
    def ist_xrechnung(self) -> bool:
        return self.format.startswith("XRechnung")


# ═════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═════════════════════════════════════════════════════════════════════════════

def _dez(text: Optional[str]) -> Optional[Decimal]:
    if text is None or not text.strip():
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        return None


def _datum_iso(text: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(text.strip()) if text and text.strip() else None
    except ValueError:
        return None


def _datum_102(text: Optional[str]) -> Optional[date]:
    """CII-Datumsformat 102 = JJJJMMTT."""
    if not text or not re.fullmatch(r"\s*\d{8}\s*", text):
        return None
    t = text.strip()
    try:
        return date(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None


def _t(el: Optional[Element], pfad: str, ns: dict[str, str]) -> Optional[str]:
    if el is None:
        return None
    knoten = el.find(pfad, ns)
    if knoten is None or knoten.text is None:
        return None
    text = knoten.text.strip()
    return text or None


def _rund2(wert: Decimal) -> Decimal:
    return wert.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ═════════════════════════════════════════════════════════════════════════════
# 1 · Sicheres Einlesen
# ═════════════════════════════════════════════════════════════════════════════

_GEFAEHRLICH = re.compile(rb"<!\s*(DOCTYPE|ENTITY|ELEMENT|ATTLIST)", re.IGNORECASE)


def _sicher_parsen(daten: bytes) -> Element:
    if len(daten) > MAX_DATEIGROESSE:
        raise XMLSicherheitsFehler(
            f"Datei größer als {MAX_DATEIGROESSE // (1024 * 1024)} MB – Verarbeitung abgelehnt."
        )
    # E-Rechnungen nach EN 16931 benötigen niemals eine DTD. Wir lehnen sie ab,
    # bevor überhaupt ein Parser läuft (auch UTF-16-kodiert).
    probe = daten
    if daten[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            probe = daten.decode("utf-16").encode("utf-8")
        except UnicodeDecodeError as exc:
            raise EInvoiceFehler("Ungültige UTF-16-Kodierung.") from exc
    if _GEFAEHRLICH.search(probe):
        raise XMLSicherheitsFehler(
            "DOCTYPE/ENTITY-Deklarationen sind nicht erlaubt (Schutz vor XXE- und Entity-Expansion-Angriffen)."
        )
    try:
        return SafeET.fromstring(daten, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise XMLSicherheitsFehler(f"Unsicheres XML abgelehnt: {type(exc).__name__}.") from exc
    except ParseError as exc:
        raise EInvoiceFehler(f"Kein wohlgeformtes XML: {exc}") from exc


def lese_rechnung(quelle: Path | str | bytes) -> GeparsteRechnung:
    """Liest eine XRechnung (UBL/CII), eine ZUGFeRD-XML oder ein ZUGFeRD-/Factur-X-PDF sicher ein."""
    if isinstance(quelle, bytes):
        daten, name = quelle, None
    else:
        pfad = Path(quelle)
        if pfad.suffix.lower() != ".pdf" and pfad.stat().st_size > MAX_DATEIGROESSE:
            raise XMLSicherheitsFehler("Datei überschreitet das Größenlimit.")
        daten, name = pfad.read_bytes(), pfad.name
    sha = hashlib.sha256(daten).hexdigest()
    container = None
    if b"%PDF-" in daten[:1024]:
        from .zugferd_pdf import extrahiere_xml  # hybride E-Rechnung (ZUGFeRD/Factur-X)

        anhang = extrahiere_xml(daten)
        daten, container = anhang.xml, anhang.beschreibung
    wurzel = _sicher_parsen(daten)

    if wurzel.tag == f"{{{NS_UBL['inv']}}}Invoice":
        r = _parse_ubl(wurzel, sha)
    elif wurzel.tag == f"{{{NS_CII['rsm']}}}CrossIndustryInvoice":
        r = _parse_cii(wurzel, sha)
    else:
        raise UnbekanntesFormat(f"Unbekanntes Wurzelelement {wurzel.tag!r} – erwartet UBL-Invoice oder CII.")
    r.quelle = name
    r.container = container
    _ergaenze_zahlungsbedingungen(r)
    return r


def _format_aus_spezifikation(spez: Optional[str], syntax: str) -> str:
    s = (spez or "").lower()
    if "xrechnung" in s:
        return f"XRechnung-{syntax}"
    if syntax == "CII":
        return "ZUGFeRD"
    return "XRechnung-UBL"  # UBL ohne XRechnung-Kennung: wie XRechnung behandeln (strengere Regeln)


# ─── UBL 2.1 ────────────────────────────────────────────────────────────────

def _ubl_partei(el: Optional[Element]) -> Partei:
    ns = NS_UBL
    if el is None:
        return Partei()
    ust, stnr = None, None
    for ts in el.findall("cac:PartyTaxScheme", ns):
        schema = _t(ts, "cac:TaxScheme/cbc:ID", ns)
        if schema == "VAT":
            ust = _t(ts, "cbc:CompanyID", ns)
        elif schema == "FC":
            stnr = _t(ts, "cbc:CompanyID", ns)
    return Partei(
        name=_t(el, "cac:PartyName/cbc:Name", ns) or _t(el, "cac:PartyLegalEntity/cbc:RegistrationName", ns),
        ust_id=ust,
        steuernummer=stnr,
        email=_t(el, "cac:Contact/cbc:ElectronicMail", ns) or _t(el, "cbc:EndpointID", ns),
        adresse=Adresse(
            strasse=_t(el, "cac:PostalAddress/cbc:StreetName", ns),
            plz=_t(el, "cac:PostalAddress/cbc:PostalZone", ns),
            ort=_t(el, "cac:PostalAddress/cbc:CityName", ns),
            land=_t(el, "cac:PostalAddress/cac:Country/cbc:IdentificationCode", ns),
        ),
    )


def _parse_ubl(w: Element, sha: str) -> GeparsteRechnung:
    ns = NS_UBL
    spez = _t(w, "cbc:CustomizationID", ns)
    zeitraum = w.find("cac:InvoicePeriod", ns)
    steuern: list[Steuerposten] = []
    steuer_gesamt: Optional[Decimal] = None
    for tt in w.findall("cac:TaxTotal", ns):
        subs = tt.findall("cac:TaxSubtotal", ns)
        if subs or steuer_gesamt is None:
            steuer_gesamt = _dez(_t(tt, "cbc:TaxAmount", ns))
        for st in subs:
            steuern.append(Steuerposten(
                kategorie=_t(st, "cac:TaxCategory/cbc:ID", ns),
                satz=_dez(_t(st, "cac:TaxCategory/cbc:Percent", ns)),
                basis=_dez(_t(st, "cbc:TaxableAmount", ns)),
                betrag=_dez(_t(st, "cbc:TaxAmount", ns)),
            ))
    positionen = []
    for z in w.findall("cac:InvoiceLine", ns):
        menge_el = z.find("cbc:InvoicedQuantity", ns)
        positionen.append(Position(
            id=_t(z, "cbc:ID", ns) or "?",
            bezeichnung=_t(z, "cac:Item/cbc:Name", ns),
            artikelnummer=_t(z, "cac:Item/cac:SellersItemIdentification/cbc:ID", ns),
            menge=_dez(menge_el.text if menge_el is not None else None),
            einheit_code=menge_el.get("unitCode") if menge_el is not None else None,
            nettobetrag=_dez(_t(z, "cbc:LineExtensionAmount", ns)),
            preis=_dez(_t(z, "cac:Price/cbc:PriceAmount", ns)),
            basismenge=_dez(_t(z, "cac:Price/cbc:BaseQuantity", ns)) or Decimal("1"),
            steuerkategorie=_t(z, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID", ns),
            steuersatz=_dez(_t(z, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", ns)),
            bestellposition=_t(z, "cac:OrderLineReference/cbc:LineID", ns),
        ))
    lmt = w.find("cac:LegalMonetaryTotal", ns)
    return GeparsteRechnung(
        syntax="UBL",
        format=_format_aus_spezifikation(spez, "UBL"),
        spezifikation=spez,
        rechnungsnummer=_t(w, "cbc:ID", ns),
        rechnungsdatum=_datum_iso(_t(w, "cbc:IssueDate", ns)),
        rechnungsart=_t(w, "cbc:InvoiceTypeCode", ns),
        waehrung=_t(w, "cbc:DocumentCurrencyCode", ns),
        faelligkeit=_datum_iso(_t(w, "cbc:DueDate", ns)),
        kaeuferreferenz=_t(w, "cbc:BuyerReference", ns),
        bestellnummer=_t(w, "cac:OrderReference/cbc:ID", ns),
        leistungsdatum=_datum_iso(_t(w, "cac:Delivery/cbc:ActualDeliveryDate", ns)),
        leistungszeitraum=(
            (_datum_iso(_t(zeitraum, "cbc:StartDate", ns)), _datum_iso(_t(zeitraum, "cbc:EndDate", ns)))
            if zeitraum is not None else None
        ),
        bemerkungen=[n.text.strip() for n in w.findall("cbc:Note", ns) if n.text and n.text.strip()],
        zahlungsbedingungen=_t(w, "cac:PaymentTerms/cbc:Note", ns),
        iban=_t(w, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID", ns),
        verkaeufer=_ubl_partei(w.find("cac:AccountingSupplierParty/cac:Party", ns)),
        kaeufer=_ubl_partei(w.find("cac:AccountingCustomerParty/cac:Party", ns)),
        positionen=positionen,
        steuern=steuern,
        summen=Summen(
            positionen=_dez(_t(lmt, "cbc:LineExtensionAmount", ns)),
            netto=_dez(_t(lmt, "cbc:TaxExclusiveAmount", ns)),
            steuer=steuer_gesamt,
            brutto=_dez(_t(lmt, "cbc:TaxInclusiveAmount", ns)),
            vorauszahlung=_dez(_t(lmt, "cbc:PrepaidAmount", ns)) or Decimal("0"),
            zahlbetrag=_dez(_t(lmt, "cbc:PayableAmount", ns)),
        ),
        sha256=sha,
    )


# ─── UN/CEFACT CII D16B (ZUGFeRD / Factur-X / XRechnung-CII) ─────────────────

def _cii_partei(el: Optional[Element]) -> Partei:
    ns = NS_CII
    if el is None:
        return Partei()
    ust, stnr = None, None
    for reg in el.findall("ram:SpecifiedTaxRegistration/ram:ID", ns):
        if reg.get("schemeID") == "VA":
            ust = (reg.text or "").strip() or None
        elif reg.get("schemeID") == "FC":
            stnr = (reg.text or "").strip() or None
    return Partei(
        name=_t(el, "ram:Name", ns),
        ust_id=ust,
        steuernummer=stnr,
        email=_t(el, "ram:URIUniversalCommunication/ram:URIID", ns)
        or _t(el, "ram:DefinedTradeContact/ram:EmailURIUniversalCommunication/ram:URIID", ns),
        adresse=Adresse(
            strasse=_t(el, "ram:PostalTradeAddress/ram:LineOne", ns),
            plz=_t(el, "ram:PostalTradeAddress/ram:PostcodeCode", ns),
            ort=_t(el, "ram:PostalTradeAddress/ram:CityName", ns),
            land=_t(el, "ram:PostalTradeAddress/ram:CountryID", ns),
        ),
    )


def _parse_cii(w: Element, sha: str) -> GeparsteRechnung:
    ns = NS_CII
    spez = _t(w, "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", ns)
    doc = w.find("rsm:ExchangedDocument", ns)
    tx = w.find("rsm:SupplyChainTradeTransaction", ns)
    if tx is None:
        raise EInvoiceFehler("CII-Rechnung ohne SupplyChainTradeTransaction.")
    vereinbarung = tx.find("ram:ApplicableHeaderTradeAgreement", ns)
    abrechnung = tx.find("ram:ApplicableHeaderTradeSettlement", ns)
    summe = abrechnung.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", ns) if abrechnung is not None else None
    bedingungen = abrechnung.find("ram:SpecifiedTradePaymentTerms", ns) if abrechnung is not None else None
    periode = abrechnung.find("ram:BillingSpecifiedPeriod", ns) if abrechnung is not None else None

    steuern = [
        Steuerposten(
            kategorie=_t(st, "ram:CategoryCode", ns),
            satz=_dez(_t(st, "ram:RateApplicablePercent", ns)),
            basis=_dez(_t(st, "ram:BasisAmount", ns)),
            betrag=_dez(_t(st, "ram:CalculatedAmount", ns)),
        )
        for st in (abrechnung.findall("ram:ApplicableTradeTax", ns) if abrechnung is not None else [])
    ]
    positionen = []
    for z in tx.findall("ram:IncludedSupplyChainTradeLineItem", ns):
        menge_el = z.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", ns)
        positionen.append(Position(
            id=_t(z, "ram:AssociatedDocumentLineDocument/ram:LineID", ns) or "?",
            bezeichnung=_t(z, "ram:SpecifiedTradeProduct/ram:Name", ns),
            artikelnummer=_t(z, "ram:SpecifiedTradeProduct/ram:SellerAssignedID", ns),
            menge=_dez(menge_el.text if menge_el is not None else None),
            einheit_code=menge_el.get("unitCode") if menge_el is not None else None,
            nettobetrag=_dez(_t(z, "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount", ns)),
            preis=_dez(_t(z, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount", ns)),
            basismenge=_dez(_t(z, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:BasisQuantity", ns)) or Decimal("1"),
            steuerkategorie=_t(z, "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode", ns),
            steuersatz=_dez(_t(z, "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent", ns)),
            bestellposition=_t(z, "ram:SpecifiedLineTradeAgreement/ram:BuyerOrderReferencedDocument/ram:LineID", ns),
        ))

    skonto = None
    if bedingungen is not None:
        rabatt = bedingungen.find("ram:ApplicableTradePaymentDiscountTerms", ns)
        if rabatt is not None:
            tage, proz = _t(rabatt, "ram:BasisPeriodMeasure", ns), _dez(_t(rabatt, "ram:CalculationPercent", ns))
            if tage and tage.isdigit() and proz is not None:
                skonto = Skonto(prozent=proz, tage=int(tage),
                                basisbetrag=_dez(_t(rabatt, "ram:BasisAmount", ns)))

    return GeparsteRechnung(
        syntax="CII",
        format=_format_aus_spezifikation(spez, "CII"),
        spezifikation=spez,
        rechnungsnummer=_t(doc, "ram:ID", ns),
        rechnungsdatum=_datum_102(_t(doc, "ram:IssueDateTime/udt:DateTimeString", ns)),
        rechnungsart=_t(doc, "ram:TypeCode", ns),
        waehrung=_t(abrechnung, "ram:InvoiceCurrencyCode", ns),
        faelligkeit=_datum_102(_t(bedingungen, "ram:DueDateDateTime/udt:DateTimeString", ns)),
        kaeuferreferenz=_t(vereinbarung, "ram:BuyerReference", ns),
        bestellnummer=_t(vereinbarung, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID", ns),
        leistungsdatum=_datum_102(_t(tx, "ram:ApplicableHeaderTradeDelivery/ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString", ns)),
        leistungszeitraum=(
            (_datum_102(_t(periode, "ram:StartDateTime/udt:DateTimeString", ns)),
             _datum_102(_t(periode, "ram:EndDateTime/udt:DateTimeString", ns)))
            if periode is not None else None
        ),
        bemerkungen=[c.text.strip() for c in (doc.findall("ram:IncludedNote/ram:Content", ns) if doc is not None else [])
                     if c.text and c.text.strip()],
        zahlungsbedingungen=_t(bedingungen, "ram:Description", ns),
        skonto=skonto,
        iban=_t(abrechnung, "ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID", ns),
        verkaeufer=_cii_partei(vereinbarung.find("ram:SellerTradeParty", ns) if vereinbarung is not None else None),
        kaeufer=_cii_partei(vereinbarung.find("ram:BuyerTradeParty", ns) if vereinbarung is not None else None),
        positionen=positionen,
        steuern=steuern,
        summen=Summen(
            positionen=_dez(_t(summe, "ram:LineTotalAmount", ns)),
            netto=_dez(_t(summe, "ram:TaxBasisTotalAmount", ns)),
            steuer=_dez(_t(summe, "ram:TaxTotalAmount", ns)),
            brutto=_dez(_t(summe, "ram:GrandTotalAmount", ns)),
            vorauszahlung=_dez(_t(summe, "ram:TotalPrepaidAmount", ns)) or Decimal("0"),
            zahlbetrag=_dez(_t(summe, "ram:DuePayableAmount", ns)),
        ),
        sha256=sha,
    )


# ─── Zahlungsziel & Skonto ───────────────────────────────────────────────────

_SKONTO_KOSIT = re.compile(r"#SKONTO#TAGE=(\d+)#PROZENT=(\d+(?:\.\d+)?)#(?:BASISBETRAG=(-?\d+(?:\.\d+)?)#)?")
_SKONTO_FREI_1 = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*Skonto\D{0,40}?(\d+)\s*Tag", re.IGNORECASE)
_SKONTO_FREI_2 = re.compile(r"(\d+)\s*Tag\w*\D{0,40}?(\d+(?:[.,]\d+)?)\s*%\s*Skonto", re.IGNORECASE)
_NETTO_ZIEL = re.compile(r"(\d+)\s*Tage?n?\s*(?:rein\s*)?netto", re.IGNORECASE)


def _ergaenze_zahlungsbedingungen(r: GeparsteRechnung) -> None:
    text = r.zahlungsbedingungen or ""
    if r.skonto is None and text:
        if m := _SKONTO_KOSIT.search(text):
            r.skonto = Skonto(tage=int(m[1]), prozent=Decimal(m[2]), basisbetrag=_dez(m[3]))
        elif m := _SKONTO_FREI_1.search(text):
            r.skonto = Skonto(prozent=Decimal(m[1].replace(",", ".")), tage=int(m[2]))
        elif m := _SKONTO_FREI_2.search(text):
            r.skonto = Skonto(tage=int(m[1]), prozent=Decimal(m[2].replace(",", ".")))
    if r.skonto and r.rechnungsdatum:
        r.skonto.frist = r.rechnungsdatum + timedelta(days=r.skonto.tage)
    if r.faelligkeit is None and r.rechnungsdatum and (m := _NETTO_ZIEL.search(text)):
        r.faelligkeit = r.rechnungsdatum + timedelta(days=int(m[1]))


# ═════════════════════════════════════════════════════════════════════════════
# 2 · Prompt-Injection-Erkennung
# ═════════════════════════════════════════════════════════════════════════════

_INJECTION_MUSTER = [
    r"ignorier\w*\s+(?:alle\s+|sämtliche\s+)?(?:vorherigen|bisherigen|obigen|vorigen)\s+(?:anweisungen|instruktionen|regeln)",
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules)",
    r"\b(?:system|assistant|developer)\s*(?:prompt)?\s*:",
    r"\bdu\s+bist\s+(?:jetzt|nun|ab\s+sofort)\b",
    r"\byou\s+are\s+now\b",
    r"\b(?:workflow_schritt|gesamturteil|bpmn_pfad|audit_vermerk)\b",
    r"\bTOUCHLESS_FREIGABE\b",
    r"ohne\s+(?:weitere\s+)?prüfung\s+(?:\w+\s+){0,3}(?:freigeben|freizugeben|zahlen|überweisen)",
    r"<\s*/?\s*(?:system|instructions?|prompt)\s*>",
    r"\{\{.*?\}\}",
]
_INJECTION_RE = re.compile("|".join(f"(?:{m})" for m in _INJECTION_MUSTER), re.IGNORECASE | re.DOTALL)


def erkenne_prompt_injection(text: Optional[str]) -> bool:
    """True, wenn ein Freitext anweisungsähnliche Muster an ein KI-System enthält."""
    return bool(text and _INJECTION_RE.search(text))


# ═════════════════════════════════════════════════════════════════════════════
# 3 · Validierung (§ 14 UStG, EN 16931, Rechenprüfung)
# ═════════════════════════════════════════════════════════════════════════════

_UST_ID_DE = re.compile(r"DE\d{9}")
_UST_ID_EU = re.compile(r"(?:AT|BE|BG|CY|CZ|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI)[0-9A-Z+*]{2,12}")


def _pr(code: PruefCode, status: Pruefstatus, meldung: str, **kw) -> Pruefergebnis:
    return Pruefergebnis(code=code, status=status, meldung=meldung, **kw)


def _pruefe_pflichtfelder(r: GeparsteRechnung) -> list[Pruefergebnis]:
    fehlend: list[tuple[str, str, str]] = []  # (Bezeichnung, BT, § 14-Nummer)

    def pflicht(wert: object, bez: str, bt: str, nr: str) -> None:
        if wert is None or (isinstance(wert, str) and not wert.strip()):
            fehlend.append((bez, bt, nr))

    v, k = r.verkaeufer, r.kaeufer
    pflicht(v.name, "Name des Verkäufers", "BT-27", "Nr. 1")
    pflicht(v.adresse.strasse, "Straße des Verkäufers", "BT-35", "Nr. 1")
    pflicht(v.adresse.plz, "PLZ des Verkäufers", "BT-38", "Nr. 1")
    pflicht(v.adresse.ort, "Ort des Verkäufers", "BT-37", "Nr. 1")
    pflicht(v.adresse.land, "Land des Verkäufers", "BT-40", "Nr. 1")
    pflicht(k.name, "Name des Käufers", "BT-44", "Nr. 1")
    pflicht(k.adresse.strasse, "Straße des Käufers", "BT-50", "Nr. 1")
    pflicht(k.adresse.plz, "PLZ des Käufers", "BT-53", "Nr. 1")
    pflicht(k.adresse.ort, "Ort des Käufers", "BT-52", "Nr. 1")
    if not v.ust_id and not v.steuernummer:
        fehlend.append(("USt-IdNr. oder Steuernummer des Verkäufers", "BT-31/BT-32", "Nr. 2"))
    pflicht(r.rechnungsdatum, "Ausstellungsdatum", "BT-2", "Nr. 3")
    pflicht(r.rechnungsnummer, "Rechnungsnummer", "BT-1", "Nr. 4")
    if not r.positionen:
        fehlend.append(("Rechnungspositionen (Menge und Art der Leistung)", "BG-25", "Nr. 5"))
    for pos in r.positionen:
        pflicht(pos.bezeichnung, f"Bezeichnung Position {pos.id}", "BT-153", "Nr. 5")
        pflicht(pos.menge, f"Menge Position {pos.id}", "BT-129", "Nr. 5")
    zeitraum_ok = r.leistungszeitraum is not None and any(r.leistungszeitraum)
    if r.leistungsdatum is None and not zeitraum_ok:
        fehlend.append(("Leistungsdatum bzw. Leistungszeitraum", "BT-72/BG-14", "Nr. 6"))
    if not r.steuern:
        fehlend.append(("Aufschlüsselung nach Steuersätzen", "BG-23", "Nr. 7/8"))
    pflicht(r.waehrung, "Rechnungswährung", "BT-5", "EN 16931")
    pflicht(r.summen.brutto, "Rechnungsbetrag brutto", "BT-112", "EN 16931")

    if not fehlend:
        return [_pr(PruefCode.PFLICHTFELD_FEHLT, Pruefstatus.OK,
                    "Pflichtangaben nach § 14 Abs. 4 UStG vollständig.")]
    return [
        _pr(PruefCode.PFLICHTFELD_FEHLT, Pruefstatus.FEHLER,
            f"Pflichtangabe fehlt: {bez} (§ 14 Abs. 4 {nr} UStG).", referenz=bt)
        for bez, bt, nr in fehlend
    ]


def _pruefe_ust_id(r: GeparsteRechnung) -> list[Pruefergebnis]:
    ergebnisse = []
    for rolle, partei, bt in (("Verkäufer", r.verkaeufer, "BT-31"), ("Käufer", r.kaeufer, "BT-48")):
        if not partei.ust_id:
            continue
        uid = partei.ust_id.replace(" ", "").upper()
        if not (_UST_ID_DE.fullmatch(uid) or _UST_ID_EU.fullmatch(uid)):
            ergebnisse.append(_pr(PruefCode.USTID_UNGUELTIG, Pruefstatus.FEHLER,
                                  f"USt-IdNr. des {rolle}s hat kein gültiges Format.", referenz=bt))
    if ergebnisse:
        return ergebnisse
    if r.verkaeufer.ust_id:
        meldung = "USt-IdNr. formal gültig."
    elif r.verkaeufer.steuernummer:
        meldung = "Steuernummer statt USt-IdNr. angegeben (§ 14 Abs. 4 Nr. 2 UStG zulässig)."
    else:
        meldung = "Keine USt-IdNr. vorhanden – siehe Pflichtfeldprüfung."
    return [_pr(PruefCode.USTID_UNGUELTIG, Pruefstatus.OK, meldung)]


def _pruefe_leitweg(r: GeparsteRechnung) -> list[Pruefergebnis]:
    if r.kaeuferreferenz:
        return [_pr(PruefCode.LEITWEG_ID_FEHLT, Pruefstatus.OK, "Käuferreferenz (BT-10) vorhanden.")]
    if r.ist_xrechnung:
        return [_pr(PruefCode.LEITWEG_ID_FEHLT, Pruefstatus.FEHLER,
                    "Käuferreferenz/Leitweg-ID (BT-10) fehlt – für XRechnung verpflichtend (BR-DE-15).",
                    referenz="BT-10")]
    return [_pr(PruefCode.LEITWEG_ID_FEHLT, Pruefstatus.OK,
                "Käuferreferenz (BT-10) im ZUGFeRD-B2B-Verkehr nicht verpflichtend.")]


def _satz_zulaessig(kategorie: Optional[str], satz: Optional[Decimal]) -> bool:
    if satz is None:
        return False
    if kategorie in NULLSATZ_KATEGORIEN:
        return satz == 0
    if kategorie == "S":
        return satz in ZULAESSIGE_SAETZE_S
    return satz in ZULAESSIGE_SAETZE_S | {Decimal("0")}


def _pruefe_steuersaetze(r: GeparsteRechnung) -> list[Pruefergebnis]:
    ergebnisse = []
    for st in r.steuern:
        if not _satz_zulaessig(st.kategorie, st.satz):
            ergebnisse.append(_pr(PruefCode.STEUERSATZ_UNZULAESSIG, Pruefstatus.FEHLER,
                                  f"Steuersatz unzulässig für Kategorie {st.kategorie}.",
                                  ist=st.satz, referenz="BT-119"))
    for pos in r.positionen:
        if not _satz_zulaessig(pos.steuerkategorie, pos.steuersatz):
            ergebnisse.append(_pr(PruefCode.STEUERSATZ_UNZULAESSIG, Pruefstatus.FEHLER,
                                  f"Steuersatz in Position {pos.id} unzulässig.",
                                  ist=pos.steuersatz, referenz=f"BT-152 Pos. {pos.id}"))
    return ergebnisse or [_pr(PruefCode.STEUERSATZ_UNZULAESSIG, Pruefstatus.OK,
                              "Steuersätze zulässig (19 %, 7 % oder 0 %).")]


def _abweichung(soll: Decimal, ist: Decimal, bezeichnung: str, referenz: str) -> Optional[Pruefergebnis]:
    if abs(ist - soll) <= RUNDUNGSTOLERANZ:
        return None
    return _pr(PruefCode.SUMMENFEHLER, Pruefstatus.FEHLER,
               f"Rechenfehler: {bezeichnung}.", soll=soll, ist=ist,
               abweichung=ist - soll, referenz=referenz)


def _pruefe_rechnerisch(r: GeparsteRechnung) -> list[Pruefergebnis]:
    befunde: list[Optional[Pruefergebnis]] = []
    s = r.summen

    # (a) Positionsebene: Menge × Preis / Basismenge = Positionsnetto
    for pos in r.positionen:
        if pos.menge is not None and pos.preis is not None and pos.nettobetrag is not None and pos.basismenge:
            soll = _rund2(pos.menge * pos.preis / pos.basismenge)
            befunde.append(_abweichung(soll, pos.nettobetrag,
                                       f"Menge × Preis ≠ Positionsnetto (Position {pos.id})",
                                       f"BT-131 Pos. {pos.id}"))

    # (b) Σ Positionsnetto = Summe Positionen (BT-106)
    if s.positionen is not None and all(p.nettobetrag is not None for p in r.positionen):
        summe = sum((p.nettobetrag for p in r.positionen), Decimal("0"))
        befunde.append(_abweichung(summe, s.positionen, "Summe der Positionen ≠ BT-106", "BT-106"))

    # (c) Gesamtnetto (BT-109) = BT-106 (Nachlässe/Zuschläge auf Belegebene nicht unterstützt)
    if s.positionen is not None and s.netto is not None:
        befunde.append(_abweichung(s.positionen, s.netto, "Gesamtnetto ≠ Summe der Positionen", "BT-109"))

    # (d) Je Steuerkategorie: Basis = Σ Positionen, Steuer = Basis × Satz
    for st in r.steuern:
        passende = [p for p in r.positionen if p.steuerkategorie == st.kategorie and p.steuersatz == st.satz]
        if st.basis is not None and passende and all(p.nettobetrag is not None for p in passende):
            basis = sum((p.nettobetrag for p in passende), Decimal("0"))
            befunde.append(_abweichung(basis, st.basis,
                                       f"Steuerbasis {st.kategorie} {st.satz} % ≠ Summe der Positionen", "BT-116"))
        if st.basis is not None and st.satz is not None and st.betrag is not None:
            befunde.append(_abweichung(_rund2(st.basis * st.satz / 100), st.betrag,
                                       f"Netto × Steuersatz ≠ Steuerbetrag ({st.kategorie} {st.satz} %)", "BT-117"))

    # (e) Σ Steuerbeträge = Steuer gesamt (BT-110)
    if s.steuer is not None and r.steuern and all(st.betrag is not None for st in r.steuern):
        befunde.append(_abweichung(sum((st.betrag for st in r.steuern), Decimal("0")), s.steuer,
                                   "Summe der Steuerbeträge ≠ Steuer gesamt", "BT-110"))

    # (f) Netto + Steuer = Brutto (BT-112)
    if s.netto is not None and s.steuer is not None and s.brutto is not None:
        befunde.append(_abweichung(s.netto + s.steuer, s.brutto, "Netto + Steuer ≠ Brutto", "BT-112"))

    # (g) Brutto − Vorauszahlung = Zahlbetrag (BT-115)
    if s.brutto is not None and s.zahlbetrag is not None:
        befunde.append(_abweichung(s.brutto - s.vorauszahlung, s.zahlbetrag,
                                   "Brutto − Vorauszahlung ≠ Zahlbetrag", "BT-115"))

    ergebnisse = [b for b in befunde if b is not None]
    return ergebnisse or [_pr(PruefCode.SUMMENFEHLER, Pruefstatus.OK,
                              "Rechnerische Prüfung auf Positions- und Kopfebene ohne Befund.")]


def _pruefe_freitexte(r: GeparsteRechnung) -> list[Pruefergebnis]:
    fundstellen = []
    if any(erkenne_prompt_injection(b) for b in r.bemerkungen):
        fundstellen.append("BT-22")
    if erkenne_prompt_injection(r.zahlungsbedingungen):
        fundstellen.append("BT-20")
    fundstellen += [f"BT-153 Pos. {p.id}" for p in r.positionen if erkenne_prompt_injection(p.bezeichnung)]
    if not fundstellen:
        return [_pr(PruefCode.VERDAECHTIGER_FREITEXT, Pruefstatus.OK, "Keine auffälligen Freitexte.")]
    return [_pr(PruefCode.VERDAECHTIGER_FREITEXT, Pruefstatus.WARNUNG,
                "Anweisungsähnlicher Freitext (möglicher Prompt-Injection-Versuch) erkannt. Der Inhalt wird "
                "nicht an das Sprachmodell übergeben und beeinflusst Prüfung und Routing nicht.",
                referenz=", ".join(fundstellen))]


def validiere_rechnung(r: GeparsteRechnung) -> list[Pruefergebnis]:
    """Formale und rechnerische Prüfung – liefert Prüfergebnisse für ``AuditFakten``."""
    ergebnisse: list[Pruefergebnis] = []
    for pruefung in (_pruefe_pflichtfelder, _pruefe_ust_id, _pruefe_leitweg,
                     _pruefe_steuersaetze, _pruefe_rechnerisch, _pruefe_freitexte):
        ergebnisse.extend(pruefung(r))
    return ergebnisse


def fehlerhafte(ergebnisse: Iterable[Pruefergebnis]) -> list[Pruefergebnis]:
    return [e for e in ergebnisse if e.status is Pruefstatus.FEHLER]
