"""Benutzereinstellungen der App (z. B. hell/dunkel), gespeichert als JSON im Datenordner.

Bewusst ohne Streamlit-Abhängigkeit: Launcher und App lesen dieselbe Datei.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from desktop.konfiguration import AppKonfiguration, lade_konfiguration

logger = logging.getLogger(__name__)
STANDARD: dict[str, Any] = {"darstellung": "dunkel"}
ERLAUBT = {"darstellung": ("dunkel", "hell")}


def _datei(konfig: AppKonfiguration | None = None) -> Path:
    return (konfig or lade_konfiguration()).datenordner / "einstellungen.json"


def lade(konfig: AppKonfiguration | None = None) -> dict[str, Any]:
    werte = dict(STANDARD)
    try:
        gelesen = json.loads(_datei(konfig).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return werte
    except (OSError, ValueError) as fehler:  # beschädigte Datei: Standard verwenden, nicht abstürzen
        logger.warning("Einstellungen nicht lesbar (%s) – Standardwerte.", fehler)
        return werte
    for schluessel, wert in gelesen.items():
        if schluessel in ERLAUBT and wert in ERLAUBT[schluessel]:
            werte[schluessel] = wert
    return werte


def speichere(aenderungen: dict[str, Any], konfig: AppKonfiguration | None = None) -> dict[str, Any]:
    for schluessel, wert in aenderungen.items():
        if schluessel not in ERLAUBT or wert not in ERLAUBT[schluessel]:
            raise ValueError(f"Unzulässige Einstellung: {schluessel}={wert!r}")
    werte = {**lade(konfig), **aenderungen}
    datei = _datei(konfig)
    datei.parent.mkdir(parents=True, exist_ok=True)
    # atomar schreiben: erst Temp-Datei, dann umbenennen
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=datei.parent, delete=False, suffix=".tmp") as tmp:
        json.dump(werte, tmp, ensure_ascii=False, indent=2)
    os.replace(tmp.name, datei)
    return werte
