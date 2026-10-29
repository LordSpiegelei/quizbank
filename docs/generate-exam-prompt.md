# Generating a mock exam with Claude

This prompt asks Claude for an exam file that quizbank can import directly.

**How to use it:**
1. Attach [`schema/exam.schema.json`](../schema/exam.schema.json) to the chat, along with your own material: lecture notes, slides or a list of topics.
2. Copy one of the prompts below and replace the `{PLACEHOLDERS}`.
3. Save Claude's answer as a `.json` file and upload it on the **Import** page, or put it into `questions/`.

## Prompt (English)

```text
Create a mock exam as JSON in quizbank's format (format_version 1). The attached
JSON Schema is binding; output must validate against it.

Course: {COURSE}
Exam title: {TITLE}
Time limit: {MINUTES} minutes. Pass mark: {PASS_MARK} points.

Write {N} questions of type "single", each with {OPTIONS} options and exactly one
correct answer, worth 1 point each. Spread them evenly across these topics:
{TOPICS}

For every question:
- "topic": one of the topics above; "tags": 1–3 keywords for the specific concept.
- "explanation": why the correct answer is right.
- "option_explanations": one entry per option, saying why it is right or wrong.
- "source": where the material comes from (e.g. "Slides 2, slide 14"), if known.
- "id": following the pattern {PREFIX}-p{EXAM_NUMBER}-{RUNNING_NUMBER:03}.
- "correct": 0-based index of the correct option. Vary its position.

Base the questions on the attached material. Make the wrong options plausible
(typical mistakes), not obviously absurd. Markdown is allowed in texts.

Output only the JSON, with no Markdown code block and no comments.
```

**Variants:**
- *Multiple choice:* "… of type `multiple`, each with 4–6 options of which one or more are correct", plus `"multiple_scoring": "partial"` on the exam if partly right answers should earn points.
- *Case studies:* "Add {K} questions of type `open` with a `scenario`, a `model_answer` and a `rubric` whose points add up to the question's `points`."

## Prompt (Deutsch)

```text
Erstelle eine Probeklausur als JSON im Format von quizbank (format_version 1).
Das angehängte JSON-Schema ist verbindlich; die Ausgabe muss dagegen gültig sein.

Kurs: {KURS}
Titel der Probeklausur: {TITEL}
Zeitlimit: {MINUTEN} Minuten. Bestehensgrenze: {PUNKTE} Punkte.

{ANZAHL} Aufgaben vom Typ "single", je {OPTIONEN} Optionen, genau eine richtig,
je 1 Punkt. Themen gleichmäßig verteilt auf:
{THEMENLISTE}

Zu jeder Frage:
- "topic" = eines der Themen oben; "tags" = 1–3 Schlagwörter zum konkreten Konzept.
- "explanation": warum die richtige Lösung stimmt.
- "option_explanations": ein Eintrag pro Option, warum sie richtig oder falsch ist.
- "source": Fundstelle (Foliensatz/Folie bzw. Buchkapitel/Seite), sofern bekannt.
- "id" nach dem Muster {KÜRZEL}-p{NR}-{LAUFNUMMER:03}.
- "correct": 0-basierter Index der richtigen Option; Position variieren.

Grundlage ist das angehängte Material. Falsche Optionen sollen plausibel sein
(typische Fehler), nicht offensichtlich absurd. Markdown in Texten ist erlaubt.

Gib nur das JSON aus, ohne Markdown-Codeblock und ohne Kommentare.
```

## Tips

- **Large exams:** ask for 20–30 questions per answer and merge the files, or ask for the next batch with new running numbers. The `id`s must stay unique within a file.
- **Import errors** name the exact field, e.g. `questions[4].correct: index 5 out of range`. Paste the message back into the chat and ask Claude to fix the file.
- **Improving questions:** importing the same file again updates the questions that keep their `id`. Your history is not affected.
- **Grading open answers:** on the review page, "Für Claude kopieren" copies the task, rubric, model answer and your answer as text, ready to paste into a chat.
- **Copyright:** questions generated from course material you don't own the rights to are for your own use. Don't publish them.
