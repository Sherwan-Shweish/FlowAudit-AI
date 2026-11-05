"""Designsystem des Streamlit-Cockpits: Farbtoken, Stylesheet und HTML-Bausteine.

Übernommen 1:1 aus VarianceAI (Referenzimplementierung des Agenten-Standards v1) – damit alle Agenten der
RheinMain Controlling-Suite gleich aussehen. Das CSS-Präfix ``va-`` ist das gemeinsame Präfix der Suite.
Projektspezifisch sind nur :data:`LOGO` und die Inhalte, die ``app.py`` übergibt.

Gestaltungsregeln (bewusst begründet, nicht nur Geschmack):

* **Dunkle, ruhige Fläche – Daten im Vordergrund.** Hintergrund und Karten sind
  neutral (warmes Anthrazit); Farbe trägt nur Bedeutung.
* **Farbe hat genau eine Aufgabe je Rolle.**
  - Datenfarben folgen IBCS: Ist (AC) hell gefüllt, Vorjahr (PY) grau, Plan (PL)
    umrandet, Forecast (FC) gestrichelt/transparent.
  - Abweichungen: günstig ``#3AAB86`` / ungünstig ``#C8382F`` – auf der dunklen
    Fläche validiert (Kontrast ≥ 3:1, Farbsehschwäche ΔE 12,6 deutan) und
    zusätzlich immer mit Vorzeichen beschriftet, nie Farbe allein.
  - Der Akzent Kupfer ``#D9825B`` ist **ausschließlich** für Interaktion und
    KI-Elemente reserviert und erscheint nie in Diagrammen.
  - Statusfarben (Ampel) immer mit Symbol und Text.
* **Typografie:** Inter (UI) und JetBrains Mono (Protokolle, IDs), lokal
  ausgeliefert – das Cockpit funktioniert offline. Ziffern tabellarisch
  (``tabular-nums``), damit Beträge spaltengenau stehen.
* **Sicherheit:** Jeder dynamische Text wird mit :func:`e` HTML-maskiert.
  Rechnungsinhalte (Freitexte BT-22, Lieferantennamen, Rechnungsnummern) sind
  unvertrauenswürdige Daten (siehe Prompt-Injection-Fall) und dürfen weder das LLM
  noch die Oberfläche steuern.
* **Urteile** (Freigabe · Klärung · Sperre) nutzen dieselben validierten Farben wie
  günstig/Warnung/ungünstig – immer zusammen mit dem Wort, nie Farbe allein.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Ton = Literal["neutral", "gut", "schlecht", "warnung", "akzent", "leise"]

LOGO = "§"  # Logo-Glyphe des Agenten (VarianceAI: „Δ“) – auch in desktop/app.toml für die Ladeseite

# =============================================================================
# Farbtoken – zwei Paletten (dunkel/hell), umschaltbar je Sitzung
# =============================================================================

PALETTEN: dict[str, dict[str, str]] = {
    "dunkel": {
        "flaeche": "#0E0E0D", "karte": "#161615", "karte_2": "#1C1C1A",
        "linie": "#2A2926", "linie_stark": "#3A3935", "raster": "#232321",
        "text": "#EDEBE6", "text_2": "#B9B7AF", "text_leise": "#8C8A83",
        # IBCS auf dunkler Fläche: AC hell gefüllt, PL hell umrandet
        "ac": "#EDEBE6", "py": "#6E6C66", "pl": "#EDEBE6",
        # Abweichungen (validiert auf #161615: Kontrast ≥ 3:1, CVD ΔE 12,6) und Textvarianten ≥ 4,5:1
        "gut": "#3AAB86", "schlecht": "#C8382F", "gut_text": "#5CC79F", "schlecht_text": "#F07A6E",
        "warnung": "#E6B450",
        # Akzent: nur Interaktion und KI
        "akzent": "#D9825B", "akzent_text": "#E39A6F",
        "log_flaeche": "#101010", "log_linie": "#1D1D1B",
    },
    "hell": {
        "flaeche": "#F7F6F3", "karte": "#FFFFFF", "karte_2": "#F3F2EE",
        "linie": "#E3E1DB", "linie_stark": "#CBC8C0", "raster": "#ECEAE5",
        "text": "#1C1B19", "text_2": "#4B4A45", "text_leise": "#66645E",
        # IBCS auf heller Fläche: AC dunkel gefüllt, PL dunkel umrandet (wie im Monatsbericht)
        "ac": "#2B2A27", "py": "#A9A69E", "pl": "#2B2A27",
        # validiert auf #FFFFFF: Kontrast ≥ 3:1, CVD ΔE 8,8 (deutan); Textvarianten ≥ 4,5:1
        "gut": "#2A9D72", "schlecht": "#C8382F", "gut_text": "#1E6E4E", "schlecht_text": "#B3261E",
        "warnung": "#8A5A00",
        "akzent": "#B85A33", "akzent_text": "#A4522C",
        "log_flaeche": "#FAFAF8", "log_linie": "#ECEAE5",
    },
}
MODI = ("dunkel", "hell")

# Streamlit-Thema je Modus (wird zur Laufzeit gesetzt, siehe app.py / desktop/launcher.py)
STREAMLIT_THEMA: dict[str, dict[str, str]] = {
    "dunkel": {
        "theme.base": "dark", "theme.primaryColor": "#D9825B", "theme.backgroundColor": "#0E0E0D",
        "theme.secondaryBackgroundColor": "#1C1C1A", "theme.textColor": "#EDEBE6", "theme.linkColor": "#E39A6F",
        "theme.borderColor": "#2A2926", "theme.codeBackgroundColor": "#161615",
        "theme.dataframeHeaderBackgroundColor": "#161615", "theme.dataframeBorderColor": "#2A2926",
        "theme.greenColor": "#3AAB86", "theme.redColor": "#C8382F",
        "theme.sidebar.backgroundColor": "#121211", "theme.sidebar.secondaryBackgroundColor": "#1C1C1A",
    },
    "hell": {
        "theme.base": "light", "theme.primaryColor": "#B85A33", "theme.backgroundColor": "#F7F6F3",
        "theme.secondaryBackgroundColor": "#EFEDE8", "theme.textColor": "#1C1B19", "theme.linkColor": "#A4522C",
        "theme.borderColor": "#E3E1DB", "theme.codeBackgroundColor": "#F3F2EE",
        "theme.dataframeHeaderBackgroundColor": "#F3F2EE", "theme.dataframeBorderColor": "#E3E1DB",
        "theme.greenColor": "#2A9D72", "theme.redColor": "#C8382F",
        "theme.sidebar.backgroundColor": "#FFFFFF", "theme.sidebar.secondaryBackgroundColor": "#F3F2EE",
    },
}

_aktiver_modus: ContextVar[str] = ContextVar("va_modus", default="dunkel")


def setze_modus(modus: str) -> None:
    """Aktiviert die Palette für die laufende Sitzung (Streamlit: je Skriptlauf aufrufen)."""
    if modus not in PALETTEN:
        raise ValueError(f"Unbekannter Darstellungsmodus: {modus!r}")
    _aktiver_modus.set(modus)


def aktiver_modus() -> str:
    return _aktiver_modus.get()


class _AktivePalette(Mapping[str, str]):
    """``FARBEN[...]`` liefert immer den Wert der gerade aktiven Palette."""

    def __getitem__(self, schluessel: str) -> str:
        return PALETTEN[_aktiver_modus.get()][schluessel]

    def __iter__(self) -> Iterator[str]:
        return iter(PALETTEN[_aktiver_modus.get()])

    def __len__(self) -> int:
        return len(PALETTEN[_aktiver_modus.get()])


FARBEN: Mapping[str, str] = _AktivePalette()

_TON_SCHLUESSEL = {"neutral": "text_2", "gut": "gut_text", "schlecht": "schlecht_text", "warnung": "warnung",
                   "akzent": "akzent_text", "leise": "text_leise"}


def ton_farbe(ton: str) -> str:
    return FARBEN[_TON_SCHLUESSEL[ton]]


def rgba(hexfarbe: str, alpha: float) -> str:
    h = hexfarbe.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def e(text: object) -> str:
    """HTML-Maskierung für jeden dynamischen Text (auch Zahlen und IDs)."""
    return html.escape("" if text is None else str(text), quote=True)


# =============================================================================
# Symbole (eigene, schlichte Strichsymbole – keine Emojis in der Oberfläche)
# =============================================================================

_PFADE = {
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "kreis": '<circle cx="12" cy="12" r="4"/>',
    "schild": '<path d="M12 3l7 3v5.5c0 4.4-3 8-7 9.5-4-1.5-7-5.1-7-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    "lupe": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.6-4.6"/>',
    "diagramm": '<path d="M4 19h16"/><path d="M7 16v-5"/><path d="M12 16V7"/><path d="M17 16v-8"/>',
    "kurve": '<path d="M3 17l5-5 4 3 8-9"/><path d="M15 6h5v5"/>',
    "funke": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    "dokument": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4 2v-8.5z"/>',
    "uhr": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "blitz": '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
    "datenbank": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'
                 '<path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "schloss": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "warnung": '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.5v.5"/>',
    "kreuz": '<path d="M7 7l10 10M17 7L7 17"/>',
}


def svg_bild(svg: str, breite: int, hoehe: int, alt: str = "", klasse: str = "") -> str:
    """Bettet SVG als Data-URI-Bild ein.

    ``st.html`` bereinigt HTML mit DOMPurify und entfernt Inline-SVG. Als ``<img>``
    bleibt die Grafik erhalten, ohne die Bereinigung abzuschalten (Defense in Depth).
    """
    kodiert = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    versteckt = "" if alt else ' aria-hidden="true"'
    return (f'<img class="{klasse}" src="data:image/svg+xml;base64,{kodiert}" width="{breite}" height="{hoehe}" '
            f'alt="{e(alt)}"{versteckt}>')


def symbol(name: str, groesse: int = 16, farbe: str | None = None, strich: float = 1.8) -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{groesse}" height="{groesse}" viewBox="0 0 24 24" '
           f'fill="none" stroke="{farbe or FARBEN["text_2"]}" stroke-width="{strich}" stroke-linecap="round" '
           f'stroke-linejoin="round">{_PFADE[name]}</svg>')
    return svg_bild(svg, groesse, groesse, klasse="va-sym")


# =============================================================================
# Stylesheet
# =============================================================================

def stylesheet() -> str:
    f = FARBEN
    return f"""<style>
