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
| Styling | **Pico.css v2**, lokal in `app/static/vendor/` | sauberes Design, Hell/Dunkel eingebaut, offline-fähig |
| Diagramme | **Chart.js** (lokal) bzw. CSS-Balken | einfache Statistik ohne Build |
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
- Validierungsfehler werden pro Frage mit Pfad angezeigt (z. B. `questions[4].correct: index 5 out of range`)

## 6. Datenmodell (SQLModel)

- `Course` (id, name, default_time_limit, default_pass_mark)
- `Exam` (id, course_id, title, time_limit_minutes, pass_mark, imported_at)
- `Question` (id [intern, int], external_id [aus JSON, eindeutig pro Exam], exam_id, type, topic, tags [JSON], source, question, scenario, options [JSON], correct [JSON], points, explanation, option_explanations [JSON], model_answer, rubric [JSON], starred, disabled)
- `Attempt` (id, exam_id, mode [`exam` | `practice` | `retry_wrong`], filter [JSON: Themen/Tags/Anzahl], started_at, finished_at, duration_seconds, score, max_score, passed)
  - Historie pro Quiz = alle `Attempt`s eines `Exam`
  - „Falsche wiederholen“ = Fragen mit `Answer.is_correct = false` aus dem letzten bzw. den letzten N Versuchen dieses `Exam`
- `Answer` (id, attempt_id, question_id, selected [JSON], text_answer, self_score, is_correct)

## 7. Features nach Phasen

### Phase 1 – MVP (Ziel: in 1–2 Sessions nutzbar)
- [ ] Projektgerüst, Docker, Healthcheck
- [ ] JSON-Upload im Browser + Validierung + Import (Duplikaterkennung über `id`)
- [ ] Übersicht: Kurse → Probeklausuren (Anzahl Fragen, letztes Ergebnis)
- [ ] Quiz durchführen (eine Frage pro Seite, Navigation vor/zurück)
- [ ] Auswertung am Ende: Punkte, richtig/falsch, **Erklärung zur richtigen Lösung**, Erklärung pro Option, **Quelle**
- [ ] **Ergebnis-Historie pro Quiz** (Versuche speichern und auflisten)

### Phase 2 – Lernfunktionen (Funktionsumfang wie quiz-prep)
- [ ] Start-Dialog: Quiz wählen, **Filter nach Thema/Tags**, Fragenanzahl, Timer an/aus
- [ ] Zwei Feedback-Modi: **Sofort-Feedback** nach jeder Frage oder **Auswertung erst am Ende**
- [ ] **Falsche Fragen pro Quiz wiederholen** (letzter Versuch oder letzte N Versuche)
- [ ] Fragen **markieren** (Stern) und **deaktivieren**; beim Start filtern: alle / nur markierte / ohne deaktivierte; alles zurücksetzen in den Einstellungen
- [ ] Fragen und Antwortoptionen zufällig mischen
- [ ] **Export** eines Quiz zurück nach JSON
- [ ] **Tastatursteuerung** (1–5 bzw. A–E zum Auswählen, Enter = weiter, ←/→ navigieren, S = markieren)
- [ ] Hell/Dunkel-Modus

### Phase 2b – Klausursimulation & Statistik (eigene Ergänzung)
- [ ] Klausurmodus: Countdown mit Auto-Abgabe, keine Lösungen bis zur Abgabe, „bestanden/nicht bestanden“ anhand `pass_mark`
- [ ] Statistik pro Thema (Trefferquote über alle Versuche), Verlauf als einfaches Diagramm

### Phase 3 – Case Studies
- [ ] Fragetyp `open`: Textfeld, nach Abgabe Musterlösung + Raster, Selbstbewertung pro Kriterium
- [ ] Export einer Antwort als Text (zum Einfügen in den Claude-Chat)

### Phase 4 – Teilen
- [ ] README (Englisch) mit Screenshots, Quickstart, Format-Doku
- [ ] Generierungs-Prompt als `docs/generate-exam-prompt.md`
- [ ] Beispiel-Fragenbank mit **neutralen, selbst geschriebenen** Fragen in `examples/`
- [ ] GitHub Actions: ruff + pytest; Docker-Image auf GHCR veröffentlichen
- [ ] Lizenz: MIT

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
├── alembic/               # Migrationen
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
│   └── static/            # app.css, quiz.js (Alpine), vendor/ (Pico, Alpine, Chart.js, KaTeX)
├── schema/exam.schema.json
├── examples/sample-exam.json
├── docs/generate-exam-prompt.md
├── data/                  # SQLite (gitignored)
├── questions/             # eigene Fragenbanken (gitignored!)
└── tests/
```

## 9. Konventionen

- **UI-Texte auf Deutsch**, zentral in `app/texts.py` (bzw. einer Übersetzungsdatei), nicht verstreut in Templates
- Code, Bezeichner und Commits auf Englisch; README zweisprachig oder Englisch (für das Teilen)
- Conventional Commits (`feat:`, `fix:`, `docs:` …)
- Bewertungslogik in `scoring.py` als reine Funktionen, mit Unit-Tests
- Keine Fragen zu kopiergeschützten Kursmaterialien committen: **`questions/` und `data/` stehen in `.gitignore`**
- Konfiguration über Umgebungsvariablen: `DB_PATH` (Default `./data/quizbank.db`), `PORT` (Default `8000`), `QUESTIONS_DIR` (Default `./questions`), `AUTO_IMPORT` (Default `true`), `APP_LANG` (Default `de`)

## 10. Bewertungsregeln

- `single`: richtig = volle Punkte, sonst 0
- `multiple`: Default alles-oder-nichts; über optionales Feld `multiple_scoring` (`all_or_nothing` | `partial`) auf Klausurebene im JSON umschaltbar
- `open`: Summe der selbst vergebenen Raster-Punkte (max. `points`)
- Kein Punktabzug für falsche Antworten (Default)
- `passed = score >= pass_mark`

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
