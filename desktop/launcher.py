"""Desktop-Launcher (Agenten-Standard v1): startet die Streamlit-App lokal und zeigt sie in einem Mac-Fenster.

Ablauf:
1. Einzelinstanz sicherstellen (Sperrdatei im Datenordner).
2. Freien Port wählen und Streamlit **nur auf 127.0.0.1** starten (nicht im Netzwerk erreichbar).
3. API-Schlüssel aus Umgebung/.env übernehmen, fehlende aus dem macOS-Schlüsselbund ergänzen.
4. Fenster mit Ladeseite öffnen, auf den Health-Check warten, dann die App laden.
5. Beim Schließen des Fensters den Server sauber beenden.

Start: ``uv run python -m desktop.launcher`` (bzw. über die mit ``desktop.build_app`` erzeugte .app).
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import html
import importlib
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import IO

from desktop import praeferenzen
from desktop.konfiguration import AppKonfiguration, lade_konfiguration

logger = logging.getLogger("desktop.launcher")

# Farben der Lade- und Fehlerseite (passend zum Designsystem, ohne Abhängigkeit davon)
_SEITENFARBEN = {
    "dunkel": {"flaeche": "#0E0E0D", "text": "#EDEBE6", "leise": "#8C8A83", "akzent": "#D9825B", "linie": "#2A2926"},
    "hell": {"flaeche": "#F7F6F3", "text": "#1C1B19", "leise": "#66645E", "akzent": "#B85A33", "linie": "#E3E1DB"},
}


# =============================================================================
# Bausteine (einzeln testbar, ohne Fenster)
# =============================================================================


def freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Einzelinstanz:
    """Sperrdatei mit ``fcntl.flock`` – eine zweite Instanz erkennt die laufende sofort."""

    def __init__(self, datei: Path) -> None:
        self.datei = datei
        self._handle: IO[str] | None = None

    def erwerben(self) -> bool:
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        handle = self.datei.open("a+", encoding="utf-8")
        try:
            import fcntl  # noqa: PLC0415 – nur unter macOS/Linux vorhanden

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows: ohne Sperre weiter
            pass
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def freigeben(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.close()
            self._handle = None


def schluessel_aus_schluesselbund(dienst: str, konto: str) -> str | None:
    """Liest ein Geheimnis aus dem macOS-Schlüsselbund (``security``-Werkzeug); sonst ``None``."""
    if sys.platform != "darwin":
        return None
    try:
        ergebnis = subprocess.run(["security", "find-generic-password", "-s", dienst, "-a", konto, "-w"],
                                  capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    wert = ergebnis.stdout.strip()
    return wert or None


def _env_datei(projekt: Path) -> dict[str, str]:
    """Minimaler .env-Leser (KEY=VALUE), damit der Launcher ohne python-dotenv auskommt."""
    werte: dict[str, str] = {}
    datei = projekt / ".env"
    if not datei.exists():
        return werte
    for zeile in datei.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        werte[schluessel.strip()] = wert.strip().strip('"').strip("'")
    return werte


def umgebung(konfig: AppKonfiguration,
             schluesselbund: Callable[[str, str], str | None] = schluessel_aus_schluesselbund) -> dict[str, str]:
    """Umgebung für den Server: Vorrang Umgebungsvariable > .env > Schlüsselbund. Werte werden nie geloggt."""
    env = dict(os.environ)
    datei = _env_datei(konfig.projekt)
    for name in konfig.geheimnisse:
        if env.get(name) or datei.get(name):
            continue
        if wert := schluesselbund(konfig.name, name):
            env[name] = wert
            logger.info("Geheimnis %s aus dem Schlüsselbund übernommen.", name)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["AGENT_LAUFZEIT"] = "desktop"   # schaltet in der App den Umschalter hell/dunkel frei (Einzelbenutzer)
    return env


def thema_argumente(konfig: AppKonfiguration, darstellung: str) -> list[str]:
    """Streamlit-Themenoptionen als CLI-Flags – verhindert ein Aufflackern des falschen Themas beim Start."""
    if not konfig.thema_modul:
        return []
    try:
        thema = importlib.import_module(konfig.thema_modul).STREAMLIT_THEMA[darstellung]
    except (ImportError, AttributeError, KeyError) as fehler:
        logger.warning("Thema nicht ladbar (%s) – Standard aus config.toml.", fehler)
        return []
    return [f"--{schluessel}={wert}" for schluessel, wert in thema.items()]


def streamlit_befehl(konfig: AppKonfiguration, port: int, darstellung: str) -> list[str]:
    return [
        sys.executable, "-m", "desktop.server", "--eltern-pid", str(os.getpid()), "--",
        "run", str(konfig.einstieg_pfad),
        "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
        "--server.fileWatcherType=none", "--browser.gatherUsageStats=false",
        *thema_argumente(konfig, darstellung),
    ]


def warte_auf_server(url: str, prozess: subprocess.Popen | None, timeout: float, intervall: float = 0.25) -> bool:
    """Wartet auf ``/_stcore/health``; bricht ab, wenn der Prozess vorher endet."""
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        if prozess is not None and prozess.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as antwort:  # noqa: S310 – nur localhost
                if antwort.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(intervall)
    return False


def beende_server(prozess: subprocess.Popen | None, wartezeit: float = 5.0) -> None:
    """Beendet den Server samt Kindprozessen (eigene Prozessgruppe)."""
    if prozess is None or prozess.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(prozess.pid, signal.SIGTERM)
        else:
            prozess.terminate()
        prozess.wait(timeout=wartezeit)
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if hasattr(os, "killpg"):
                os.killpg(prozess.pid, signal.SIGKILL)
            else:
                prozess.kill()


def ladeseite(konfig: AppKonfiguration, darstellung: str, meldung: str = "wird gestartet …",
              fehler: str | None = None, logdatei: Path | None = None) -> str:
    f = _SEITENFARBEN.get(darstellung, _SEITENFARBEN["dunkel"])
    inhalt = (f'<p class="f">Der lokale Server ist nicht gestartet.</p><pre>{html.escape(fehler)}</pre>'
              f'<p class="l">Protokoll: {html.escape(str(logdatei))}</p>') if fehler else \
        f'<div class="s"></div><p class="l">{html.escape(meldung)}</p>'
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:{f['flaeche']};color:{f['text']};
font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif}}
main{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}}
.logo{{width:64px;height:64px;border-radius:16px;display:grid;place-items:center;font-size:30px;font-weight:800;
color:#1a0f0a;background:linear-gradient(145deg,#E9A07A,#C96F47)}}
h1{{margin:6px 0 0;font-size:22px;font-weight:650}} .u{{margin:0;color:{f['leise']};font-size:14px}}
.l{{color:{f['leise']};font-size:13px}} .f{{color:#C8382F;font-weight:600}}
pre{{max-width:70vw;white-space:pre-wrap;border:1px solid {f['linie']};border-radius:10px;padding:12px;font-size:12px}}
.s{{width:22px;height:22px;border-radius:50%;border:2px solid {f['linie']};border-top-color:{f['akzent']};
animation:r .9s linear infinite;margin-top:10px}} @keyframes r{{to{{transform:rotate(360deg)}}}}
</style></head><body><main><div class="logo">{html.escape(konfig.logo)}</div><h1>{html.escape(konfig.name)}</h1>
<p class="u">{html.escape(konfig.untertitel)}</p>{inhalt}</main></body></html>"""


