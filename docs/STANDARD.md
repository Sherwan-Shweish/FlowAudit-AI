# Agenten-Standard v1

Verbindliche Vorlage für alle KI-Agenten von Sherwan Shweish – vom Portfolio-Projekt bis zum privaten
Familien-Agenten. **VarianceAI ist die Referenzimplementierung.** Abweichungen werden im jeweiligen Auftrag
ausdrücklich genannt; alles andere folgt diesem Dokument.

---

## 1. Grundprinzipien

| Prinzip | Bedeutung |
|---|---|
| **Determinismus vor KI** | Zahlen, Bewertungen, Prüfungen und Entscheidungen rechnet Python. Das LLM formuliert, erklärt und fragt nach – es rechnet nie. |
| **Jede Zahl hat eine Quelle** | KI-Texte werden gegen ein strukturiertes Faktenblatt geprüft (Wert, Einheit, Vorzeichen, Rundung). Fehlschlag → regelbasierter Ersatztext. |
| **Mensch entscheidet** | KI-Ergebnisse sind Entwürfe; berichtsfähig bzw. wirksam erst nach namentlicher Freigabe. |
| **Offline zuerst** | Jeder Agent läuft ohne API-Key mit einem Mock-Client vollständig durch (Demo, Tests, Datenschutz). |
| **Daten sind Daten** | Texte aus Daten (Buchungstexte, Dokumente, Chats) sind unvertrauenswürdig: maskiert für die Oberfläche, bereinigt für das LLM. |
| **Ein Code, zwei Wege** | Dieselbe App läuft im Browser (Demo-Link, Server) und als Mac-App (Desktop-Schicht). |

---

## 2. Technik

| Baustein | Festlegung |
|---|---|
| Sprache | Python ≥ 3.11 (Entwicklung mit 3.12) |
| Umgebung & Pakete | **uv** – `pyproject.toml` + `uv.lock` (eingecheckt); `requirements.txt` synchron für pip |
| Oberfläche | **Streamlit** mit eigenem Designsystem (`src/ui_design.py`) und Altair-Diagrammen (`src/ui_diagramme.py`) |
| Desktop | **pywebview** (macOS: WKWebView) über die generische Schicht `desktop/` |
| Daten lokal | SQLite bzw. Dateien im Datenordner des Benutzers – nie im Git-Repository |
| Schemata | Pydantic v2 für alles, was das LLM sieht oder liefert |
| KI-Anbieter | austauschbarer Client: `mock` · `claude` · `gemini` (Erweiterung: lokales Modell, z. B. Ollama) |
| Qualität | pytest (inkl. Streamlit-`AppTest`), ruff (`F, E9, B, UP, I`), Python 3.11 und 3.13 getestet |

---

## 3. Projektstruktur

```text
<agent>/
├── app.py                    Streamlit-App (Tabs mit Fehlergrenze je Tab)
├── src/
│   ├── agent.py              LLM-Clients, System-Prompt, Schemata, Faktenprüfung, Tool-Whitelist
│   ├── <fachlogik>.py        deterministische Engines (rechnen, prüfen, bewerten)
│   ├── ui_design.py          Farbtoken (dunkel/hell), Stylesheet, HTML-Bausteine (maskiert)
│   ├── ui_diagramme.py       interaktive Diagramme, Farben aus der aktiven Palette
│   └── diagnose.py           Umgebungsprüfung (`python run_agent.py diagnose`)
├── desktop/                  ← unverändert in jeden Agenten kopierbar
│   ├── app.toml              Name, Logo, Bundle-ID, Version, Fenster, Geheimnisse – EINZIGE anzupassende Datei
│   ├── launcher.py           Server starten, Fenster öffnen, sauber beenden
│   ├── server.py             Streamlit mit Wächter (kein Waisenprozess nach Absturz)
│   ├── praeferenzen.py       Benutzereinstellungen (z. B. hell/dunkel) als JSON
│   ├── build_app.py          erzeugt <Name>.app (Starter, kein eingefrorener Code)
│   └── ressourcen/           icon.png (1024 px), AppIcon.icns
├── .streamlit/config.toml    Thema (Startwerte), Schriften, Fehlerdetails
├── static/fonts/             Inter + JetBrains Mono (SIL OFL), offline
├── tests/                    Fachlogik, Invarianten, Agent, UI (AppTest), Desktop
├── docs/                     Methodik, Screenshots, dieser Standard
├── CLAUDE.md                 Arbeitsregeln für KI-gestützte Entwicklung
├── pyproject.toml · uv.lock · requirements.txt · .python-version · .env.example
└── README.md
```

---

## 4. Desktop-Schicht (Stufe 1: pywebview-Starter)

