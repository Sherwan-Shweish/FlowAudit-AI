"""Erzeugt das App-Symbol von FlowAudit-AI im Stil der Suite (Kupferfläche, dunkle Glyphen, ein heller Akzent).

Motiv: 3-Way-Matching – Bestellung (umrandet) = Wareneingang (gefüllt) = Rechnung (hell, mit Prüfhaken).
Ausgabe: desktop/ressourcen/icon.png (1024 px), desktop/ressourcen/AppIcon.icns, static/favicon.png (128 px).

Aufruf (einmalig, nicht Teil der App):  uv run --with playwright python tools/erzeuge_symbol.py
"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

PROJEKT = Path(__file__).resolve().parents[1]

D, L = "#1E120C", "#FCF4EC"
W, GAP, Y0, Y1, X0 = 186, 58, 262, 742, 170
ECKE = 62

def dokument(x, art):
    ein = 9 if art == "umriss" else 0          # Umriss nach innen versetzt: gleiche Außenkante wie die Flächen
    x0, y0, x1, y1 = x + ein, Y0 + ein, x + W - ein, Y1 - ein
    e = ECKE - ein
    p = (f"M{x0+14},{y0} H{x1-e} L{x1},{y0+e} V{y1-14} Q{x1},{y1} {x1-14},{y1} "
         f"H{x0+14} Q{x0},{y1} {x0},{y1-14} V{y0+14} Q{x0},{y0} {x0+14},{y0} Z")
    lasche = f"M{x1-e},{y0} V{y0+e-12} Q{x1-e},{y0+e} {x1-e+12},{y0+e} H{x1} Z"
    if art == "umriss":
        k = f'<path d="{p}" fill="none" stroke="{D}" stroke-width="18" stroke-linejoin="round"/>'
        k += f'<path d="{lasche}" fill="none" stroke="{D}" stroke-width="14" stroke-linejoin="round"/>'
        linie = D
    elif art == "dunkel":
        k = f'<path d="{p}" fill="{D}"/><path d="{lasche}" fill="#5C3826"/>'
        linie = "#B07658"
    else:
        k = f'<path d="{p}" fill="{L}"/><path d="{lasche}" fill="#E6CDBB"/>'
        linie = "#D7B7A3"
    for i, breite in enumerate((0.62, 0.48, 0.56)):
        y = Y0 + 112 + i * 46
        k += (f'<line x1="{x+38}" y1="{y}" x2="{x+38+(W-76)*breite/0.62}" y2="{y}" stroke="{linie}" '
              f'stroke-width="16" stroke-linecap="round"/>')
    return k

teile = []
xs = [X0 + i * (W + GAP) for i in range(3)]
teile.append(dokument(xs[0], "umriss"))
teile.append(dokument(xs[1], "dunkel"))
teile.append(dokument(xs[2], "hell"))
# Gleichheitszeichen zwischen den Belegen: Bestellung = Wareneingang = Rechnung
for x in (xs[0] + W, xs[1] + W):
    m = x + GAP / 2
    for dy in (-15, 15):
        teile.append(f'<line x1="{m-15}" y1="{560+dy}" x2="{m+15}" y2="{560+dy}" stroke="{D}" stroke-width="13" stroke-linecap="round"/>')
# Prüfhaken auf der Rechnung
cx = xs[2] + W / 2
teile.append(f'<path d="M{cx-52},{606} L{cx-14},{646} L{cx+56},{564}" fill="none" stroke="{D}" stroke-width="30" '
             'stroke-linecap="round" stroke-linejoin="round"/>')
# Grundlinie wie im VarianceAI-Symbol
teile.append(f'<line x1="176" y1="768" x2="848" y2="768" stroke="{D}" stroke-width="14" stroke-linecap="round"/>')

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#EEAA82"/><stop offset="1" stop-color="#C2663E"/></linearGradient>
<filter id="s" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="16" stdDeviation="20" flood-color="#000" flood-opacity="0.32"/></filter></defs>
<rect x="100" y="100" width="824" height="824" rx="186" fill="url(#g)" filter="url(#s)"/>
{"".join(teile)}
</svg>'''
ziel = PROJEKT / "desktop" / "ressourcen"
(ziel / "icon.svg").write_text(svg, encoding="utf-8")
with sync_playwright() as p:
    browser = p.chromium.launch()
    seite = browser.new_page(viewport={"width": 1024, "height": 1024})
    seite.set_content(f'<html><body style="margin:0;background:transparent">{svg}</body></html>')
    seite.screenshot(path=str(ziel / "icon.png"), omit_background=True,
                     clip={"x": 0, "y": 0, "width": 1024, "height": 1024})
    browser.close()
bild = Image.open(ziel / "icon.png").convert("RGBA")
bild.save(ziel / "AppIcon.icns", format="ICNS")
bild.resize((128, 128), Image.LANCZOS).save(PROJEKT / "static" / "favicon.png", optimize=True)
print("Symbol erzeugt:", ziel)
