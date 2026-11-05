"""
Prüft die XML-Musterrechnungen gegen die offiziellen Regelwerke
================================================================

* **EN 16931** – Schematron-Regeln des CEN (ConnectingEurope/eInvoicing-EN16931),
  vorkompiliert als XSLT, für UBL und CII,
* **XRechnung-CIUS** – Schematron der KoSIT (itplr-kosit/xrechnung-schematron),
  zur Laufzeit mit der ISO-Schematron-Referenzimplementierung kompiliert,
* **CII-XSD** – UN/CEFACT D16B (aus dem CEN-Repository).

Dieses Werkzeug ist für die Entwicklung gedacht und nicht Teil der Laufzeit.

Vorbereitung (einmalig)::

    pip install saxonche xmlschema
    git clone --depth 1 https://github.com/ConnectingEurope/eInvoicing-EN16931 vendor/en16931
    git clone --depth 1 https://github.com/itplr-kosit/xrechnung-schematron vendor/xrechnung-schematron
    git clone --depth 1 https://github.com/Schematron/schematron vendor/iso-schematron

Aufruf::

    python tools/offizielle_validierung.py data/invoices --vendor vendor \
        --markdown docs/validierung_tabelle.md --json docs/validierung.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

try:
    import xmlschema
    from saxonche import PySaxonProcessor
except ImportError:  # pragma: no cover
    sys.exit("Bitte zuerst installieren: pip install saxonche xmlschema")

SVRL_FEHLER = re.compile(r"<svrl:failed-assert\b([^>]*)>", re.S)


def _fehlschlaege(svrl: str) -> list[tuple[str, str]]:
    ergebnis = []
    for attrs in SVRL_FEHLER.findall(svrl):
        regel = re.search(r'\bid="([^"]+)"', attrs)
        flag = re.search(r'\bflag="([^"]+)"', attrs)
        ergebnis.append((regel[1] if regel else "?", flag[1] if flag else "fatal"))
    return ergebnis


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ordner", type=Path)
    ap.add_argument("--vendor", type=Path, default=Path("vendor"))
    ap.add_argument("--markdown", type=Path, help="Ergebnis zusätzlich als Markdown-Tabelle speichern")
    ap.add_argument("--json", type=Path, help="Ergebnis zusätzlich als JSON speichern (wird vom Cockpit angezeigt)")
    a = ap.parse_args()

    en = a.vendor / "en16931"
    xr = a.vendor / "xrechnung-schematron" / "src" / "validation" / "schematron"
    iso = a.vendor / "iso-schematron" / "trunk" / "schematron" / "code"
    cii_xsd = next((en / "cii" / "schema").rglob("uncoupled clm/CII/uncefact/data/standard/CrossIndustryInvoice_100pD16B.xsd"))

    zeilen = []
    with PySaxonProcessor(license=False) as proc, tempfile.TemporaryDirectory() as tmp:
        xslt = proc.new_xslt30_processor()
        cen = {
            "UBL": xslt.compile_stylesheet(stylesheet_file=str(en / "ubl/xslt/EN16931-UBL-validation.xslt")),
            "CII": xslt.compile_stylesheet(stylesheet_file=str(en / "cii/xslt/EN16931-CII-validation.xslt")),
        }
        schritte = [xslt.compile_stylesheet(stylesheet_file=str(iso / n))
                    for n in ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl", "iso_svrl_for_xslt2.xsl")]
        kosit = {}
        for syntax in ("UBL", "CII"):
            dok = proc.parse_xml(xml_file_name=str(xr / syntax.lower() / f"XRechnung-{syntax}-validation.sch"))
            for s in schritte[:-1]:
                dok = proc.parse_xml(xml_text=s.transform_to_string(xdm_node=dok))
            ziel = Path(tmp) / f"xr-{syntax}.xsl"
            ziel.write_text(schritte[-1].transform_to_string(xdm_node=dok), encoding="utf-8")
            kosit[syntax] = xslt.compile_stylesheet(stylesheet_file=str(ziel))
        schema = xmlschema.XMLSchema(str(cii_xsd))

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.zugferd_pdf import KeineEingebetteteRechnung, extrahiere_xml

        for datei in sorted(p for p in a.ordner.iterdir() if p.suffix.lower() in {".xml", ".pdf"}):
            quelle = datei
            if datei.suffix.lower() == ".pdf":
                try:
                    anhang = extrahiere_xml(datei)
                except KeineEingebetteteRechnung:
                    zeilen.append((datei.name, "PDF", "–", [("keine XML", "fatal")], [("keine XML", "fatal")]))
                    continue
                quelle = Path(tmp) / f"{datei.stem}.xml"
                quelle.write_bytes(anhang.xml)
            text = quelle.read_text(encoding="utf-8")
            syntax = "CII" if "CrossIndustryInvoice" in text[:600] else "UBL"
            xsd = "–"
            if syntax == "CII":
                fehler = list(schema.iter_errors(str(quelle)))
                xsd = "✅" if not fehler else f"❌ {fehler[0].reason[:60]}"
            r_cen = _fehlschlaege(cen[syntax].transform_to_string(source_file=str(quelle)))
            r_xr = _fehlschlaege(kosit[syntax].transform_to_string(source_file=str(quelle)))
            anzeige = f"{syntax} (aus PDF)" if datei.suffix.lower() == ".pdf" else syntax
            zeilen.append((datei.name, anzeige, xsd, r_cen, r_xr))

    def fmt(liste):
        if not liste:
            return "✅"
        return ", ".join(f"{'⚠️' if f == 'warning' else '❌'} {r}" for r, f in liste)

    tabelle = ["| Datei | Syntax | CII-XSD | EN 16931 (CEN) | XRechnung (KoSIT) |", "|---|---|---|---|---|"]
    tabelle += [f"| `{n}` | {s} | {x} | {fmt(c)} | {fmt(k)} |" for n, s, x, c, k in zeilen]
    print("\n".join(tabelle))
    if a.markdown:
        a.markdown.parent.mkdir(parents=True, exist_ok=True)
        a.markdown.write_text("\n".join(tabelle) + "\n", encoding="utf-8")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({
            n: {"syntax": s, "cii_xsd": x, "en16931": [list(e) for e in c], "xrechnung": [list(e) for e in k]}
            for n, s, x, c, k in zeilen}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
