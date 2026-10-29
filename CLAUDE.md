# quizbank – Projektkontext

> Kontextdatei für die Entwicklung (VS Code, ggf. mit Claude Code / Copilot).
> Liegt im Repo-Root. Wird von Claude Code automatisch gelesen, wenn sie `CLAUDE.md` heißt.

## 1. Ziel

Eine selbst gehostete Web-App (Docker), um **Probeklausuren** zu üben, die mit Claude generiert und als **JSON** importiert werden.
Schwerpunkt: Single-/Multiple-Choice mit automatischer Auswertung, echte Klausursimulation (Timer, Fragenanzahl, Bestehensgrenze) und gezieltes Wiederholen von Fehlern.

Die App soll später **öffentlich auf GitHub geteilt** werden. Andere bringen ihre eigenen Fragenbanken mit.

**Vorbild:** [vikas027/quiz-prep](https://github.com/vikas027/quiz-prep) (MIT). Dessen Kernfunktionen werden übernommen (siehe Abschnitt 7), aber mit JSON-Import, Kurs-/Modulstruktur, Klausursimulation mit Bestehensgrenze und Freitext-Case-Studies.

### Kernfunktionen (Muss)
- **Themen-Tags** pro Frage, Filtern nach Thema beim Start eines Quiz
- **Quellenreferenz** pro Frage (optional, z. B. „Folie 2, S. 14“ oder „Lehrbuch Kap. 3“), angezeigt in der Auswertung
- **Erklärungen bei Fehlern**: Erklärung zur richtigen Lösung und (optional) pro Antwortoption, warum sie falsch ist
- **Ergebnis-Historie pro Quiz**: alle Versuche mit Datum, Punkten, bestanden/nicht bestanden, Dauer
- **Falsche Fragen pro Quiz wiederholen**: neuer Versuch nur mit den Fragen, die im letzten (oder in den letzten N) Versuchen falsch waren

## 2. Nicht-Ziele (bewusst weggelassen)

- Keine KI-Korrektur in der App (Freitexte werden mit Musterlösung verglichen oder separat im Claude-Chat bewertet)
- Kein MCP-Server, keine CLI
- Keine Mehrsprachigkeit: UI auf **Deutsch** (Texte zentral halten, damit Übersetzung später möglich bleibt)
- Keine Benutzerverwaltung / kein Login (Single-User, lokal). Wer mehrere Nutzer braucht, startet mehrere Instanzen mit eigenem `./data`
- Kein Einlesen von PDFs – nur strukturiertes JSON

## 3. Nutzungskontext

- **Genau ein Nutzer pro Instanz** (eine SQLite-Datei = ein Nutzer), kein Login, keine Nutzer-Tabelle
- **Beliebig viele Kurse/Fächer**, jeweils mit beliebig vielen Probeklausuren
  - Kurse entstehen automatisch beim Import über das Feld `course` (Abgleich per Name) oder manuell in der UI
  - Kurse in der UI umbenennen, löschen (inkl. Klausuren und Versuche, mit Bestätigung) und Defaults pflegen (`default_time_limit`, `default_pass_mark`)
  - Startseite = Kursliste; skaliert auch bei vielen Fächern (Suche/Sortierung nach Name bzw. zuletzt geübt)
- Beispielkurs „Einführung in die BWL“: 60 Single-Choice-Aufgaben, 120 Minuten, 5 Optionen (A–E), genau 1 richtig, 1 Punkt pro Aufgabe, kein Punktabzug, Bestehensgrenze 30 Punkte, Themen = 8 Perspektiven
- Ein Modul hat zusätzlich **Case Studies** mit Freitext → in der App nur: Antwort eingeben, danach Musterlösung + Bewertungskriterien anzeigen, Punkte selbst vergeben

## 4. Tech-Stack

| Bereich | Wahl | Begründung |
|---|---|---|
| Backend | Python 3.12+, **FastAPI** | einfach, gute Doku, automatische API-Doku unter `/docs` |
| Datenbank | **SQLite** via **SQLModel** | eine Datei, kein extra Service |
| Frontend | **Jinja2-Templates** für normale Seiten, **Alpine.js** für den Quiz-Ablauf (eine Seite, Fragen als JSON, Antworten per `fetch` gespeichert) | kein Build-Schritt; Zustand (Timer, Navigation, Tastatur, Feedback) an einem Ort; Fortsetzen abgebrochener Versuche ohne Zusatzaufwand. Kein HTMX. |
| Styling | **Eigenes CSS-Designsystem** in `app/static/app.css` (Tokens mit `light-dark()`, Hell/Dunkel über `data-theme`), Schriften **Atkinson Hyperlegible Next/Mono** lokal in `app/static/vendor/fonts/`, Lucide-Symbole als Sprite `app/static/icons.svg` | eigene Identität („Antwortbogen“), keine Konflikte mit Framework-Defaults, offline-fähig |
| Diagramme | CSS-Balken und SVG-Linie aus Jinja-Macros | einfache Statistik ohne Build und ohne Bibliothek |
| Inhalte | **Markdown** (markdown-it-py + Sanitizing via nh3) in Frage, Szenario, Optionen, Erklärungen; **KaTeX** (lokal) für `$…$`/`$$…$$` | generisch für alle Fächer (Code, Tabellen, Formeln) |
| Validierung | Pydantic-Modelle für das Import-JSON | klare Fehlermeldungen beim Import |
| Migrationen | **Alembic** ab Start | Updates brechen keine Nutzer-DBs |
| Paketverwaltung | **uv** (`pyproject.toml` + `uv.lock`) | schnell, reproduzierbar, schlanker Docker-Build |
| Tests | pytest + httpx TestClient | |
| Lint/Format | ruff | |
| Deployment | Dockerfile + docker-compose, SQLite auf Volume `./data`, Fragen-Ordner `./questions` gemountet | |

### Generik-Prinzipien
- Nichts kursspezifisch im Code: Optionsanzahl variabel (**2–10**), Tastenkürzel passen sich an (1–9/A–J)
- **Auto-Import**: alle `*.json` in `QUESTIONS_DIR` (Default `./questions`) werden beim Start importiert (idempotent); abschaltbar über `AUTO_IMPORT=false`
- UI-Texte in `app/locales/de.py` (Dict mit Keys); Sprache über Env `APP_LANG` (Default `de`), damit `en.py` später einfach ergänzt werden kann

### Befehle
```bash
uv sync --locked                          # install deps incl. dev
uv run uvicorn app.main:app --reload      # dev server on :8000
uv run pytest                             # tests
uv run python -m app.schemas              # regenerate schema/exam.schema.json (test fails if stale)
uv run ruff check . && uv run ruff format --check .   # what CI runs
docker compose up --build                 # container; binds 127.0.0.1 only (no login)
```
- Docker uses `/data` and `/questions` (env set in Dockerfile); locally the defaults are `./data`, `./questions`
- `[tool.uv] package = false`: app is not installed as a package; pytest finds it via `pythonpath = ["."]`

## 5. Import-Format (JSON)

Eine Datei = eine Probeklausur. Schema wird als Pydantic-Modell implementiert und als `schema/exam.schema.json` exportiert (für Claude und für andere Nutzer).

```json
{
  "format_version": 1,
  "course": "Einführung in die BWL",
  "title": "Probeklausur 1",
  "time_limit_minutes": 120,
  "pass_mark": 30,
  "questions": [
    {
      "id": "ebwl-p1-001",
      "type": "single",
      "topic": "Strategische Perspektive",
      "tags": ["Five Forces", "Porter"],
      "question": "Welche Aussage …?",
      "options": ["…", "…", "…", "…", "…"],
      "correct": [2],
      "points": 1,
      "explanation": "Warum C richtig ist …",
      "option_explanations": ["A falsch, weil …", "…", "…", "…", "…"],
      "source": "Foliensatz 2, Folie 14"
    },
    {
      "id": "mod3-p1-010",
      "type": "open",
      "topic": "Case Study Expansion",
      "scenario": "Ausgangslage …",
      "question": "Analysieren Sie …",
      "points": 10,
      "model_answer": "…",
      "rubric": [
        { "criterion": "Nennt Risiko X und Y", "points": 4 },
        { "criterion": "Leitet Empfehlung ab", "points": 6 }
      ]
    }
  ]
}
```

**Regeln:**
- `type`: `single` | `multiple` | `open`
- `topic`: Hauptthema (Pflicht), dient für Filter und Statistik
- `tags`: zusätzliche Schlagwörter (optional), ebenfalls filterbar
- `source`: freie Quellenreferenz (optional)
- `correct`: 0-basierte Indizes; bei `single` genau ein Eintrag
- `option_explanations`, `explanation`, `scenario`, `rubric`: optional
- `pass_mark`, `time_limit_minutes`: optional (Fallback: Kurs-Einstellung)
- Import ist **idempotent** über `id` (gleiche ID → Frage wird aktualisiert, nicht dupliziert)
- Klausur-Identität = `course` (Abgleich mit `Course.name`) + `title`. **Dateien gewinnen** gegenüber der UI: die Datei ist maßgeblich, Fragen, die nicht mehr in ihr stehen, werden gelöscht (Versuche behalten ihre Snapshots, siehe §6)
- Browser-Uploads landen nur in der DB, nicht in `QUESTIONS_DIR`. Beim Löschen einer Klausur in der UI erscheint die Warnung „kommt beim Neustart zurück" nur, wenn `QUESTIONS_DIR/source_file` tatsächlich existiert
- Validierungsfehler werden pro Frage mit Pfad angezeigt (z. B. `questions[4].correct: index 5 out of range`)

## 6. Datenmodell (SQLModel)

- `Course` (id, name, display_name, default_time_limit, default_pass_mark)
  - `name` = Import-Schlüssel, unveränderlich für importierte Kurse; die UI benennt nur `display_name` um (Fallback `name`). Suche/Sortierung nach dem angezeigten Namen. Klausurtitel haben keinen Anzeigenamen
- `Exam` (id, course_id, title, time_limit_minutes, pass_mark, multiple_scoring, source_file, imported_at)
- `Question` (id [intern, int], external_id [aus JSON, eindeutig pro Exam], exam_id, position, type, topic, tags [JSON], source, question, scenario, options [JSON], correct [JSON], points, explanation, option_explanations [JSON], model_answer, rubric [JSON], starred, disabled, in_wrong_pool, pool_streak)
  - `starred`, `disabled`, `in_wrong_pool`, `pool_streak` = lokaler Lernstand; der Import fasst sie nicht an
- `Attempt` (id, exam_id, mode [`exam` | `practice` | `retry_wrong`], feedback [`instant` | `end`], filter [JSON: Themen/Tags/Anzahl], pass_mark, time_limit_minutes, multiple_scoring, deadline, started_at, finished_at, duration_seconds, score, max_score, passed)
  - `pass_mark`, `time_limit_minutes`, `multiple_scoring` = effektive Werte, beim Start eingefroren
  - Historie pro Quiz = alle `Attempt`s eines `Exam`
- `Answer` (id, attempt_id, question_id [nullable, `ON DELETE SET NULL`], position, snapshot [JSON], selected [JSON], text_answer, rubric_scores [JSON], self_score, revealed, is_correct)
  - **Eine Zeile pro Frage im Versuch**, angelegt beim Start (unbeantwortet = `selected` ist `None`)
  - `snapshot` = eingefrorene Frage (type, topic, tags, source, scenario, question, options, correct, points, explanations, model_answer, rubric), **bereits in Anzeigereihenfolge** (Optionen gemischt, `correct`/`option_explanations` mitgemappt). `selected` bezieht sich immer auf den Snapshot. Auswertung und Statistik lesen nur den Snapshot, Re-Importe ändern die Historie nie
  - `question_id` = Verweis auf die Live-Frage (Stern, Pool, Wiederholen); `None`, wenn die Frage aus der Datei entfernt wurde
  - `self_score` = Summe von `rubric_scores` (ohne Raster direkt eingegeben)

### Versuchsablauf
- **Klausurmodus**: immer **alle** Fragen (ignoriert `disabled`), `feedback = end`. `disabled` und Filter gelten nur für Übung und Wiederholen
- **Timer**: Server-Deadline `deadline = started_at + limit`, Uhrzeit läuft weiter, auch während die Seite geschlossen ist. Der Client bekommt die Restsekunden vom Server. Abgelaufene Versuche werden **lazy** abgeschlossen (`finalize_expired`, beim Lesen von Versuchen und bei jedem Schreibzugriff), mit `finished_at = deadline`. Unbeantwortete Fragen zählen 0. Ohne Klausurmodus kein Countdown, nur verstrichene Zeit
- **Übung**: Themen und Tags filtern jeweils mit ODER (keine Auswahl = alle), beide zusammen mit UND; deaktivierte Fragen fallen weg; „Anzahl" zieht eine Zufallsstichprobe (ohne Mischen in Dateireihenfolge). Kein Countdown, keine Bestehensgrenze, nur verstrichene Zeit. Mischen von Fragen/Optionen geht in beiden Modi
- **Sofort-Feedback**: „Prüfen"-Button pro Frage (auch für `multiple`/`open`), erst danach „Weiter"; die Einstellungen des Start-Dialogs liegen in `Attempt.filter`, „Verwerfen und neu starten" übernimmt sie
- **Höchstens ein unfertiger Versuch pro Exam**: „Fortsetzen" oder „Verwerfen und neu starten" (Verwerfen löscht den Versuch)
- **Lösungen erst dann zum Client, wenn sie gezeigt werden dürfen**: die Start-Daten enthalten nur Frage, Optionen, Szenario, Thema, Punkte und das aktuelle `selected`. Sofort-Feedback läuft über einen `reveal`-Endpunkt (setzt `revealed`, die Antwort ist danach gesperrt), sonst über die Auswertungsseite nach der Abgabe
- **Antwort speichern**: `PUT /api/attempts/{id}/answers/{answer_id}` mit `{selected}` oder `{text_answer}`; **409**, wenn der Versuch beendet ist, die Deadline überschritten ist (löst den Abschluss aus) oder die Antwort `revealed` ist. Prüft die Indizes und bei `single` genau einen Eintrag
- `score`/`max_score`/`passed` werden beim Abschluss gespeichert und nur bei einer Selbstbewertung aus dem Snapshot neu berechnet

### Falsch-Pool (pro Exam)
- **Rein**: Fragen, die in einem **abgeschlossenen Versuch beliebigen Modus** falsch, teilweise richtig oder unbeantwortet waren (`open`: Selbstbewertung unter voller Punktzahl). `pool_streak` wird auf 0 gesetzt. Aktualisiert wird beim Abschluss (auch lazy), nie während des Versuchs; verworfene Versuche ändern nichts. `open`-Fragen zählen bei ihrer **ersten** Selbstbewertung, spätere Korrekturen ändern den Pool nicht
- **Raus**: nach **2 richtigen Antworten in Folge** (in jedem Modus) oder manuell („aus Pool entfernen"). Reine Funktion `update_pool(in_pool, streak, is_correct)` in `scoring.py`, Schwelle als Konstante
- **Wiederholen** (`retry_wrong`): der ganze Pool ohne deaktivierte Fragen, optional eine zufällige Stichprobe. Die Exam-Seite zeigt die Pool-Größe und eine Pool-Liste; bei leerem Pool ist der Button ausgegraut
- Statistik pro Thema: **Snapshot-Thema** abgeschlossener Versuche aller Modi; `open` erst nach Selbstbewertung; teilweise richtig zählt als falsch

### Zurücksetzen und Export
- „Lernstand zurücksetzen" (Sterne, Deaktivierungen, Pool, Streaks; pro Exam und global) und „Verlauf löschen" (pro Exam, lässt den Pool unberührt) sind getrennte Aktionen mit eigener Bestätigung
- Export = exakt `format_version: 1` aus den Live-Fragen in `position`-Reihenfolge (Roundtrip ohne Änderungen beim Re-Import), ohne Lernstand

## 7. Features nach Phasen

### Phase 1 – MVP (Ziel: in 1–2 Sessions nutzbar)
- [x] Projektgerüst, Docker, Healthcheck
- [x] JSON-Upload im Browser + Validierung + Import (Duplikaterkennung über `id`)
- [x] Übersicht: Kurse → Probeklausuren (Anzahl Fragen, letztes Ergebnis)
- [x] Attempts-PR: komplettes Schema aus §6 in einer Migration; nur Klausurmodus mit allen Fragen und Auswertung am Ende, Countdown mit Auto-Abgabe (falls Limit gesetzt), Fortsetzen/Verwerfen, Antworten per JSON-`PUT` aus Alpine. `open` nur Textfeld + Musterlösung/Raster in der Auswertung, `passed` bleibt `None` bis Phase 3
- [x] Quiz durchführen (eine Frage pro Seite, Navigation vor/zurück)
- [x] Auswertung am Ende: Punkte, richtig/falsch, **Erklärung zur richtigen Lösung**, Erklärung pro Option, **Quelle**
- [x] **Ergebnis-Historie pro Quiz** (Versuche speichern und auflisten)

### Phase 2 – Lernfunktionen (Funktionsumfang wie quiz-prep)
- [x] Start-Dialog auf der Exam-Seite: Klausur oder Übung, **Filter nach Thema/Tags**, Fragenanzahl (Übung ohne Countdown, siehe §6)
- [x] Zwei Feedback-Modi: **Sofort-Feedback** nach jeder Frage („Prüfen"-Button) oder **Auswertung erst am Ende**
- [x] **Falsch-Pool pro Quiz** wiederholen (siehe §6), Pool-Liste auf der Exam-Seite (Wiederholen bietet Feedback-Modus, Anzahl und Mischen, keine Themenfilter)
- [x] Fragen **markieren** (Stern) und **deaktivieren** (im Quiz, in der Auswertung und in der Fragenliste der Exam-Seite); Übung: „nur markierte" (deaktivierte fallen immer weg); „Lernstand zurücksetzen" pro Exam und global (Kursliste), „Verlauf löschen" pro Exam
- [x] Fragen und Antwortoptionen zufällig mischen
- [x] **Export** eines Quiz zurück nach JSON (Button auf der Exam-Seite; `course` = Import-Name, eigene statt Kurs-Defaults)
- [x] **Tastatursteuerung** (1–9 bzw. A–J je nach Optionsanzahl zum Auswählen, Enter = Hauptbutton (Prüfen/Weiter/Abgeben), ←/→ navigieren, S = markieren; Hinweis passt sich der Frage an)
- [x] Hell/Dunkel-Modus (Umschalter in der Navigation: automatisch → hell → dunkel, im Browser gespeichert)

### Phase 2b – Klausursimulation & Statistik (eigene Ergänzung)
- [x] Klausurmodus: Countdown mit Auto-Abgabe, keine Lösungen bis zur Abgabe, „bestanden/nicht bestanden“ anhand `pass_mark`
- [x] Statistik pro Thema (Trefferquote über alle Versuche), Verlauf als einfaches Diagramm (Exam-Seite: CSS-Balken + SVG-Linie, ohne Chart.js)

### Phase 3 – Case Studies
- [x] Fragetyp `open`: Textfeld, nach Abgabe Musterlösung + Raster, Selbstbewertung pro Kriterium (auf der Auswertungsseite; berechnet Punkte und `passed` neu)
- [x] Export einer Antwort als Text (zum Einfügen in den Claude-Chat): „Für Claude kopieren" mit Aufgabe, Raster, Musterlösung und eigener Antwort

### Phase 4 – Teilen
- [x] README (Englisch) mit Screenshots (`docs/screenshots/`), Quickstart, Format-Doku
- [x] Generierungs-Prompt als `docs/generate-exam-prompt.md` (EN + DE)
- [x] Beispiel-Fragenbank mit **neutralen, selbst geschriebenen** Fragen in `examples/` (`sample-exam.json` DE, `web-basics.json` EN; Tests validieren alle Dateien)
- [x] GitHub Actions: ruff + pytest; Docker-Image auf GHCR veröffentlichen (bei Tag `v*`)
- [x] Lizenz: MIT

## 8. Projektstruktur (Ziel)

```
quizbank/
├── CLAUDE.md
├── README.md
├── LICENSE
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml         # uv, ruff, pytest
├── uv.lock
├── .python-version
├── .gitignore / .dockerignore
├── .pre-commit-config.yaml
├── .github/
│   ├── workflows/ci.yml       # ruff + pytest
│   ├── workflows/docker.yml   # Image bauen; bei Tag v* → GHCR (amd64+arm64)
│   └── dependabot.yml
├── alembic.ini
├── migrations/            # Alembic-Migrationen (nicht `alembic/`, sonst Namenskonflikt mit dem Paket)
├── app/
│   ├── main.py            # FastAPI-App, Router einbinden
│   ├── db.py              # Engine, Session, init
│   ├── models.py          # SQLModel-Tabellen
│   ├── schemas.py         # Pydantic-Importformat
│   ├── importer.py        # JSON → DB
│   ├── scoring.py         # Auswertungslogik (rein, gut testbar)
│   ├── locales/de.py      # UI-Texte
│   ├── routes/
│   │   ├── courses.py
│   │   ├── exams.py
│   │   └── attempts.py
│   ├── templates/         # Jinja2
│   └── static/            # app.css, icons.svg, shell.js (Theme, Menüs, Formular-Helfer), quiz.js (Alpine), vendor/ (Alpine, fonts/)
├── schema/exam.schema.json
├── examples/sample-exam.json
├── docs/generate-exam-prompt.md
├── data/                  # SQLite (gitignored)
├── questions/             # eigene Fragenbanken (gitignored!)
└── tests/
```

## 9. Konventionen

- **UI-Texte auf Deutsch**, zentral in `app/locales/de.py`, nicht verstreut in Templates
- Code, Bezeichner und Commits auf Englisch; README zweisprachig oder Englisch (für das Teilen)
- Conventional Commits (`feat:`, `fix:`, `docs:` …)
- Bewertungslogik in `scoring.py` als reine Funktionen, mit Unit-Tests
- Nach jedem abgeschlossenen Feature bzw. größeren Code-Änderung den Skill `/simplify` auf die Änderungen ausführen (vor dem Commit), danach Tests und ruff erneut laufen lassen
- Keine Fragen zu kopiergeschützten Kursmaterialien committen: **`questions/` und `data/` stehen in `.gitignore`**
- Konfiguration über Umgebungsvariablen: `DB_PATH` (Default `./data/quizbank.db`), `PORT` (Default `8000`), `QUESTIONS_DIR` (Default `./questions`), `AUTO_IMPORT` (Default `true`), `APP_LANG` (Default `de`)

## 10. Bewertungsregeln

- `single`: richtig = volle Punkte, sonst 0
- `multiple`: Default alles-oder-nichts; über optionales Feld `multiple_scoring` (`all_or_nothing` | `partial`) auf Klausurebene im JSON umschaltbar
  - `partial`: `points × max(0, richtige Kreuze − falsche Kreuze) / Anzahl richtiger Optionen`
- `open`: Summe der selbst vergebenen Raster-Punkte (auf 0…`points` begrenzt); gilt als richtig bei voller Punktzahl, ohne Selbstbewertung `is_correct = None`
- Kein Punktabzug für falsche Antworten (Default)
- `passed = score >= pass_mark`; solange eine `open`-Frage noch nicht selbst bewertet ist, bleibt `passed` leer (`None`)
- `passed` nur für Versuche mit `mode = exam` über alle Fragen; Übung und Wiederholen zeigen nur Punkte und Prozent (`passed = None`)
- „Falsch" (für Pool und Statistik) = `is_correct is False` **oder unbeantwortet**; teilweise richtige `multiple`-Antworten zählen als falsch, nicht selbst bewertete `open`-Fragen zählen nicht

## 11. Generierungs-Prompt (Entwurf für Claude)

> Erstelle eine Probeklausur als JSON im Format `format_version: 1` (Schema siehe unten).
> Kurs: {KURS}. {ANZAHL} Aufgaben, Typ `single`, je 5 Optionen, genau eine richtig, 1 Punkt.
> Themen gleichmäßig verteilt auf: {THEMENLISTE}. Feld `topic` = Thema, `tags` = 1–3 Schlagwörter zum konkreten Konzept.
> Zu jeder Frage `explanation` (warum die richtige Lösung stimmt) und `option_explanations` (warum jede falsche Option falsch ist).
> Feld `source` mit der Fundstelle (Foliensatz/Folie bzw. Buchkapitel/Seite), sofern bekannt.
> IDs nach dem Muster `{kürzel}-p{nr}-{laufnummer:03}`.
> Gib **nur** das JSON aus, ohne Markdown-Codeblock.

## 12. Offene Entscheidungen

- [x] HTMX vs. Alpine.js → **Alpine.js**, kein HTMX
- [x] Teilpunkte bei `multiple` → Feld `multiple_scoring` pro Klausur im JSON
- [x] Fortschritt laufender Versuche → **ja**, jede Antwort wird sofort gespeichert; unfertige Versuche können fortgesetzt werden
- [x] Historie → **alle** Versuche behalten, manuelles Löschen möglich
- [x] Sprache → Deutsch jetzt, Struktur EN-fähig (`app/locales/`)
- [x] Migrationen → Alembic ab Start
- [x] Re-Import vs. Historie → **eingefroren**: jeder Versuch speichert Snapshots seiner Fragen (§6)
- [x] Datei vs. UI → **Dateien gewinnen**; Kurs-Umbenennung nur über `display_name`
- [x] Timer → Server-Deadline, läuft weiter, lazy Auto-Abgabe
- [x] „Falsche wiederholen" → persistenter **Falsch-Pool pro Exam** (raus nach 2× richtig in Folge) statt „letzte N Versuche"
- [x] Selbstbewertung `open` → pro Raster-Kriterium (`rubric_scores`)
- [x] Redesign der UI → **„Antwortbogen“**: eigenes Designsystem statt Pico, linke Seitenleiste, Quiz im Fokusmodus mit Antwortbogen, Fragenaktionen im „⋯“-Menü (Aktionen statt Zustände), eine Fragenliste mit Filtern statt eigener Pool-Tabelle

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- graphify-out/ is gitignored (local only); don't commit it.