def _letzte_zeilen(datei: Path, anzahl: int = 25) -> str:
    try:
        return "\n".join(datei.read_text(encoding="utf-8", errors="replace").splitlines()[-anzahl:])
    except OSError:
        return "(kein Protokoll vorhanden)"


def _mac_identitaet(konfig: AppKonfiguration) -> None:
    """Menüleiste und Dock zeigen App-Name und -Symbol statt „Python“ (nur macOS, PyObjC kommt mit pywebview)."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSImage  # noqa: PLC0415
        from Foundation import NSBundle  # noqa: PLC0415
    except ImportError:
        return
    info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = konfig.name
    symbol = Path(__file__).parent / "ressourcen" / "AppIcon.icns"
    if symbol.exists():
        bild = NSImage.alloc().initWithContentsOfFile_(str(symbol))
        if bild is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(bild)


def _benachrichtigen(titel: str, text: str) -> None:
    if sys.platform == "darwin":
        skript = f'display notification "{text}" with title "{titel}"'
        subprocess.run(["osascript", "-e", skript], check=False, capture_output=True)
    else:
        print(f"{titel}: {text}", file=sys.stderr)


# =============================================================================
# Hauptablauf
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--debug", action="store_true", help="Web-Inspector im Fenster aktivieren")
    parser.add_argument("--nur-server", action="store_true", help="ohne Fenster; URL ausgeben (z. B. für Tests)")
    args = parser.parse_args(argv)

    konfig = lade_konfiguration()
    konfig.logordner.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(konfig.logordner / "launcher.log", encoding="utf-8"),
                                  logging.StreamHandler()])

    sperre = Einzelinstanz(konfig.datenordner / "instanz.lock")
    if not sperre.erwerben():
        _benachrichtigen(konfig.name, f"{konfig.name} ist bereits geöffnet.")
        return 0
    atexit.register(sperre.freigeben)

    darstellung = praeferenzen.lade(konfig)["darstellung"]
    port = freier_port()
    url = f"http://127.0.0.1:{port}"
    serverlog = konfig.logordner / "server.log"
    log = serverlog.open("a", encoding="utf-8")
    logger.info("Starte %s %s auf %s (Darstellung: %s)", konfig.name, konfig.version, url, darstellung)
    prozess = subprocess.Popen(streamlit_befehl(konfig, port, darstellung), cwd=konfig.projekt, env=umgebung(konfig),
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    atexit.register(beende_server, prozess)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))

    if args.nur_server:
        bereit = warte_auf_server(url, prozess, konfig.timeout_sekunden)
        print(url if bereit else "FEHLER", flush=True)
        if bereit:
            with contextlib.suppress(KeyboardInterrupt):
                prozess.wait()
        return 0 if bereit else 1

    import webview  # noqa: PLC0415 – nur für das Fenster nötig

    _mac_identitaet(konfig)
    farbe = _SEITENFARBEN.get(darstellung, _SEITENFARBEN["dunkel"])["flaeche"]
    fenster = webview.create_window(
        f"{konfig.name} – {konfig.untertitel}" if konfig.untertitel else konfig.name,
        html=ladeseite(konfig, darstellung), width=konfig.breite, height=konfig.hoehe,
        min_size=(konfig.min_breite, konfig.min_hoehe), background_color=farbe, text_select=True,
    )

    def laden() -> None:
        if warte_auf_server(url, prozess, konfig.timeout_sekunden):
            fenster.load_url(url)
        else:
            logger.error("Server nicht erreichbar – siehe %s", serverlog)
            fenster.load_html(ladeseite(konfig, darstellung, fehler=_letzte_zeilen(serverlog), logdatei=serverlog))

    try:
        webview.start(laden, debug=args.debug)
    finally:
        beende_server(prozess)
        log.close()
        sperre.freigeben()
    return 0


if __name__ == "__main__":
    sys.exit(main())
