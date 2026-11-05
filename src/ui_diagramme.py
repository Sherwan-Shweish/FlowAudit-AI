"""Interaktive Diagramme des Cockpits (Altair/Vega-Lite) im Stil der RheinMain Controlling-Suite.

Gleiche Regeln wie ``ui_diagramme.py`` in VarianceAI:

* Farben ausschließlich aus der aktiven Palette (:data:`src.ui_design.FARBEN`) – hell und dunkel.
* Ist-Werte (AC) hell bzw. dunkel gefüllt, Vergleich/Rest grau (PY-Ton); Urteile in den validierten
  Farben günstig/Warnung/ungünstig und **immer** mit Wort bzw. Zahl beschriftet, nie Farbe allein.
* Keine abgeschnittenen Achsen: Balken beginnen bei 0, Platz für Beschriftungen über das Skalen-Padding.
* Beschriftungen und Tooltips deutsch formatiert (``1.234,5``) – in Python, nicht in Vega.
* Der Akzent (Kupfer) ist Bedienung und KI vorbehalten und erscheint in keinem Diagramm.

Außerdem: das Mermaid-Farbschema für BPMN/eEPK aus derselben Palette (:func:`mermaid_farbschema`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

import altair as alt
import pandas as pd

from src.agent import fmt_de
from src.bpmn_tracker import Farbschema, Wochenwert
from src.ui_design import FARBEN, mische

# Kurztexte der Workflow-Schritte (gleiche Begriffe wie im Excel-Prüfbericht)
SCHRITT_KURZ = {
    "TOUCHLESS_FREIGABE": "Touchless-Freigabe",
    "SPERRE_DUBLETTE": "Sperre: Dublette",
    "SPERRE_FEHLENDER_WARENEINGANG": "Sperre: Wareneingang fehlt",
    "KLAERUNG_LIEFERANT_FORMFEHLER": "Klärung Lieferant: Formfehler",
    "KLAERUNG_EINKAUF_PREIS": "Klärung Einkauf: Preis",
    "KLAERUNG_EINKAUF_MENGE": "Klärung Einkauf: Menge",
    "COMPLIANCE_MAVERICK_BUYING": "Compliance: Maverick Buying",
    "ABGELEHNT": "Abgewiesen (keine E-Rechnung)",
}


def _zahl(wert: float | Decimal | int, stellen: int = 0) -> str:
    return fmt_de(Decimal(str(wert)), stellen)


def _thema(chart: alt.TopLevelMixin, hoehe: int) -> alt.TopLevelMixin:
    f = FARBEN
    return (
        chart.properties(height=hoehe, background="transparent", padding={"left": 4, "right": 8, "top": 12, "bottom": 4})
        .configure_view(stroke=None)
        .configure_axis(labelColor=f["text_leise"], titleColor=f["text_leise"], gridColor=f["raster"],
                        domainColor=f["linie_stark"], tickColor=f["linie_stark"], labelFont="Inter", titleFont="Inter",
                        labelFontSize=11, titleFontSize=11, titleFontWeight=500, labelPadding=6)
        .configure_text(font="Inter", fontSize=11)
        .configure_legend(labelColor=f["text_2"], titleColor=f["text_leise"], labelFont="Inter", titleFont="Inter",
                          orient="top", direction="horizontal", symbolSize=90, labelFontSize=11)
    )


def _achse_anzahl() -> alt.Axis:
    """Ganzzahlige Achse mit deutschem Tausenderpunkt."""
    return alt.Axis(title=None, tickCount=4, domain=False, ticks=False, tickMinStep=1,
                    labelExpr="replace(format(datum.value, ',.0f'), ',', '.')")


# =============================================================================
# Wochenverlauf: Eingänge ohne und mit manuellem Eingriff
# =============================================================================


def wochendaten(reihen: Sequence[Wochenwert]) -> pd.DataFrame:
    zeilen = []
    for w in reihen:
        woche = f"{w.bis:%d.%m.}"
        quote = (f" ({fmt_de(w.touchless_quote_prozent, 1)} % ohne Eingriff)"
                 if w.touchless_quote_prozent is not None else "")
        tip = f"{w.von:%d.%m.}–{w.bis:%d.%m.%Y}: {w.eingegangen} Eingänge{quote}"
        zeilen.append({"woche": woche, "art": "ohne Eingriff", "anzahl": w.touchless, "tooltip": tip,
                       "summe": w.eingegangen})
        zeilen.append({"woche": woche, "art": "mit Eingriff", "anzahl": w.eingegangen - w.touchless, "tooltip": tip,
                       "summe": w.eingegangen})
    return pd.DataFrame(zeilen)


def wochenverlauf(reihen: Sequence[Wochenwert], hoehe: int = 250) -> alt.TopLevelMixin:
    """Gestapelte Säulen je 7-Tage-Fenster: ohne Eingriff (AC-Ton) und mit Eingriff (grau); Summe darüber."""
    df = wochendaten(reihen)
    f = FARBEN
    reihenfolge = list(dict.fromkeys(df["woche"]))
    x = alt.X("woche:N", sort=reihenfolge, title=None, axis=alt.Axis(labelAngle=0, ticks=False, domain=False))
    hoechstwert = max(float(df.groupby("woche")["anzahl"].sum().max() or 0), 1.0)
    y = alt.Y("anzahl:Q", stack="zero", axis=_achse_anzahl(), scale=alt.Scale(domain=[0, hoechstwert * 1.18]))
    saeulen = alt.Chart(df).mark_bar(width=22, cornerRadius=1.5).encode(
        x=x, y=y, order=alt.Order("art:N", sort="descending"),
        fill=alt.Fill("art:N", scale=alt.Scale(domain=["ohne Eingriff", "mit Eingriff"], range=[f["ac"], f["py"]]),
                      legend=alt.Legend(title=None)),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    summen = df.drop_duplicates("woche").assign(label=lambda d: d["summe"].map(lambda v: _zahl(v)))
    text = alt.Chart(summen).mark_text(dy=-8, color=f["text"], fontWeight=600).encode(
        x=x, y=alt.Y("summe:Q", axis=None), text="label:N")
    return _thema(alt.layer(saeulen, text), hoehe)


# =============================================================================
# Sperr- und Klärungsgründe (horizontale Balken)
# =============================================================================


def gruendedaten(sperrgruende: Mapping[str, int]) -> pd.DataFrame:
    gesamt = sum(sperrgruende.values()) or 1
    return pd.DataFrame([{
        "grund": SCHRITT_KURZ.get(k, k), "anzahl": v,
        "art": "Sperre" if k.startswith("SPERRE") else "Klärung",
        "label": f"{_zahl(v)} · {fmt_de(Decimal(v) * 100 / gesamt, 0)} %",
        "tooltip": f"{SCHRITT_KURZ.get(k, k)}: {_zahl(v)} Rechnungen ({fmt_de(Decimal(v) * 100 / gesamt, 1)} %)",
    } for k, v in sorted(sperrgruende.items(), key=lambda x: (-x[1], x[0]))])


def gruendebalken(sperrgruende: Mapping[str, int]) -> alt.TopLevelMixin:
    df = gruendedaten(sperrgruende)
    f = FARBEN
    if df.empty:
        df = pd.DataFrame([{"grund": "keine Klärungsfälle", "anzahl": 0, "art": "Klärung", "label": "0", "tooltip": ""}])
    y = alt.Y("grund:N", sort=list(df["grund"]), title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=240))
    x = alt.X("anzahl:Q", axis=None, scale=alt.Scale(domain=[0, max(float(df["anzahl"].max()), 1.0) * 1.35]))
    balken = alt.Chart(df).mark_bar(size=14, cornerRadius=2).encode(
        x=x, y=y, color=alt.Color("art:N", scale=alt.Scale(domain=["Klärung", "Sperre"],
                                                            range=[f["warnung"], f["schlecht"]]),
                                  legend=alt.Legend(title=None)),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    text = alt.Chart(df).mark_text(align="left", dx=6, color=f["text"]).encode(
        x=alt.X("anzahl:Q", axis=None), y=y, text="label:N")
    return _thema(alt.layer(balken, text), max(150, 30 * len(df) + 40))


# =============================================================================
# Gesamturteile
# =============================================================================


def urteilsbalken(freigabe: int, klaerung: int, sperre: int) -> alt.TopLevelMixin:
    """Drei Urteile als Balken, beschriftet mit Anzahl und Anteil (kein Kreisdiagramm)."""
    gesamt = max(freigabe + klaerung + sperre, 1)
    df = pd.DataFrame([
        {"urteil": name, "anzahl": n, "label": f"{_zahl(n)} · {fmt_de(Decimal(n) * 100 / gesamt, 0)} %",
         "tooltip": f"{name}: {_zahl(n)} Rechnungen ({fmt_de(Decimal(n) * 100 / gesamt, 1)} %)"}
        for name, n in (("Freigabe (touchless)", freigabe), ("Klärung", klaerung), ("Sperre", sperre))
    ])
    f = FARBEN
    y = alt.Y("urteil:N", sort=list(df["urteil"]), title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=200))
    x = alt.X("anzahl:Q", axis=None, scale=alt.Scale(domain=[0, max(float(df["anzahl"].max()), 1.0) * 1.35]))
    balken = alt.Chart(df).mark_bar(size=16, cornerRadius=2).encode(
        x=x, y=y, color=alt.Color("urteil:N", legend=None, scale=alt.Scale(
            domain=list(df["urteil"]), range=[f["gut"], f["warnung"], f["schlecht"]])),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    text = alt.Chart(df).mark_text(align="left", dx=6, color=f["text"]).encode(
        x=alt.X("anzahl:Q", axis=None), y=y, text="label:N")
    return _thema(alt.layer(balken, text), 150)


# =============================================================================
# Mermaid (BPMN 2.0 / eEPK) in den Farben der aktiven Palette
# =============================================================================


def mermaid_farbschema() -> Farbschema:
    """Klassenfarben für ``bpmn_tracker.prozessdiagramm`` – deckende Hexwerte (Mermaid kennt kein rgba in classDef)."""
    f = FARBEN

    def aktiv(farbe: str) -> str:
        return f"fill:{mische(farbe, f['karte'], 0.22)},stroke:{farbe},stroke-width:3px,color:{f['text']}"

    return Farbschema(
        knoten={
            "besucht": f"fill:{f['karte_2']},stroke:{f['text_2']},stroke-width:1.5px,color:{f['text']}",
            "aktiv_FREIGABE": aktiv(f["gut"]),
            "aktiv_KLAERUNG": aktiv(f["warnung"]),
            "aktiv_SPERRE": aktiv(f["schlecht"]),
            "inaktiv": f"fill:{f['flaeche']},stroke:{f['linie']},color:{f['text_leise']}",
            "org": f"fill:{f['karte']},stroke:{f['py']},color:{f['text_2']}",
            "info": f"fill:{f['karte']},stroke:{f['linie_stark']},color:{f['text_2']}",
        },
        kante_pfad=f"stroke:{f['text']},stroke-width:2.5px",
        kante_inaktiv=f"stroke:{f['linie_stark']},stroke-width:1px",
        kante_zuordnung=f"stroke:{f['text_leise']},stroke-width:1px,stroke-dasharray:3 3",
    )


def mermaid_thema() -> dict[str, str]:
    """``themeVariables`` für ``mermaid.initialize`` (Grundfarben außerhalb der Klassen)."""
    f = FARBEN
    return {
        "background": f["karte"], "primaryColor": f["karte_2"], "primaryTextColor": f["text"],
        "primaryBorderColor": f["linie_stark"], "lineColor": f["text_leise"], "secondaryColor": f["karte"],
        "tertiaryColor": f["karte"], "edgeLabelBackground": f["karte"], "titleColor": f["text"],
        "textColor": f["text"], "fontFamily": "Inter, -apple-system, 'Segoe UI', sans-serif", "fontSize": "14px",
    }


# =============================================================================
# Abweichungstreiber im 3-Way-Matching (Brücke je Rechnung, Balken je Treiber)
# =============================================================================


def treiberdaten(rt) -> pd.DataFrame:  # noqa: ANN001 – src.treiber.Rechnungstreiber
    """Brücke „gedeckt → Treiber → Rechnung netto“ in €; Summe der Treiber = Netto − gedeckt (Invariante)."""
    zeilen = [{"schritt": "gedeckt", "von": 0.0, "bis": float(rt.gedeckt), "art": "ac",
               "label": _zahl(rt.gedeckt, 2), "tooltip": f"durch Bestellung und Wareneingang gedeckt: "
                                                          f"{_zahl(rt.gedeckt, 2)} €"}]
    stand = float(rt.gedeckt)
    for name, betrag in rt.treiber.items():
        if not betrag:
            continue
        d = float(betrag)
        zeilen.append({"schritt": name, "von": stand, "bis": stand + d, "art": "schlecht" if d > 0 else "gut",
                       "label": ("+" if d > 0 else "−") + _zahl(abs(betrag), 2),
                       "tooltip": f"{name}: {'+' if d > 0 else '−'}{_zahl(abs(betrag), 2)} €"})
        stand += d
    zeilen.append({"schritt": "Rechnung netto", "von": 0.0, "bis": float(rt.netto), "art": "ac",
                   "label": _zahl(rt.netto, 2), "tooltip": f"Rechnung netto: {_zahl(rt.netto, 2)} €"})
    df = pd.DataFrame(zeilen)
    df["oben"] = df[["von", "bis"]].max(axis=1)
    df["unten"] = df[["von", "bis"]].min(axis=1)
    return df


def treiberbruecke(rt, hoehe: int = 260) -> alt.TopLevelMixin:  # noqa: ANN001
    df = treiberdaten(rt)
    f = FARBEN
    x = alt.X("schritt:N", sort=list(df["schritt"]), title=None,
              axis=alt.Axis(labelAngle=0, labelLimit=120, ticks=False, domain=False))
    oben = max(float(df["oben"].max()), 1.0)
    y = alt.Y("von:Q", axis=None, scale=alt.Scale(domain=[0, oben * 1.15]))
    balken = alt.Chart(df).mark_bar(size=36, cornerRadius=2).encode(
        x=x, y=y, y2="bis:Q", tooltip=alt.Tooltip("tooltip:N", title=None),
        fill=alt.Fill("art:N", legend=None, scale=alt.Scale(domain=["ac", "gut", "schlecht"],
                                                             range=[f["ac"], f["gut"], f["schlecht"]])))
    text = alt.Chart(df).mark_text(dy=-9, color=f["text"], fontWeight=600).encode(
        x=x, y=alt.Y("oben:Q", axis=None), text="label:N")
    null = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(color=f["linie_stark"]).encode(y=alt.Y("y:Q", axis=None))
    return _thema(alt.layer(null, balken, text), hoehe)


def treiberbalken(summen) -> alt.TopLevelMixin:  # noqa: ANN001 – Sequence[src.treiber.Treibersumme]
    df = pd.DataFrame([{
        "treiber": s.treiber, "betrag": float(s.betrag), "art": "schlecht" if s.betrag > 0 else "gut",
        "label": f"{'+' if s.betrag > 0 else '−'}{_zahl(abs(s.betrag), 2)} € · {s.anzahl_rechnungen} Rg.",
        "tooltip": f"{s.treiber}: {_zahl(s.betrag, 2)} € netto in {s.anzahl_rechnungen} Rechnung(en)",
    } for s in summen]) if summen else pd.DataFrame([{"treiber": "keine Abweichungen", "betrag": 0.0, "art": "gut",
                                                       "label": "0", "tooltip": ""}])
    f = FARBEN
    y = alt.Y("treiber:N", sort=list(df["treiber"]), title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=200))
    x = alt.X("betrag:Q", axis=None, scale=alt.Scale(domain=[min(float(df["betrag"].min()), 0.0),
                                                             max(float(df["betrag"].max()), 1.0) * 1.45]))
    balken = alt.Chart(df).mark_bar(size=14, cornerRadius=2).encode(
        x=x, y=y, tooltip=alt.Tooltip("tooltip:N", title=None),
        color=alt.Color("art:N", legend=None, scale=alt.Scale(domain=["gut", "schlecht"],
                                                               range=[f["gut"], f["schlecht"]])))
    text = alt.Chart(df).mark_text(align="left", dx=6, color=f["text"]).encode(
        x=alt.X("betrag:Q", axis=None), y=y, text="label:N")
    return _thema(alt.layer(balken, text), max(150, 30 * len(df) + 30))