:root {{
  --va-flaeche:{f['flaeche']}; --va-karte:{f['karte']}; --va-karte-2:{f['karte_2']};
  --va-linie:{f['linie']}; --va-linie-stark:{f['linie_stark']};
  --va-text:{f['text']}; --va-text-2:{f['text_2']}; --va-leise:{f['text_leise']};
  --va-gut:{f['gut_text']}; --va-schlecht:{f['schlecht_text']}; --va-warnung:{f['warnung']};
  --va-akzent:{f['akzent']}; --va-akzent-text:{f['akzent_text']};
  --va-log-flaeche:{f['log_flaeche']}; --va-log-linie:{f['log_linie']};
  --va-mono:'JetBrains Mono', ui-monospace, monospace;
}}
/* --- Streamlit-Rahmen beruhigen ------------------------------------------------ */
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"], .block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1640px; }}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top: .4rem; }}
[data-testid="stTabs"] [role="tablist"] {{ gap: .2rem; border-bottom: 1px solid var(--va-linie); }}
[data-testid="stTab"] {{ padding: .5rem .7rem .6rem; border-radius: .55rem .55rem 0 0; }}
[data-testid="stTab"]:hover {{ background: var(--va-karte); }}
[data-testid="stTab"] p {{ font-size: .9rem; font-weight: 560; color: var(--va-leise); }}
[data-testid="stTab"][aria-selected="true"] p {{ color: var(--va-text); }}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ background: var(--va-akzent); height: 2px; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--va-karte); }}
[data-testid="stExpander"] details {{ background: var(--va-karte); border-color: var(--va-linie); }}
[data-testid="stChatMessage"] {{ background: var(--va-karte); border: 1px solid var(--va-linie); border-radius: .9rem; }}
[data-testid="stDataFrame"] {{ border-radius: .6rem; }}
h1, h2, h3 {{ letter-spacing: -.01em; }}
.stHtml {{ width: 100%; }}

