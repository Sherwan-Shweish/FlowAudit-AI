"""Umgebungsdiagnose: Sind alle Pakete importierbar und in passender Version?

Aufruf: ``python run_agent.py diagnose`` (oder ``python -m src.diagnose``).
Häufigste Ursache für Importfehler ist eine gemischte Umgebung, z. B. ein altes
``pyarrow``, das gegen NumPy 1.x kompiliert wurde, neben NumPy 2.x.
"""

from __future__ import annotations

import importlib
import platform
from dataclasses import dataclass
from importlib import metadata

# (Importname, Paketname auf PyPI, Mindestversion laut requirements.txt bzw. Streamlit)
PAKETE: tuple[tuple[str, str, str], ...] = (
    ("streamlit", "streamlit", "1.50"),
    ("altair", "altair", "5.4"),
    ("pyarrow", "pyarrow", "16.0"),   # ältere Builds sind gegen NumPy 1.x kompiliert
    ("pandas", "pandas", "2.2"),
    ("numpy", "numpy", "1.26"),
    ("pydantic", "pydantic", "2.7"),
    ("defusedxml", "defusedxml", "0.7"),  # XXE-Schutz beim Einlesen der E-Rechnungen
    ("pypdf", "pypdf", "5.0"),            # ZUGFeRD: XML aus PDF/A-3
    ("fpdf", "fpdf2", "2.8"),
    ("openpyxl", "openpyxl", "3.1"),
    ("PIL", "pillow", "10.0"),
    ("dotenv", "python-dotenv", "1.0"),
)


@dataclass(frozen=True)
class Paketstatus:
    paket: str
    version: str | None
    mindestens: str
    fehler: str | None

    @property
    def ok(self) -> bool:
        return self.fehler is None and self.version is not None and _version(self.version) >= _version(self.mindestens)


def _version(text: str) -> tuple[int, ...]:
    teile = []
    for teil in text.split(".")[:3]:
        ziffern = "".join(z for z in teil if z.isdigit())
        teile.append(int(ziffern) if ziffern else 0)
    return tuple(teile)


def pruefe_umgebung() -> list[Paketstatus]:
    ergebnis = []
    for modul, paket, mindestens in PAKETE:
        try:
            version = metadata.version(paket)
        except metadata.PackageNotFoundError:
            version = None
        try:
            importlib.import_module(modul)
            fehler = None
        except Exception as ausnahme:  # noqa: BLE001 – jede Importausnahme ist ein Befund
            fehler = f"{type(ausnahme).__name__}: {str(ausnahme).splitlines()[0][:160]}"
        ergebnis.append(Paketstatus(paket, version, mindestens, fehler))
    return ergebnis


def bericht() -> str:
    zeilen = [f"Python {platform.python_version()} · {platform.system()} {platform.machine()}", ""]
    for s in pruefe_umgebung():
        status = "OK " if s.ok else "!! "
        hinweis = s.fehler or ("" if s.ok else f"Version zu alt (mind. {s.mindestens})")
        zeilen.append(f"{status}{s.paket:<14} {s.version or 'fehlt':<10} {hinweis}")
    if not all(s.ok for s in pruefe_umgebung()):
        zeilen += ["", "Lösung (Umgebung aus uv.lock neu herstellen):",
                   "  uv sync",
                   "oder ohne uv – frische virtuelle Umgebung:",
                   "  python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate",
                   "  pip install -U pip && pip install -r requirements.txt"]
    return "\n".join(zeilen)


if __name__ == "__main__":
    print(bericht())
