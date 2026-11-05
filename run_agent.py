#!/usr/bin/env python3
"""
FlowAudit-AI · Kommandozeile
============================

    python run_agent.py alle                          # alle 15 Musterdokumente prüfen (Soll/Ist-Vergleich)
    python run_agent.py pruefe data/invoices/03_preisabweichung_ubl.xml
    python run_agent.py pruefe data/invoices/14_zugferd_pdf_mengenabweichung.pdf --diagramm epk
    python run_agent.py pruefe <datei> --freigeben "Max Mustermann"   # Aktionsentwürfe freigeben (HITL)
    python run_agent.py kpi                           # Prozess-KPIs nach einem Batchlauf
    python run_agent.py export                        # Excel (mit Gegenprüfung) + Power BI + PDF + PowerPoint
    python run_agent.py klaerfaelle                   # Klärfälle mit Grund, Zuständigkeit und Vier-Augen-Pflicht
    python run_agent.py zahlung                       # Zahlungsvorschlag mit Skonto (gesichert/gefährdet)
    python run_agent.py sql "SELECT IBAN FROM LFBK"   # SQL-Guardrail direkt testen
    python run_agent.py frage "Wie viele Rechnungen sind gesperrt?"   # benötigt LLM-API-Key
    python run_agent.py init                          # Demo-Daten neu erzeugen
    python run_agent.py diagnose                      # Python-Umgebung prüfen (Pakete, Versionen)

Ohne API-Key läuft alles im Offline-Modus (regelbasierter Prüfvermerk).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJEKT))

from src.agent import (  # noqa: E402
    Einstellungen,
    FlowAuditAgent,
    KonfigurationsFehler,
    ReadOnlySQLGuard,
    SQLGuardrailVerletzung,
    erstelle_llm_client,
    fmt_de,
)
from src.assistent import KIAssistent, Vorgang  # noqa: E402
from src.bpmn_tracker import kpi_bericht  # noqa: E402
from src.demo import (  # noqa: E402
    batchlauf,
    einstellungen_fuer,
    protokolliere_freigabe,
    stelle_demodaten_sicher,
)

FARBE = {"FREIGABE": "\033[32m", "KLAERUNG": "\033[33m", "SPERRE": "\033[31m", "ABGELEHNT": "\033[31m"}
RESET, FETT = "\033[0m", "\033[1m"


def _f(text: str, urteil: str, farbe: bool) -> str:
    return f"{FARBE.get(urteil, '')}{FETT}{text}{RESET}" if farbe else text


def _absatz(text: str, einzug: str = "  ") -> str:
    return "\n".join(textwrap.fill(z, 100, initial_indent=einzug, subsequent_indent=einzug) if z else ""
                     for z in text.splitlines())


def zeige_vorgang(v: Vorgang, farbe: bool, diagramm: str | None) -> None:
    print(f"\n{FETT}═══ {v.datei} ═══{RESET}" if farbe else f"\n=== {v.datei} ===")
    if v.abgelehnt:
        print(_f(f"ABGELEHNT ({v.ablehnungstyp})", "ABGELEHNT", farbe))
        print(_absatz(v.ablehnungsgrund or ""))
    else:
        r = v.fakten.rechnung
        print(_f(f"{v.urteil.value}  →  {v.schritt.value}", v.urteil.value, farbe))
        print(f"  Rechnung {r.rechnungsnummer} · {r.lieferant_name} · {fmt_de(r.summe_brutto or Decimal(0))} EUR brutto"
              f" · {v.rechnung.container or r.format}")
        print("  BPMN-Pfad: " + " → ".join(s.value for s in v.pfad))
        print("\nBegründung:")
        for s in v.begruendung:
            print(_absatz("• " + s, "  "))
        auffaellig = v.fakten.auffaellige_pruefungen
        if auffaellig:
            print("\nPrüfprotokoll (Auffälligkeiten):")
            for p in auffaellig:
                print(f"  [{p.status.value:7}] {p.code.value:28} {p.referenz or ''}")
        if v.sap and v.sap.positionen:
            print("\nSAP-Abgleich (über Read-Only-SQL-Guardrail):")
            print(f"  {'EBELP':>5} {'MATNR':10} {'Bestellt':>9} {'WE':>7} {'berechnet':>9} {'Rechnung':>9} {'Preis PO':>9} {'Preis RE':>9}  Status")
            for p in v.sap.positionen:
                print(f"  {p.ebelp:>5} {p.matnr or '':10} {p.bestellmenge:>9} {p.we_menge:>7} {p.bereits_berechnet:>9} "
                      f"{str(p.rechnungsmenge or '-'):>9} {fmt_de(p.bestellpreis):>9} "
                      f"{fmt_de(p.rechnungspreis) if p.rechnungspreis is not None else '-':>9}  {p.status}")
        a = v.audit
        print(f"\nKI-Prüfvermerk ({a.quelle}, Modell {a.modell}, Faktencheck "
              f"{'bestanden' if a.faktencheck.bestanden else 'NICHT bestanden'}, SHA-256 {a.eingabe_hash[:12]}…):")
        print(_absatz(a.bericht.audit_vermerk))
        print("  Nächster Schritt: " + a.bericht.naechster_schritt)
    for akt in v.aktionen:
        print(f"\n--- Aktionsentwurf [{akt.status}] {akt.typ} · Priorität {akt.prioritaet} ---")
        print(_absatz(akt.als_text()))
    if diagramm and not v.abgelehnt:
        print(f"\nMermaid-Diagramm ({diagramm.upper()}) – z. B. in https://mermaid.live einfügen:\n")
        print(v.diagramm(diagramm))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_agent.py", description="FlowAudit-AI – E-Rechnungs- & P2P-Audit-Agent",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--provider", default=None,
                    help="offline | claude | gemini (Standard: LLM_ANBIETER bzw. LLM_PROVIDER aus .env)")
    ap.add_argument("--toleranz", default="2.0", help="Preistoleranz in Prozent (Standard 2.0)")
    ap.add_argument("--keine-farben", action="store_true")
    sub = ap.add_subparsers(dest="befehl", required=True)
    p = sub.add_parser("pruefe", help="eine XML- oder ZUGFeRD-PDF-Rechnung prüfen")
    p.add_argument("datei", type=Path)
    p.add_argument("--diagramm", choices=["bpmn", "epk"])
    p.add_argument("--json", action="store_true", help="vollständiges Ergebnis als JSON ausgeben")
    p.add_argument("--freigeben", metavar="NAME", help="alle Aktionsentwürfe im Namen dieser Person freigeben")
    sub.add_parser("alle", help="alle Musterdokumente prüfen")
    sub.add_parser("kpi", help="Prozess-KPIs nach Batchlauf")
    e = sub.add_parser("export", help="Excel, Power BI, PDF und PowerPoint (nach Prüflauf aller Musterdokumente)")
    e.add_argument("--ziel", type=Path, default=PROJEKT / "exports")
    sub.add_parser("klaerfaelle", help="Klärfälle des Prüflaufs (Grund, Zuständigkeit, Vier-Augen)")
    sub.add_parser("zahlung", help="Zahlungsvorschlag nach Skontofrist und Fälligkeit")
    s = sub.add_parser("sql", help="SQL-Abfrage durch den Read-Only-Guardrail schicken")
    s.add_argument("abfrage")
    f = sub.add_parser("frage", help="Frage in natürlicher Sprache (benötigt LLM)")
    f.add_argument("text")
    sub.add_parser("init", help="Demo-Daten neu erzeugen")
    sub.add_parser("diagnose", help="Python-Umgebung prüfen (Pakete importierbar, Mindestversionen)")
    a = ap.parse_args(argv)
    farbe = sys.stdout.isatty() and not a.keine_farben

    if a.befehl == "diagnose":
        from src.diagnose import bericht, pruefe_umgebung

        print(bericht())
        return 0 if all(s.ok for s in pruefe_umgebung()) else 1

    if a.befehl == "init":
        d = stelle_demodaten_sicher(neu=True)
        print(f"Demo-Daten erzeugt: {d.db_pfad} · {len(d.manifest)} Dokumente · Stichtag {d.stichtag:%d.%m.%Y}")
        return 0

    demo = stelle_demodaten_sicher()
    einst = einstellungen_fuer(a.provider or Einstellungen.aus_umgebung().llm_provider, a.toleranz)
    try:
        erstelle_llm_client(einst)
    except KonfigurationsFehler as exc:
        print(f"Hinweis: {exc} → Offline-Modus wird verwendet.", file=sys.stderr)
        einst = einstellungen_fuer("offline", a.toleranz)

    if a.befehl == "pruefe":
        if not a.datei.exists():
            print(f"Datei nicht gefunden: {a.datei}", file=sys.stderr)
            return 2
        v = KIAssistent(demo.db_pfad, einst, stichtag=demo.stichtag).pruefe(a.datei)
        if a.freigeben:
            v.aktionen = [akt.freigeben(a.freigeben, "Freigabe über CLI") for akt in v.aktionen]
            for akt in v.aktionen:
                protokolliere_freigabe(akt, v.datei)
        if a.json:
            print(v.model_dump_json(indent=2, exclude={"rechnung"}))
        else:
            zeige_vorgang(v, farbe, a.diagramm)
        return 0

    if a.befehl == "alle":
        assistent = KIAssistent(demo.db_pfad, einst, stichtag=demo.stichtag)
        print(f"{'Datei':40} {'Rechnung':14} {'Ergebnis':32} {'Soll':4} Aktionen")
        fehler = 0
        for name, eintrag in demo.manifest.items():
            v = assistent.pruefe(demo.ordner / name)
            ist = v.schritt.value if v.schritt else "ABGELEHNT"
            ok = ist == eintrag["erwartete_route"]
            fehler += not ok
            nr = v.fakten.rechnung.rechnungsnummer if v.fakten else "–"
            print(f"{name:40} {nr:14} {_f(f'{ist:32}', v.urteil.value if v.urteil else 'ABGELEHNT', farbe)} "
                  f"{'✓' if ok else '✗':4} {', '.join(x.typ for x in v.aktionen)}")
        print(f"\n{len(demo.manifest) - fehler}/{len(demo.manifest)} Dokumente entsprechen der erwarteten Route.")
        return 1 if fehler else 0

    if a.befehl == "kpi":
        b = batchlauf(demo, Decimal(a.toleranz))
        print("Synthetische Demo-Daten · Batchlauf auf einer Kopie der Datenbank\n")
        print(kpi_bericht(b.kpis))
        st = b.statistik
        print(f"Security: {st['injektionen_erkannt']} Prompt-Injection(s) erkannt, {st['injektionen_wirksam']} wirksam · "
              f"{st['abgewiesen']} Datei(en) abgewiesen · Dubletten verhindert: {fmt_de(st['dubletten_verhindert_eur'])} EUR")
        return 0

    if a.befehl == "export":
        from src.bericht import erstelle_pdf, erstelle_pptx
        from src.exporter import erstelle_excel_bericht, erstelle_powerbi_paket, pruefe_gegenpruefung, pruefe_hashkette
        from src.pruefstand import erstelle_pruefstand

        p = erstelle_pruefstand(demo, einstellungen_fuer("offline", a.toleranz))
        a.ziel.mkdir(parents=True, exist_ok=True)
        xlsx = a.ziel / "flowaudit_pruefbericht.xlsx"
        xlsx.write_bytes(erstelle_excel_bericht(p.batch.db_kopie, demo.stichtag, pruefstand=p, werte_cachen=True))
        pbi = a.ziel / "flowaudit_powerbi.zip"
        pbi.write_bytes(erstelle_powerbi_paket(p.batch.db_kopie, demo.stichtag, pruefstand=p))
        (a.ziel / "flowaudit_pruefbericht.pdf").write_bytes(erstelle_pdf(p, p.klaerfaelle))
        (a.ziel / "flowaudit_pruefbericht.pptx").write_bytes(erstelle_pptx(p, p.klaerfaelle))
        intakt, _ = pruefe_hashkette(xlsx)
        gegen = pruefe_gegenpruefung(xlsx)["gesamtstatus"]
        print(f"Excel-Prüfbericht: {xlsx}  (IKS-Hash-Kette {'intakt' if intakt else 'DEFEKT'} · Gegenprüfung "
              f"{gegen or 'beim Öffnen in Excel (LibreOffice fehlt)'})")
        print(f"Power-BI-Paket:    {pbi}")
        print(f"Prüfbericht:       {a.ziel / 'flowaudit_pruefbericht.pdf'} und .pptx (nur freigegebene Klärungen – "
              f"im CLI-Lauf {sum(f.status == 'gelöst' for f in p.klaerfaelle.values())})")
        return 0

    if a.befehl in ("klaerfaelle", "zahlung"):
        from src.pruefstand import erstelle_pruefstand

        p = erstelle_pruefstand(demo, einstellungen_fuer("offline", a.toleranz))
        if a.befehl == "klaerfaelle":
            print(f"{'Rechnung':16} {'Brutto':>12} {'Status':11} {'4-Augen':8} Grund · zuständig")
            for f in p.klaerfaelle.values():
                print(f"{f.rechnungsnummer[:16]:16} {fmt_de(f.brutto):>12} {f.status:11} "
                      f"{'ja' if f.vier_augen else '–':8} {f.grund} · {f.zustaendig}")
            print(f"\n{len(p.klaerfaelle)} Klärfälle · Vier-Augen ab {fmt_de(p.vier_augen_grenze, 0)} € brutto")
            return 0
        z = p.zahlungsvorschlag()
        print(f"{'Rechnung':16} {'Art':10} {'Zahltag':10} {'Skonto':>10} {'Zahlbetrag':>12}  Begründung")
        for x in z.positionen:
            print(f"{x.rechnungsnummer[:16]:16} {x.zustand:10} {x.zahltag.strftime('%d.%m.%Y') if x.zahltag else '–':10} "
                  f"{fmt_de(x.skonto_betrag):>10} {fmt_de(x.zahlbetrag):>12}  {x.begruendung}")
        print(f"\nZahlbetrag {fmt_de(z.zahlbetrag)} € · Skonto gesichert {fmt_de(z.skonto_gesichert)} € · "
              f"gefährdet {fmt_de(z.skonto_gefaehrdet)} € · verloren {fmt_de(z.skonto_verloren)} €")
        return 0

    if a.befehl == "sql":
        guard = ReadOnlySQLGuard(demo.db_pfad)
        try:
            zeilen, abgeschnitten = guard.ausfuehren(a.abfrage)
        except SQLGuardrailVerletzung as v:
            print(_f(f"BLOCKIERT – Schicht {v.schicht}: {v.grund}", "SPERRE", farbe))
            return 3
        print(_f(f"ERLAUBT – {len(zeilen)} Zeile(n){' (gekürzt)' if abgeschnitten else ''}", "FREIGABE", farbe))
        for z in zeilen[:20]:
            print("  " + json.dumps(z, ensure_ascii=False, default=str))
        return 0

    if a.befehl == "frage":
        agent = FlowAuditAgent(einstellungen=einst, sql_guard=ReadOnlySQLGuard(demo.db_pfad))
        e = agent.beantworte_frage(a.text)
        if e.blockiert:
            print(_f(f"BLOCKIERT: {e.blockiergrund}", "SPERRE", farbe))
            return 3
        print(f"SQL: {e.sql}\n\n{e.antwort}  ({e.quelle})")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