1. `uv run python -m desktop.build_app` erzeugt `~/Applications/<Name>.app` (ohne Administratorrechte).
2. Die App ist ein **Starter**: Sie ruft `uv run python -m desktop.launcher` im Projektordner auf.
   Code-Änderungen sind beim nächsten Öffnen aktiv – kein Neubau nötig.
3. Der Launcher
   * erlaubt nur **eine Instanz** (Sperrdatei),
   * startet Streamlit **nur auf 127.0.0.1** und einem freien Port (nicht im Netzwerk sichtbar),
   * zeigt eine Ladeseite und bei Fehlern die letzten Protokollzeilen,
   * beendet den Server beim Schließen; der Wächter in `desktop/server.py` beendet ihn auch nach einem Absturz.
4. Pfade (macOS): Einstellungen `~/Library/Application Support/<Name>/`, Protokolle `~/Library/Logs/<Name>/`.
   Für Tests: Umgebungsvariable `AGENT_DATENORDNER`.

---

## 5. Darstellung: hell und dunkel

* Umschalter **„Darstellung: Dunkel | Hell“ oben rechts auf der Startseite**; die Wahl wird gespeichert und gilt
  auch beim nächsten Start (der Launcher startet direkt im gewählten Thema – kein Aufflackern).
* Zwei Paletten in `ui_design.PALETTEN`, Streamlit-Thema je Modus in `ui_design.STREAMLIT_THEMA`.
* Farbregeln (beide Modi): Datenfarben nach IBCS; günstig/ungünstig validiert (Kontrast ≥ 3:1, Farbsehschwäche
  ΔE ≥ 8) und immer mit Vorzeichen; Akzentfarbe nur für Bedienung und KI; Textfarben ≥ 4,5:1.

### Laufzeitmodi (`AGENT_LAUFZEIT`)

| Modus | Gesetzt durch | Umschalter hell/dunkel | Einstellungen gespeichert |
|---|---|---|---|
| `desktop` | Launcher automatisch | ja (serverweit – ein Benutzer) | ja |
| `lokal` | manuell: `AGENT_LAUFZEIT=lokal uv run streamlit run app.py` | ja | ja |
| `web` (Standard) | – (z. B. Streamlit Community Cloud) | nein, festes Thema aus `config.toml` | nein |

Begründung: Streamlit-Themenoptionen gelten für den ganzen Server. Im Webbetrieb würde ein Besucher sonst das
Thema aller anderen Besucher ändern.

---

## 6. Sicherheit und Datenschutz

| Regel | Umsetzung |
|---|---|
| Keine Secrets im Repository | `.env` (aus `.env.example`) lokal, oder macOS-Schlüsselbund: `security add-generic-password -s "<Name>" -a "ANTHROPIC_API_KEY" -w` |
| Vorrang der Schlüssel | Umgebungsvariable > `.env` > Schlüsselbund; Werte werden nie protokolliert |
| Oberfläche | jeder dynamische Text über `ui.e()` maskiert; Grafiken als Data-URI (HTML-Bereinigung bleibt aktiv) |
| LLM | nur Whitelist-Tools mit typisierten Parametern, kein freies SQL; Datentexte bereinigt |
| Familien-Agenten | Profile mit Altersstufe; Kinder nur freigegebene Agenten, Inhaltsfilter, Zeitlimit, Protokoll für Eltern; personenbezogene Daten von Kindern standardmäßig nur an lokale Modelle |

---

## 7. Qualität (Definition of Done)

- [ ] `uv run pytest` grün, `uv run ruff check .` sauber, Python 3.11 und 3.13
- [ ] Invarianten der Fachlogik als Tests (z. B. Summe der Effekte = Abweichung, auf den Cent)
- [ ] App-Test: alle Tabs ohne Ausnahme, Fehlergrenze je Tab, Umschalter hell/dunkel
- [ ] Desktop-Test: Server antwortet, endet mit dem Launcher, App-Bundle gültig
- [ ] `python run_agent.py diagnose` zeigt nur „OK“
- [ ] README mit Schnellstart (Browser **und** Mac-App), Screenshots in beiden Modi

---

## 8. Neuen Agenten anlegen – Checkliste

1. Ordner nach Struktur oben anlegen; `desktop/` unverändert übernehmen.
2. `desktop/app.toml`: `name`, `untertitel`, `logo` (Glyphe der Ladeseite), `bundle_id`, `version`, `geheimnisse` anpassen.
3. `desktop/ressourcen/icon.png` (1024 px) ersetzen und `AppIcon.icns` daraus erzeugen.
4. Fachlogik deterministisch in `src/` – erst Tests, dann Oberfläche.
5. `uv sync` → `uv run streamlit run app.py` → `uv run python -m desktop.build_app`.
