"""Prüfbericht als PDF und PowerPoint – **nur mit freigegebenen Klärungen** (wie VarianceAI: nur Freigegebenes).

Klärfälle im Status „offen“ oder „in Klärung“ (auch mit erster von zwei Freigaben) erscheinen nie im Bericht;
der Bericht nennt lediglich ihre Anzahl. Alle Zahlen stammen aus dem Prüfstand (deterministisch).
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .agent import fmt_datum, fmt_de
from .klaerfall import Klaerfall
from .pruefstand import Pruefstand

FIRMA = "RheinMain Industrietechnik GmbH (fiktiv) · Buchungskreis 1000"
_ERSETZE = {"−": "-", "→": "->", "≙": "=", "…": "...", " ": " ", "Δ": "Delta "}


def _text(s: object) -> str:
    """Standardschriften (Helvetica/WinAnsi) kennen einige Zeichen nicht – ersetzen statt Kästchen drucken."""
    t = str(s)
    for alt, neu in _ERSETZE.items():
        t = t.replace(alt, neu)
    return t


def _vz(x: Decimal | None, stellen: int = 1, einheit: str = "") -> str:
    if x is None:
        return "–"
    return f"{'+' if x > 0 else ('−' if x < 0 else '±')}{fmt_de(abs(x), stellen)}{einheit}"


@dataclass(frozen=True)
class Berichtsinhalt:
    titel: str
    untertitel: str
    kennzahlen: list[list[str]]          # Kennzahl, Berichtsperiode, Vorperiode, Veränderung
    kernaussagen: list[str]
    klaerungen: list[list[str]]          # nur gelöste Klärfälle
    nicht_enthalten: int
    treiber: list[tuple[str, Decimal, int]]
    zahlungen: list[list[str]]
    zahlung_summen: tuple[Decimal, Decimal, Decimal]   # Zahlbetrag, Skonto gesichert, Skonto gefährdet
    praevention: list[list[str]]


def berichtsinhalt(p: Pruefstand, klaerfaelle: Mapping[str, Klaerfall]) -> Berichtsinhalt:
    a, vp = p.periode.aktuell, p.periode.vorperiode
    kennzahlen = [
        ["Geprüfte Rechnungen", str(a.eingegangen), str(vp.eingegangen), _vz(p.periode.delta("eingegangen"), 0)],
        ["Touchless-Quote", f"{fmt_de(a.touchless_quote_prozent or 0, 1)} %",
         f"{fmt_de(vp.touchless_quote_prozent or 0, 1)} %", _vz(p.periode.delta("touchless_quote_prozent"), 1, " Pp")],
        ["Volumen in Klärung", f"{fmt_de(a.volumen_offen_eur)} €", f"{fmt_de(vp.volumen_offen_eur)} €",
         _vz(p.periode.delta("volumen_offen_eur"), 2, " €")],
        ["Skonto gefährdet", f"{fmt_de(a.skonto_gefaehrdet_eur)} €", f"{fmt_de(vp.skonto_gefaehrdet_eur)} €",
         _vz(p.periode.delta("skonto_gefaehrdet_eur"), 2, " €")],
        ["Ø Durchlaufzeit", f"{fmt_de(a.durchlaufzeit_avg_stunden or 0, 1)} h",
         f"{fmt_de(vp.durchlaufzeit_avg_stunden or 0, 1)} h", _vz(p.periode.delta("durchlaufzeit_avg_stunden"), 1, " h")],
    ]
    geloest = [f for f in klaerfaelle.values() if f.status == "gelöst"]
    klaerungen = [[f.rechnungsnummer, f.lieferant, f"{fmt_de(f.brutto)} €", f.grund, f.loesung or "",
                   " / ".join(f.freigegeben_von) + (" (Vier-Augen)" if f.vier_augen else ""),
                   fmt_datum(f.freigaben[-1][1].date()) if f.freigaben else ""] for f in geloest]
    z = p.zahlungsvorschlag(klaerfaelle)
    zahlungen = [[x.rechnungsnummer, x.lieferant, f"{fmt_de(x.brutto)} €", fmt_datum(x.zahltag) if x.zahltag else "–",
                  f"{fmt_de(x.skonto_betrag)} €" if x.skonto_betrag else "–", f"{fmt_de(x.zahlbetrag)} €", x.zustand]
                 for x in z.positionen if x.zustand != "gesperrt"]
    regeln: dict[str, int] = {}
    for h in p.risiken_bestand:
        regeln[h.titel] = regeln.get(h.titel, 0) + 1
    groesster = p.treiber_summen[0] if p.treiber_summen else None
    kern = [
        f"{a.eingegangen} Rechnungen in {p.periode.tage} Tagen bis {fmt_datum(p.stichtag)}; Touchless-Quote "
        f"{fmt_de(a.touchless_quote_prozent or 0, 1)} % ({_vz(p.periode.delta('touchless_quote_prozent'), 1, ' Pp')} "
        "ggü. Vorperiode).",
        f"{len(geloest)} Klärfälle gelöst und namentlich freigegeben; {len(klaerfaelle) - len(geloest)} weitere sind "
        "offen bzw. in Klärung und nicht Bestandteil dieses Berichts.",
        f"Zahlungsvorschlag: {len(zahlungen)} Rechnungen über {fmt_de(z.zahlbetrag)} €, Skonto gesichert "
        f"{fmt_de(z.skonto_gesichert)} €, gefährdet {fmt_de(z.skonto_gefaehrdet)} €.",
    ]
    if groesster:
        kern.insert(1, f"Größter Abweichungstreiber im Eingang: {groesster.treiber} mit {fmt_de(groesster.betrag)} € "
                       f"netto ({groesster.anzahl_rechnungen} Rechnung(en)).")
    return Berichtsinhalt(
        titel="Prüfbericht Purchase-to-Pay",
        untertitel=f"{FIRMA} · Stichtag {fmt_datum(p.stichtag)} · synthetische Daten",
        kennzahlen=kennzahlen, kernaussagen=kern, klaerungen=klaerungen,
        nicht_enthalten=len(klaerfaelle) - len(geloest),
        treiber=[(s.treiber, s.betrag, s.anzahl_rechnungen) for s in p.treiber_summen],
        zahlungen=zahlungen, zahlung_summen=(z.zahlbetrag, z.skonto_gesichert, z.skonto_gefaehrdet),
        praevention=[[titel, str(n)] for titel, n in sorted(regeln.items(), key=lambda x: -x[1])],
    )


# =============================================================================
# PDF
# =============================================================================


def erstelle_pdf(p: Pruefstand, klaerfaelle: Mapping[str, Klaerfall]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    b = berichtsinhalt(p, klaerfaelle)
    puffer = io.BytesIO()
    doc = SimpleDocTemplate(puffer, pagesize=landscape(A4), leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=b.titel, author="FlowAudit-AI")
    basis = getSampleStyleSheet()
    s_titel = ParagraphStyle("t", parent=basis["Title"], fontName="Helvetica-Bold", fontSize=20, alignment=0,
                             textColor=colors.HexColor("#1C1B19"), spaceAfter=2)
    s_unter = ParagraphStyle("u", parent=basis["Normal"], fontSize=9, textColor=colors.HexColor("#66645E"))
    s_h = ParagraphStyle("h", parent=basis["Heading2"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10,
                         textColor=colors.HexColor("#1C1B19"))
    s_text = ParagraphStyle("n", parent=basis["Normal"], fontSize=9.5, leading=13)
    s_zelle = ParagraphStyle("z", parent=basis["Normal"], fontSize=8, leading=10)

    def tabelle(kopf: list[str], zeilen: list[list[str]], breiten: list[float]) -> Table:
        daten = [[Paragraph(f"<b>{_text(k)}</b>", s_zelle) for k in kopf]]
        daten += [[Paragraph(_text(z), s_zelle) for z in zeile] for zeile in zeilen] or [
            [Paragraph("–", s_zelle) for _ in kopf]]
        t = Table(daten, colWidths=[w * mm for w in breiten], repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#2B2A27")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E3E1DB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    inhalt = [Paragraph(_text(b.titel), s_titel), Paragraph(_text(b.untertitel), s_unter), Spacer(1, 6)]
    inhalt.append(Paragraph("Kernaussagen", s_h))
    inhalt += [Paragraph("• " + _text(k), s_text) for k in b.kernaussagen]
    inhalt.append(Paragraph(f"Kennzahlen (letzte {p.periode.tage} Tage vs. Vorperiode)", s_h))
    inhalt.append(tabelle(["Kennzahl", "Berichtsperiode", "Vorperiode", "Veränderung"], b.kennzahlen, [70, 45, 45, 45]))
    inhalt.append(Paragraph("Freigegebene Klärungen", s_h))
    inhalt.append(tabelle(["Rechnung", "Lieferant", "Brutto", "Grund", "Lösung", "Freigabe durch", "Datum"],
                          b.klaerungen, [28, 45, 22, 45, 70, 35, 20]))
    inhalt.append(Paragraph(_text(f"Nicht enthalten: {b.nicht_enthalten} offene bzw. noch nicht vollständig "
                                  "freigegebene Klärfälle (Entwürfe erscheinen nie im Bericht)."), s_unter))
    inhalt.append(Paragraph("Abweichungstreiber im Eingang (netto)", s_h))
    inhalt.append(tabelle(["Treiber", "Betrag", "Rechnungen"],
                          [[t, f"{fmt_de(x)} €", str(n)] for t, x, n in b.treiber], [60, 35, 30]))
    inhalt.append(Paragraph("Zahlungsvorschlag", s_h))
    inhalt.append(tabelle(["Rechnung", "Lieferant", "Brutto", "Zahltag", "Skonto", "Zahlbetrag", "Art"], b.zahlungen,
                          [28, 55, 25, 22, 22, 25, 22]))
    zb, sg, sf = b.zahlung_summen
    inhalt.append(Paragraph(_text(f"Summe {fmt_de(zb)} € · Skonto gesichert {fmt_de(sg)} € · gefährdet {fmt_de(sf)} €"),
                            s_unter))
    inhalt.append(Paragraph("Präventionsregeln (Treffer im Bestand)", s_h))
    inhalt.append(tabelle(["Regel", "Treffer"], b.praevention, [80, 25]))
    inhalt.append(Spacer(1, 8))
    inhalt.append(Paragraph(_text(f"Erstellt mit FlowAudit-AI am {datetime.now():%d.%m.%Y %H:%M} · deterministische "
                                  "Prüfung, KI nur für Formulierungen · keine Steuer- oder Rechtsberatung."), s_unter))
    doc.build(inhalt)
    return puffer.getvalue()


# =============================================================================
# PowerPoint
# =============================================================================


def erstelle_pptx(p: Pruefstand, klaerfaelle: Mapping[str, Klaerfall]) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    b = berichtsinhalt(p, klaerfaelle)
    dunkel, leise, akzent = RGBColor(0x1C, 0x1B, 0x19), RGBColor(0x66, 0x64, 0x5E), RGBColor(0xB8, 0x5A, 0x33)
    hell = RGBColor(0xF3, 0xF2, 0xEE)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    leer = prs.slide_layouts[6]

    def folie(titel: str, unter: str = ""):  # noqa: ANN202
        f = prs.slides.add_slide(leer)
        balken = f.shapes.add_shape(1, Inches(0.6), Inches(0.55), Inches(0.08), Inches(0.62))
        balken.fill.solid()
        balken.fill.fore_color.rgb = akzent
        balken.line.fill.background()
        box = f.shapes.add_textbox(Inches(0.85), Inches(0.45), Inches(11.8), Inches(0.9)).text_frame
        box.word_wrap = True  # feste Breite: sonst zentriert LibreOffice das automatisch skalierte Textfeld
        box.text = titel
        box.paragraphs[0].alignment = PP_ALIGN.LEFT
        box.paragraphs[0].runs[0].font.size, box.paragraphs[0].runs[0].font.bold = Pt(26), True
        box.paragraphs[0].runs[0].font.color.rgb = dunkel
        if unter:
            absatz = box.add_paragraph()
            absatz.text = unter
            absatz.alignment = PP_ALIGN.LEFT
            absatz.runs[0].font.size, absatz.runs[0].font.color.rgb = Pt(12), leise
        return f

    def tabelle(f, kopf: list[str], zeilen: list[list[str]], oben: float, breiten: list[float]) -> None:  # noqa: ANN001
        zeilen = zeilen or [["–"] * len(kopf)]
        form = f.shapes.add_table(len(zeilen) + 1, len(kopf), Inches(0.6), Inches(oben), Inches(sum(breiten)),
                                  Inches(0.4 * (len(zeilen) + 1))).table
        for j, w in enumerate(breiten):
            form.columns[j].width = Inches(w)
        for i, zeile in enumerate([kopf, *zeilen]):
            for j, wert in enumerate(zeile):
                zelle = form.cell(i, j)
                zelle.text = str(wert)
                zelle.fill.solid()
                zelle.fill.fore_color.rgb = dunkel if i == 0 else (hell if i % 2 else RGBColor(0xFF, 0xFF, 0xFF))
                run = zelle.text_frame.paragraphs[0].runs[0]
                run.font.size, run.font.bold = Pt(12), i == 0
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if i == 0 else dunkel

    f = folie(b.titel, b.untertitel)
    text = f.shapes.add_textbox(Inches(0.85), Inches(1.8), Inches(11.6), Inches(4.5)).text_frame
    text.word_wrap = True
    for i, k in enumerate(b.kernaussagen):
        absatz = text.paragraphs[0] if i == 0 else text.add_paragraph()
        absatz.text = "• " + k
        absatz.runs[0].font.size = Pt(17)
        absatz.space_after = Pt(10)

    f = folie("Kennzahlen", f"Letzte {p.periode.tage} Tage bis {fmt_datum(p.stichtag)} gegenüber der Vorperiode")
    tabelle(f, ["Kennzahl", "Berichtsperiode", "Vorperiode", "Veränderung"], b.kennzahlen, 1.7, [4.2, 2.8, 2.8, 2.8])

    f = folie("Abweichungstreiber im Eingang", "Nettobetrag je Treiber · Summe der Treiber = Rechnung netto (centgenau)")
    daten = CategoryChartData()
    reihe = list(reversed(b.treiber))  # Balkendiagramm zeichnet von unten nach oben: größter Treiber oben
    daten.categories = [t for t, _, _ in reihe] or ["–"]
    daten.add_series("Betrag in €", [float(x) for _, x, _ in reihe] or [0.0])
    grafik = f.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.6), Inches(12), Inches(5.3),
                                daten).chart
    grafik.has_legend = False
    grafik.has_title = False
    grafik.plots[0].has_data_labels = True
    # Beschriftung als Text im deutschen Format (Zahlenformate rendern je nach Systemsprache mit Komma oder Punkt);
    # die Werteachse entfällt, weil jeder Balken seinen Betrag trägt
    for punkt, (_, betrag, _) in zip(grafik.series[0].points, reihe, strict=False):
        punkt.data_label.text_frame.text = f"{fmt_de(betrag)} €"
        punkt.data_label.text_frame.paragraphs[0].runs[0].font.size = Pt(16)
    grafik.value_axis.visible = False
    grafik.value_axis.has_major_gridlines = False
    grafik.category_axis.tick_labels.font.size = Pt(16)
    grafik.series[0].format.fill.solid()
    grafik.series[0].format.fill.fore_color.rgb = dunkel

    f = folie("Freigegebene Klärungen", f"Nur gelöste und namentlich freigegebene Fälle · nicht enthalten: "
                                        f"{b.nicht_enthalten} offene Klärfälle")
    tabelle(f, ["Rechnung", "Lieferant", "Brutto", "Grund", "Lösung", "Freigabe durch"],
            [z[:6] for z in b.klaerungen], 1.7, [1.6, 2.2, 1.3, 2.4, 3.0, 1.6])

    zb, sg, sf = b.zahlung_summen
    f = folie("Zahlungsvorschlag", f"Summe {fmt_de(zb)} € · Skonto gesichert {fmt_de(sg)} € · gefährdet {fmt_de(sf)} €")
    tabelle(f, ["Rechnung", "Lieferant", "Brutto", "Zahltag", "Skonto", "Zahlbetrag", "Art"], b.zahlungen, 1.7,
            [1.6, 3.0, 1.5, 1.4, 1.3, 1.6, 1.4])
    puffer = io.BytesIO()
    prs.core_properties.title, prs.core_properties.author = b.titel, "FlowAudit-AI"
    prs.save(puffer)
    return puffer.getvalue()


__all__ = ["Berichtsinhalt", "berichtsinhalt", "erstelle_pdf", "erstelle_pptx"]
