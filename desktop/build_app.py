"""Erzeugt eine macOS-App (``<Name>.app``) für den Agenten – Agenten-Standard v1.

Die App ist bewusst ein **Starter, kein eingefrorener Code**: Sie startet
``uv run python -m desktop.launcher`` im Projektordner. Jede Änderung am Python-Code
ist damit beim nächsten Öffnen aktiv – ohne die App neu zu bauen. Neu bauen ist nur
nötig, wenn der Projektordner verschoben wird oder sich ``desktop/app.toml`` ändert.

Aufruf (auf dem Mac, im Projektordner):

    uv run python -m desktop.build_app                 # → ~/Applications/<Name>.app
    uv run python -m desktop.build_app --ziel ~/Desktop
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from desktop.konfiguration import DESKTOP_ORDNER, AppKonfiguration, lade_konfiguration

RESSOURCEN = DESKTOP_ORDNER / "ressourcen"
UV_KANDIDATEN = ("$HOME/.local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv")


def info_plist(konfig: AppKonfiguration) -> dict:
    return {
        "CFBundleName": konfig.name,
        "CFBundleDisplayName": konfig.name,
        "CFBundleIdentifier": konfig.bundle_id,
        "CFBundleVersion": konfig.version,
        "CFBundleShortVersionString": konfig.version,
        "CFBundleExecutable": konfig.name,
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.business",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": f"{konfig.name} – lokal, synthetische Daten",
    }


def startskript(konfig: AppKonfiguration) -> str:
    """Bash-Starter: findet uv (Apps erben nicht den PATH der Shell) und startet den Launcher."""
    kandidaten = " ".join(f'"{k}"' for k in UV_KANDIDATEN)
    projekt = str(konfig.projekt).replace('"', '\\"')
    return f"""#!/bin/bash
# Erzeugt von desktop/build_app.py (Agenten-Standard v1) – nicht von Hand bearbeiten.
PROJEKT="{projekt}"
NAME="{konfig.name}"
LOG="$HOME/Library/Logs/$NAME"
mkdir -p "$LOG"

meldung() {{ /usr/bin/osascript -e "display alert \\"$NAME\\" message \\"$1\\"" >/dev/null 2>&1; }}

UV=""
for k in {kandidaten}; do
  if [ -x "$k" ]; then UV="$k"; break; fi
done
[ -z "$UV" ] && UV="$(command -v uv)"
if [ -z "$UV" ]; then
  meldung "uv wurde nicht gefunden. Installation im Terminal: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
if [ ! -f "$PROJEKT/desktop/app.toml" ]; then
  meldung "Projektordner nicht gefunden: $PROJEKT – bitte die App mit desktop.build_app neu erzeugen."
  exit 1
fi
cd "$PROJEKT" || exit 1
exec "$UV" run --project "$PROJEKT" python -m desktop.launcher >>"$LOG/launcher.log" 2>&1
"""


def erzeuge_icns(ziel: Path) -> Path:
    """Nutzt das mitgelieferte AppIcon.icns; fehlt es, wird es aus icon.png erzeugt."""
    vorhanden = RESSOURCEN / "AppIcon.icns"
    if vorhanden.exists():
        shutil.copy2(vorhanden, ziel)
        return ziel
    from PIL import Image  # noqa: PLC0415

    Image.open(RESSOURCEN / "icon.png").convert("RGBA").save(ziel, format="ICNS")
    return ziel


def baue_app(konfig: AppKonfiguration, zielordner: Path) -> Path:
    app = zielordner / f"{konfig.name}.app"
    if app.exists():
        shutil.rmtree(app)
    inhalt = app / "Contents"
    (inhalt / "MacOS").mkdir(parents=True)
    (inhalt / "Resources").mkdir()
    with (inhalt / "Info.plist").open("wb") as datei:
        plistlib.dump(info_plist(konfig), datei)
    starter = inhalt / "MacOS" / konfig.name
    starter.write_text(startskript(konfig), encoding="utf-8")
    starter.chmod(starter.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    erzeuge_icns(inhalt / "Resources" / "AppIcon.icns")
    (inhalt / "PkgInfo").write_text("APPL????", encoding="ascii")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="macOS-App für diesen Agenten erzeugen")
    parser.add_argument("--ziel", type=Path, default=Path.home() / "Applications",
                        help="Zielordner (Standard: ~/Applications, ohne Administratorrechte)")
    parser.add_argument("--ohne-sync", action="store_true", help="Abhängigkeiten nicht vorab mit uv installieren")
    args = parser.parse_args(argv)

    konfig = lade_konfiguration()
    if not args.ohne_sync and shutil.which("uv"):
        print("Abhängigkeiten installieren (uv sync) …", flush=True)
        subprocess.run(["uv", "sync"], cwd=konfig.projekt, check=True)
    args.ziel.expanduser().mkdir(parents=True, exist_ok=True)
    app = baue_app(konfig, args.ziel.expanduser())
    if sys.platform == "darwin":
        subprocess.run(["touch", str(app)], check=False)  # Finder/Dock aktualisieren das Symbol
    print(f"Fertig: {app}\nStart per Doppelklick, Spotlight („{konfig.name}“) oder ins Dock ziehen.")
    if os.environ.get("TERM_PROGRAM") and sys.platform == "darwin":
        print(f"Direkt öffnen: open \"{app}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