/* --- Grundbausteine ----------------------------------------------------------- */
.va * {{ box-sizing: border-box; }}
.va {{ font-variant-numeric: tabular-nums; color: var(--va-text); }}
.va-sym {{ flex: none; display: inline-block; vertical-align: -3px; }}
.va-karte {{ background: var(--va-karte); border: 1px solid var(--va-linie); border-radius: 14px; padding: 16px 18px; }}
.va-karte + .va-karte {{ margin-top: 12px; }}
.va-ueberschrift {{ font-size: .76rem; letter-spacing: .09em; text-transform: uppercase; color: var(--va-leise);
  font-weight: 600; margin: 0 0 10px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
.va-ueberschrift span:last-child {{ text-transform: none; letter-spacing: 0; font-weight: 500; }}
.va-leise {{ color: var(--va-leise); }}
.va-mono {{ font-family: var(--va-mono); font-size: .8rem; }}
.va-pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: .78rem;
  font-weight: 600; border: 1px solid currentColor; line-height: 1.35; white-space: nowrap; }}
.va-pill--neutral {{ color: var(--va-text-2); border-color: var(--va-linie-stark); }}
.va-pill--gut {{ color: var(--va-gut); background: {rgba(f['gut'], .10)}; border-color: {rgba(f['gut'], .38)}; }}
.va-pill--schlecht {{ color: var(--va-schlecht); background: {rgba(f['schlecht'], .10)}; border-color: {rgba(f['schlecht'], .40)}; }}
.va-pill--warnung {{ color: var(--va-warnung); background: {rgba(f['warnung'], .10)}; border-color: {rgba(f['warnung'], .38)}; }}
.va-pill--akzent {{ color: var(--va-akzent-text); background: {rgba(f['akzent'], .10)}; border-color: {rgba(f['akzent'], .40)}; }}
.va-pill--leise {{ color: var(--va-leise); border-color: var(--va-linie); }}
.va-punkt {{ width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: inline-block; }}

/* --- Kopfzeile ------------------------------------------------------------------ */
.va-kopf {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap;
  padding: 14px 18px; background: var(--va-karte); border: 1px solid var(--va-linie); border-radius: 16px; }}
