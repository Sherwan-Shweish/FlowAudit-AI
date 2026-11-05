"""Konfiguration der Desktop-Schicht aus ``desktop/app.toml`` und plattformgerechte Ordner."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DESKTOP_ORDNER = Path(__file__).resolve().parent
PROJEKT_ORDNER = DESKTOP_ORDNER.parent


@dataclass(frozen=True)
class AppKonfiguration:
    name: str
    untertitel: str
    bundle_id: str
    version: str
    einstieg: str
    logo: str = "Δ"                      # Glyphe der Lade-/Fehlerseite (wie im Kopf der App)
    breite: int = 1440
    hoehe: int = 900
    min_breite: int = 1024
    min_hoehe: int = 700
    timeout_sekunden: int = 90
    thema_modul: str | None = None
    geheimnisse: tuple[str, ...] = field(default_factory=tuple)
    projekt: Path = PROJEKT_ORDNER

    @property
    def einstieg_pfad(self) -> Path:
        return self.projekt / self.einstieg

    @property
    def datenordner(self) -> Path:
        """Benutzerdaten (Einstellungen, Sperrdatei) – nie im Projekt- oder Git-Ordner."""
        if basis := os.getenv("AGENT_DATENORDNER"):
            return Path(basis) / self.name
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / self.name
        return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / self.name

    @property
    def logordner(self) -> Path:
        if basis := os.getenv("AGENT_DATENORDNER"):
            return Path(basis) / self.name / "logs"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Logs" / self.name
        return self.datenordner / "logs"


def lade_konfiguration(pfad: Path | None = None) -> AppKonfiguration:
    daten = tomllib.loads((pfad or DESKTOP_ORDNER / "app.toml").read_text(encoding="utf-8"))
    app, fenster, start = daten["app"], daten.get("fenster", {}), daten.get("start", {})
    return AppKonfiguration(
        name=app["name"], untertitel=app.get("untertitel", ""), bundle_id=app["bundle_id"],
        version=str(app.get("version", "1.0.0")), einstieg=app.get("einstieg", "app.py"),
        logo=str(app.get("logo", "Δ"))[:2] or "Δ",
        breite=int(fenster.get("breite", 1440)), hoehe=int(fenster.get("hoehe", 900)),
        min_breite=int(fenster.get("min_breite", 1024)), min_hoehe=int(fenster.get("min_hoehe", 700)),
        timeout_sekunden=int(start.get("timeout_sekunden", 90)), thema_modul=start.get("thema_modul") or None,
        geheimnisse=tuple(daten.get("geheimnisse", {}).get("schluessel", [])),
    )