.va-marke {{ display: flex; align-items: center; gap: 12px; }}
.va-logo {{ flex: none; width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; color: #1a0f0a;
  background: linear-gradient(145deg, #E9A07A, #C96F47); font-weight: 800; font-size: 1.15rem; }}
.va-marke b {{ font-size: 1.05rem; letter-spacing: -.01em; }}
.va-marke small {{ display: block; color: var(--va-leise); font-size: .78rem; margin-top: 1px; }}
.va-kopf-mitte {{ font-size: 1.02rem; font-weight: 600; }}
.va-kopf-mitte small {{ display: block; font-weight: 400; color: var(--va-leise); font-size: .78rem; }}
.va-kopf-rechts {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}

/* --- Kennzahlen-Leiste ---------------------------------------------------------- */
.va-leiste {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 14px 0 8px; }}
.va-kpi {{ overflow: hidden; background: var(--va-karte); border: 1px solid var(--va-linie); border-radius: 14px; padding: 12px 14px;
  display: flex; flex-direction: column; min-width: 0; }}
.va-kpi--hervor {{ border-color: {rgba(f['akzent'], .45)}; box-shadow: inset 0 0 0 1px {rgba(f['akzent'], .12)}; }}
.va-kpi-label {{ font-size: .8rem; color: var(--va-leise); font-weight: 600; letter-spacing: .02em; }}
.va-kpi-wert {{ font-size: clamp(1.1rem, 1.55vw, 1.45rem); font-weight: 650; letter-spacing: -.015em; margin-top: 4px;
  white-space: nowrap; }}
.va-kpi-spark {{ order: 9; line-height: 0; margin-top: 10px; }}
.va-kpi-spark img {{ width: 100%; height: 26px; display: block; }}
.va-kpi-fuss {{ display: flex; gap: 8px; align-items: center; margin-top: 6px; font-size: .78rem;
  color: var(--va-leise); flex-wrap: wrap; }}

/* --- Held (große Zahl) ---------------------------------------------------------- */
.va-held {{ display: flex; align-items: flex-end; gap: 18px; flex-wrap: wrap; }}
.va-held-zahl {{ font-size: 2.6rem; font-weight: 700; letter-spacing: -.03em; line-height: 1; }}
.va-held-zahl small {{ font-size: 1rem; color: var(--va-leise); font-weight: 500; margin-left: 6px; letter-spacing: 0; }}
.va-held-meta {{ display: flex; gap: 8px; flex-wrap: wrap; padding-bottom: 4px; }}
.va-botschaft {{ font-size: 1.02rem; font-weight: 600; margin: 0 0 2px; }}
.va-unter {{ font-size: .8rem; color: var(--va-leise); margin: 0; }}

/* --- Copilot-Panel --------------------------------------------------------------- */
.va-copilot-kopf {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
.va-copilot-kopf .va-logo {{ width: 34px; height: 34px; }}
.va-copilot-kopf b {{ display: block; font-size: 1rem; }}
.va-liste {{ list-style: none; margin: 0; padding: 0; }}
.va-liste li {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; padding: 6px 0; font-size: .87rem; }}
.va-liste li .va-wert {{ margin-left: auto; font-weight: 600; text-align: right; white-space: nowrap; }}
.va-haken {{ width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; flex: none;
  border: 1px solid currentColor; }}
.va-fortschritt {{ height: 3px; border-radius: 3px; background: var(--va-linie); margin-top: 10px; overflow: hidden; }}
.va-fortschritt i {{ display: block; height: 100%; background: var(--va-akzent); }}
.va-absatz {{ font-size: .88rem; line-height: 1.55; color: var(--va-text); margin: 0; }}
.va-absatz + .va-absatz {{ margin-top: 6px; }}

/* --- Ablauf (Prozessschritte) ---------------------------------------------------- */
.va-ablauf {{ display: flex; align-items: flex-start; margin: 6px 0 2px; }}
.va-schritt {{ flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; position: relative;
  font-size: .72rem; color: var(--va-leise); text-align: center; }}
.va-schritt::before {{ content: ""; position: absolute; top: 10px; left: -50%; width: 100%; height: 2px; background: var(--va-linie); z-index: 0; }}
.va-schritt:first-child::before {{ display: none; }}
.va-schritt--fertig::before, .va-schritt--aktiv::before {{ background: var(--va-akzent); }}
.va-schritt i {{ width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; z-index: 1;
  background: var(--va-karte-2); border: 1.5px solid var(--va-linie-stark); color: var(--va-leise); font-style: normal; }}
.va-schritt--fertig i {{ background: var(--va-akzent); border-color: var(--va-akzent); color: #1a0f0a; }}
.va-schritt--aktiv i {{ border-color: var(--va-akzent); color: var(--va-akzent-text); }}
.va-schritt--fertig, .va-schritt--aktiv {{ color: var(--va-text-2); }}
.va-schritt b {{ font-weight: 600; }}

/* --- Ring ------------------------------------------------------------------------ */
.va-ring {{ position: relative; width: 58px; height: 58px; flex: none; }}
.va-ring span {{ position: absolute; inset: 0; display: grid; place-items: center; font-size: .8rem; font-weight: 650; }}

/* --- Zeilenstatistik ------------------------------------------------------------- */
.va-statistik {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 10px; }}
.va-statistik div b {{ display: block; font-size: 1.25rem; font-weight: 650; letter-spacing: -.01em; }}
.va-statistik div small {{ color: var(--va-leise); font-size: .74rem; }}

/* --- Rangliste mit Balken -------------------------------------------------------- */
.va-rang {{ display: grid; grid-template-columns: 1fr auto; gap: 4px 14px; font-size: .86rem; }}
.va-rang .va-rang-name {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.va-rang .va-rang-name small {{ color: var(--va-leise); margin-left: 6px; }}
.va-rang .va-rang-wert {{ text-align: right; font-weight: 650; }}
.va-rang .va-balken {{ grid-column: 1 / span 2; height: 3px; background: var(--va-linie); border-radius: 3px; margin-bottom: 8px; }}
.va-rang .va-balken i {{ display: block; height: 100%; border-radius: 3px; }}

/* --- Trichter -------------------------------------------------------------------- */
.va-trichter {{ display: flex; flex-direction: column; gap: 8px; }}
.va-stufe {{ display: grid; grid-template-columns: 34px 1fr auto; align-items: center; gap: 12px; padding: 10px 12px;
  border: 1px solid var(--va-linie); border-radius: 12px; background: var(--va-karte-2); }}
.va-stufe-symbol {{ width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center;
  background: var(--va-karte); border: 1px solid var(--va-linie-stark); color: var(--va-text-2); }}
.va-stufe b {{ font-size: .9rem; }}
.va-stufe small {{ display: block; color: var(--va-leise); font-size: .76rem; }}
.va-stufe .va-zahl {{ font-weight: 650; font-size: .95rem; text-align: right; }}
.va-stufe .va-zahl small {{ text-align: right; }}

/* --- Agentenkarten --------------------------------------------------------------- */
.va-agenten {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; }}
.va-agent {{ border: 1px solid var(--va-linie); border-radius: 12px; padding: 12px 13px; background: var(--va-karte-2); }}
.va-agent--aktiv {{ border-color: {rgba(f['akzent'], .55)}; }}
.va-agent-kopf {{ display: flex; align-items: center; gap: 10px; }}
.va-agent-kopf .va-stufe-symbol {{ width: 30px; height: 30px; }}
.va-agent-kopf b {{ font-size: .86rem; letter-spacing: .02em; }}
.va-agent-kopf .va-pill {{ margin-left: auto; font-size: .68rem; padding: 2px 8px; }}
.va-agent p {{ margin: 8px 0 0; font-size: .8rem; color: var(--va-text-2); }}
.va-agent .va-regel {{ color: var(--va-akzent-text); font-weight: 600; }}
.va-agent .va-mono {{ color: var(--va-leise); display: block; margin-top: 6px; }}

/* --- Protokoll (append-only) ----------------------------------------------------- */
.va-log {{ font-family: var(--va-mono); font-size: .78rem; border: 1px solid var(--va-linie); border-radius: 12px;
  background: var(--va-log-flaeche); max-height: 460px; overflow: auto; }}
.va-log-zeile {{ padding: 9px 12px; border-bottom: 1px solid var(--va-log-linie); }}
.va-log-zeile:last-child {{ border-bottom: 0; }}
.va-log-kopf {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.va-log-kopf .va-pill {{ font-size: .66rem; padding: 1px 7px; }}
.va-log-kopf b {{ font-weight: 600; color: var(--va-text); overflow-wrap: anywhere; }}
.va-log-wert {{ margin-left: auto; color: var(--va-text-2); white-space: nowrap; }}
.va-log-zeile em {{ display: block; font-style: normal; color: var(--va-leise); margin: 4px 0 0 72px; overflow-wrap: anywhere; }}

/* --- Tabelle --------------------------------------------------------------------- */
.va-tabelle {{ width: 100%; border-collapse: collapse; font-size: .86rem; }}
.va-tabelle th {{ text-align: right; font-weight: 600; color: var(--va-leise); font-size: .74rem; letter-spacing: .03em;
  padding: 6px 10px; border-bottom: 1px solid var(--va-linie-stark); }}
.va-tabelle td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--va-linie); }}
.va-tabelle th:first-child, .va-tabelle td:first-child {{ text-align: left; padding-left: 0; }}
.va-tabelle tr.va-summe td {{ font-weight: 700; border-bottom: 0; border-top: 1px solid var(--va-linie-stark); }}
.va-tabelle .va-gut {{ color: var(--va-gut); }} .va-tabelle .va-schlecht {{ color: var(--va-schlecht); }}

/* --- Leitfrage und Krümelpfad ---------------------------------------------------- */
.va-leitfrage {{ display: flex; align-items: center; gap: 10px; margin: 4px 0 12px; font-size: .95rem; }}
.va-leitfrage .va-pill {{ font-size: .7rem; }}
.va-leitfrage span:last-child {{ color: var(--va-text-2); }}
.va-pfad {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: .82rem; color: var(--va-leise); margin: 2px 0 10px; }}
.va-pfad b {{ color: var(--va-text); font-weight: 600; }}
.va-szenarien {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
.va-szenario {{ border: 1px solid var(--va-linie); border-radius: 12px; padding: 12px; background: var(--va-karte-2); }}
.va-szenario--base {{ border-color: var(--va-linie-stark); }}
.va-szenario b {{ display: block; font-size: 1.1rem; margin: 4px 0; }}
.va-szenario small {{ color: var(--va-leise); font-size: .74rem; }}
.va-kette {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-family: var(--va-mono); font-size: .76rem; }}
.va-kette span.va-glied {{ padding: 3px 8px; border-radius: 7px; border: 1px solid var(--va-linie-stark); color: var(--va-text-2); }}
.va-kette span.va-glied--nein {{ border-color: {rgba(f['schlecht'], .5)}; color: var(--va-schlecht); text-decoration: line-through; }}
@media (max-width: 760px) {{
  .va-held-zahl {{ font-size: 2rem; }}
  .va-szenarien {{ grid-template-columns: 1fr; }}
  .va-log-zeile em {{ margin-left: 0; }}
}}
</style>"""


# =============================================================================
# Bausteine
# =============================================================================

def pill(text: str, ton: Ton = "neutral", punkt: bool = False) -> str:
    return f'<span class="va-pill va-pill--{ton}">{"<i class=va-punkt></i>" if punkt else ""}{e(text)}</span>'


def ton_fuer(guenstig: bool | None) -> Ton:
    return "neutral" if guenstig is None else ("gut" if guenstig else "schlecht")


def sparkline(werte: Sequence[float], vergleich: Sequence[float] | None = None, breite: int = 108,
              hoehe: int = 34, ton: Ton = "neutral", dehnbar: bool = False,
              beschreibung: str = "Verlauf (Linie) und Vergleich (gepunktet)") -> str:
    """Kleiner Verlauf: Wert als Linie, Vergleich gepunktet (gleiche Skala)."""
    alle = [*werte, *(vergleich or [])]
    if len(werte) < 2 or not alle:
        return ""
    lo, hi = min(alle), max(alle)
    spanne = (hi - lo) or 1.0
    n = max(len(werte), len(vergleich or [])) - 1 or 1

    def punkte(reihe: Sequence[float]) -> str:
        return " ".join(f"{2 + i * (breite - 4) / n:.1f},{hoehe - 3 - (v - lo) / spanne * (hoehe - 6):.1f}"
                        for i, v in enumerate(reihe))

    farbe = ton_farbe(ton)
    letzte = punkte(werte).split(" ")[-1].split(",")
    pl = (f'<polyline points="{punkte(vergleich)}" fill="none" stroke="{FARBEN["text_leise"]}" stroke-width="1" '
          f'stroke-dasharray="2 3"/>') if vergleich else ""
    # dehnbar: auf Kartenbreite gestreckt; Linienstärke bleibt konstant, der Endpunkt entfällt (würde verzerren)
    rahmen = ' preserveAspectRatio="none"' if dehnbar else ""
    strich = ' vector-effect="non-scaling-stroke"' if dehnbar else ""
    punkt = "" if dehnbar else f'<circle cx="{letzte[0]}" cy="{letzte[1]}" r="2.6" fill="{farbe}"/>'
    pl = pl.replace("/>", f"{strich}/>") if pl else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{breite}" height="{hoehe}" viewBox="0 0 {breite} {hoehe}"'
           f'{rahmen}>{pl}<polyline points="{punkte(werte)}" fill="none" stroke="{farbe}" stroke-width="1.6" '
           f'stroke-linejoin="round" stroke-linecap="round"{strich}/>{punkt}</svg>')
    return svg_bild(svg, breite, hoehe, beschreibung)


@dataclass(frozen=True)
class Kpi:
    label: str
    wert: str
    delta: str | None = None
    ton: Ton = "neutral"
    fuss: str = ""
    verlauf: Sequence[float] = ()
    vergleich: Sequence[float] = ()
    hervorheben: bool = False
    verlauf_text: str = "Verlauf (Linie) und Vergleich (gepunktet)"


def kpi_leiste(kpis: Iterable[Kpi]) -> str:
    karten = []
    for k in kpis:
        spark = sparkline(k.verlauf, k.vergleich or None, breite=160, hoehe=26, ton=k.ton, dehnbar=True,
                          beschreibung=k.verlauf_text) if k.verlauf else ""
        karten.append(
            f'<div class="va-kpi{" va-kpi--hervor" if k.hervorheben else ""}">'
            f'<div class="va-kpi-label">{e(k.label)}</div>'
            f'<div class="va-kpi-spark">{spark}</div>'
            f'<div class="va-kpi-wert">{e(k.wert)}</div>'
            f'<div class="va-kpi-fuss">{pill(k.delta, k.ton) if k.delta else ""}<span>{e(k.fuss)}</span></div></div>'
        )
    return f'<div class="va va-leiste">{"".join(karten)}</div>'


def kopfzeile(titel: str, untertitel: str, mitte: str, mitte_unter: str, pills: Iterable[str],
              logo: str = LOGO) -> str:
    return (f'<div class="va va-kopf"><div class="va-marke"><div class="va-logo">{e(logo)}</div>'
            f'<div><b>{e(titel)}</b><small>{e(untertitel)}</small></div></div>'
            f'<div class="va-kopf-mitte">{e(mitte)}<small>{e(mitte_unter)}</small></div>'
            f'<div class="va-kopf-rechts">{"".join(pills)}</div></div>')


def leitfrage(frage: str) -> str:
    return f'<div class="va va-leitfrage">{pill("Leitfrage", "akzent")}<span>{e(frage)}</span></div>'


def karte(inhalt: str, ueberschrift: str | None = None, rechts: str = "", klasse: str = "") -> str:
    kopf = (f'<div class="va-ueberschrift"><span>{e(ueberschrift)}</span><span>{rechts}</span></div>'
            if ueberschrift else "")
    return f'<div class="va va-karte {klasse}">{kopf}{inhalt}</div>'


def held(label: str, zahl: str, einheit: str, pills: Iterable[str], botschaft: str, unter: str) -> str:
    return (f'<div class="va-ueberschrift"><span>{e(label)}</span><span></span></div>'
            f'<div class="va-held"><div class="va-held-zahl">{e(zahl)}<small>{e(einheit)}</small></div>'
            f'<div class="va-held-meta">{"".join(pills)}</div></div>'
            f'<div style="margin-top:16px"><p class="va-botschaft">{e(botschaft)}</p><p class="va-unter">{e(unter)}</p></div>')


def checkliste(eintraege: Iterable[tuple[str, str, Ton]], fortschritt: float | None = None) -> str:
    zeilen = []
    for text, wert, ton in eintraege:
        farbe = ton_farbe(ton)
        zeichen = "kreuz" if ton == "schlecht" else ("warnung" if ton == "warnung" else "check")
        zeilen.append(f'<li><span class="va-haken" style="color:{farbe}">{symbol(zeichen, 11, farbe, 2.6)}</span>'
                      f'<span>{e(text)}</span><span class="va-wert" style="color:{farbe}">{e(wert)}</span></li>')
    balken = (f'<div class="va-fortschritt"><i style="width:{max(0.0, min(fortschritt, 1.0)) * 100:.0f}%"></i></div>'
              if fortschritt is not None else "")
    return f'<ul class="va-liste">{"".join(zeilen)}</ul>{balken}'


def ablauf(schritte: Iterable[tuple[str, Literal["fertig", "aktiv", "offen"]]]) -> str:
    teile = []
    for i, (name, zustand) in enumerate(schritte, start=1):
        inhalt = symbol("check", 12, "#1A0F0A", 3) if zustand == "fertig" else str(i)
        teile.append(f'<div class="va-schritt va-schritt--{zustand}"><i>{inhalt}</i><b>{e(name)}</b></div>')
    return f'<div class="va-ablauf">{"".join(teile)}</div>'


def ring(anteil: float, text: str, ton: Ton = "akzent") -> str:
    umfang = 2 * 3.14159 * 24
    farbe = FARBEN["akzent"] if ton == "akzent" else ton_farbe(ton)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="58" height="58" viewBox="0 0 58 58">'
           f'<circle cx="29" cy="29" r="24" fill="none" stroke="{FARBEN["linie"]}" stroke-width="4"/>'
           f'<circle cx="29" cy="29" r="24" fill="none" stroke="{farbe}" stroke-width="4" stroke-linecap="round" '
           f'stroke-dasharray="{umfang * max(0.0, min(anteil, 1.0)):.1f} {umfang:.1f}" transform="rotate(-90 29 29)"/>'
           '</svg>')
    return f'<div class="va-ring">{svg_bild(svg, 58, 58)}<span>{e(text)}</span></div>'


def statistik(eintraege: Iterable[tuple[str, str, Ton]]) -> str:
    teile = "".join(f'<div><b style="color:{ton_farbe(t) if t not in ("neutral",) else FARBEN["text"]}">{e(w)}</b>'
                    f'<small>{e(label)}</small></div>' for w, label, t in eintraege)
    return f'<div class="va-statistik">{teile}</div>'


def rangliste(eintraege: Iterable[tuple[str, str, str, float, Ton]]) -> str:
    """Einträge: (Name, Zusatz, Wert-Text, Anteil 0..1, Ton)."""
    teile = []
    for name, zusatz, wert, anteil, ton in eintraege:
        farbe = {"gut": FARBEN["gut"], "schlecht": FARBEN["schlecht"]}.get(ton, FARBEN["text_leise"])
        teile.append(f'<div class="va-rang-name">{e(name)}<small>{e(zusatz)}</small></div>'
                     f'<div class="va-rang-wert" style="color:{ton_farbe(ton)}">{e(wert)}</div>'
                     f'<div class="va-balken"><i style="width:{max(2.0, anteil * 100):.1f}%;background:{farbe}"></i></div>')
    return f'<div class="va-rang">{"".join(teile)}</div>'


def trichter(stufen: Iterable[tuple[str, str, str, str, str]]) -> str:
    """Stufen: (Symbol, Name, Beschreibung, Zahl, Zahl-Zusatz)."""
    teile = "".join(
        f'<div class="va-stufe"><div class="va-stufe-symbol">{symbol(s, 17)}</div>'
        f'<div><b>{e(name)}</b><small>{e(text)}</small></div>'
        f'<div class="va-zahl">{e(zahl)}<small>{e(zusatz)}</small></div></div>'
        for s, name, text, zahl, zusatz in stufen
    )
    return f'<div class="va-trichter">{teile}</div>'


@dataclass(frozen=True)
class Agentenkarte:
    symbol: str
    name: str
    status: str
    aufgabe: str
    regel: str
    stand: str
    ton: Ton = "neutral"
    aktiv: bool = False


def agentenkarten(karten: Iterable[Agentenkarte]) -> str:
    teile = "".join(
        f'<div class="va-agent{" va-agent--aktiv" if k.aktiv else ""}"><div class="va-agent-kopf">'
        f'<div class="va-stufe-symbol">{symbol(k.symbol, 16)}</div><div><b>{e(k.name)}</b></div>'
        f'{pill(k.status, k.ton)}</div><p>{e(k.aufgabe)}</p><p class="va-regel">{e(k.regel)}</p>'
        f'<span class="va-mono">{e(k.stand)}</span></div>'
        for k in karten
    )
    return f'<div class="va-agenten">{teile}</div>'


@dataclass(frozen=True)
class Logeintrag:
    zeit: str
    art: str
    ton: Ton
    objekt: str
    grund: str
    wert: str = ""


def protokoll(eintraege: Sequence[Logeintrag]) -> str:
    if not eintraege:
        return '<div class="va-log"><div class="va-log-zeile"><em>Noch keine Ereignisse.</em></div></div>'
    zeilen = "".join(
        f'<div class="va-log-zeile"><div class="va-log-kopf"><span class="va-leise">{e(z.zeit)}</span>'
        f'{pill(z.art, z.ton)}<b>{e(z.objekt)}</b><span class="va-log-wert">{e(z.wert)}</span></div>'
        f'<em>{e(z.grund)}</em></div>'
        for z in eintraege
    )
    return f'<div class="va-log">{zeilen}</div>'


def tabelle(kopf: Sequence[str], zeilen: Sequence[Sequence[tuple[str, Ton | None]]],
            summenzeilen: Iterable[int] = ()) -> str:
    summen = set(summenzeilen)
    th = "".join(f"<th>{e(k)}</th>" for k in kopf)
    tr = []
    for i, zeile in enumerate(zeilen):
        zellen = "".join(
            f'<td class="{"va-" + ("gut" if t == "gut" else "schlecht") if t in ("gut", "schlecht") else ""}">{e(w)}</td>'
            for w, t in zeile)
        tr.append(f'<tr class="{"va-summe" if i in summen else ""}">{zellen}</tr>')
    return f'<table class="va-tabelle"><thead><tr>{th}</tr></thead><tbody>{"".join(tr)}</tbody></table>'


def kette(glieder: Iterable[tuple[str, bool]]) -> str:
    """Tool-Kette: erlaubte Aufrufe normal, abgelehnte durchgestrichen."""
    teile = []
    for i, (name, erlaubt) in enumerate(glieder):
        if i:
            teile.append('<span class="va-leise">→</span>')
        teile.append(f'<span class="va-glied{"" if erlaubt else " va-glied--nein"}">{e(name)}</span>')
    return f'<div class="va-kette">{"".join(teile)}</div>'


def pfad(teile: Sequence[str]) -> str:
    inhalt = '<span class="va-leise">›</span>'.join(
        f"<b>{e(t)}</b>" if i == len(teile) - 1 else f"<span>{e(t)}</span>" for i, t in enumerate(teile))
    return f'<div class="va va-pfad">{inhalt}</div>'


def szenarien(eintraege: Iterable[tuple[str, str, str, str]]) -> str:
    """(Name, Wert, Anpassung, Annahme)."""
    teile = "".join(
        f'<div class="va-szenario{" va-szenario--base" if n == "Base" else ""}"><small>{e(n)} · {e(a)}</small>'
        f'<b>{e(w)}</b><small>{e(t)}</small></div>'
        for n, w, a, t in eintraege
    )
    return f'<div class="va va-szenarien">{teile}</div>'


# =============================================================================
# Ergänzung FlowAudit-AI (ohne Änderung der übernommenen Bausteine)
# =============================================================================

URTEIL_TON: dict[str, Ton] = {"FREIGABE": "gut", "KLAERUNG": "warnung", "SPERRE": "schlecht", "ABGELEHNT": "schlecht"}
URTEIL_TEXT = {"FREIGABE": "Freigabe", "KLAERUNG": "Klärung", "SPERRE": "Sperre", "ABGELEHNT": "Abgewiesen"}


def urteil_pill(urteil: str) -> str:
    """Urteil immer als Wort mit Farbe (nie Farbe allein)."""
    return pill(URTEIL_TEXT.get(urteil, urteil), URTEIL_TON.get(urteil, "neutral"), punkt=True)


def mische(vorne: str, hinten: str, anteil: float) -> str:
    """Deckende Mischfarbe (Hex) – für Umgebungen ohne Alpha-Kanal, z. B. Mermaid-``classDef``."""
    a, b = vorne.lstrip("#"), hinten.lstrip("#")
    kanal = [round(int(a[i:i + 2], 16) * anteil + int(b[i:i + 2], 16) * (1 - anteil)) for i in (0, 2, 4)]
    return "#" + "".join(f"{k:02X}" for k in kanal)


def zusatz_stylesheet() -> str:
    """Kleine Ergänzungen (nur CSS-Variablen der Suite, keine eigenen Farben)."""
    return """<style>
.va-tabelle--text th, .va-tabelle--text td { text-align: left; vertical-align: top; padding: 8px 14px 8px 0; }
.va-tabelle--text td { color: var(--va-text-2); }
.va-tabelle--text td:first-child { color: var(--va-text); font-weight: 600; white-space: nowrap; }
.va-saetze { margin: 0; padding-left: 1.1rem; font-size: .88rem; line-height: 1.55; color: var(--va-text); }
.va-saetze li + li { margin-top: 4px; }
.va-zitat { font-family: var(--va-mono); font-size: .78rem; color: var(--va-text-2); background: var(--va-log-flaeche);
  border: 1px solid var(--va-linie); border-radius: 10px; padding: 10px 12px; overflow-wrap: anywhere; margin: 0; }
</style>"""


def texttabelle(kopf: Sequence[str], zeilen: Sequence[Sequence[str]]) -> str:
    """Linksbündige Texttabelle (z. B. Aufbau der App); alle Zellen maskiert."""
    th = "".join(f"<th>{e(k)}</th>" for k in kopf)
    tr = "".join("<tr>" + "".join(f"<td>{e(z)}</td>" for z in zeile) + "</tr>" for zeile in zeilen)
    return f'<table class="va-tabelle va-tabelle--text"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def saetze(liste: Iterable[str]) -> str:
    return f'<ul class="va-saetze">{"".join(f"<li>{e(s)}</li>" for s in liste)}</ul>'


def zitat(text: str) -> str:
    """Unvertrauenswürdiger Rohtext (z. B. Freitext BT-22) – maskiert und als Daten gekennzeichnet."""
    return f'<p class="va-zitat">{e(text)}</p>'
