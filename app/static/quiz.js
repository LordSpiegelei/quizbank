// Exam runner (Alpine.js): one question at a time, every change is saved immediately.
// Data comes from the #quiz-data JSON block rendered by app/routes/attempts.py.
const LETTERS = "ABCDEFGHIJ"; // option labels and shortcuts; questions have at most 10 options
document.addEventListener("alpine:init", () => {
  Alpine.data("quiz", () => {
    const data = JSON.parse(document.getElementById("quiz-data").textContent);
    return {
      questions: data.questions,
      texts: data.texts,
      feedback: data.feedback, // "instant": a Prüfen button reveals each solution
      remaining: data.remaining, // seconds, from the server's deadline; null without time limit
      elapsed: data.elapsed, // seconds since the start; shown when there is no countdown
      // Current question. Not named `index`: inside x-for loops Alpine's scope shadows that
      // name with the loop index, which broke the `current` getter in short attempts.
      pos: 0,
      status: "",
      pending: 0,
      queue: Promise.resolve(), // saves run one after another so they arrive in order
      textTimers: {},

      get current() {
        return this.questions[this.pos];
      },

      get answeredCount() {
        return this.questions.filter((q) => this.isAnswered(q)).length;
      },

      get isLast() {
        return this.pos === this.questions.length - 1;
      },

      // With instant feedback, each question is checked before moving on.
      get canAdvance() {
        return this.feedback !== "instant" || this.current.solution !== null;
      },

      get clock() {
        const s = this.remaining ?? this.elapsed;
        const pad = (n) => String(n).padStart(2, "0");
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
      },

      init() {
        // Resume at the first unanswered question.
        const first = this.questions.findIndex((q) => !this.isAnswered(q));
        this.pos = first === -1 ? 0 : first;
        if (this.remaining !== null) {
          const end = Date.now() + this.remaining * 1000;
          const tick = () => {
            this.remaining = Math.max(0, Math.round((end - Date.now()) / 1000));
            if (this.remaining === 0) {
              clearInterval(timer);
              this.finish();
            }
          };
          const timer = setInterval(tick, 1000);
          tick();
        } else {
          const start = Date.now() - this.elapsed * 1000;
          setInterval(() => (this.elapsed = Math.round((Date.now() - start) / 1000)), 1000);
        }
        window.addEventListener("keydown", (event) => this.onKey(event));
        window.addEventListener("beforeunload", (event) => {
          if (this.pending) event.preventDefault();
        });
      },

      // What the main button does right now: check (instant feedback), next or submit.
      primary() {
        if (!this.canAdvance) this.check(this.current);
        else if (this.isLast) this.submit();
        else this.go(this.pos + 1);
      },

      // 1–9 or A–J pick an option, Enter = main button, ←/→ move, S stars.
      onKey(event) {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const target = event.target;
        if (target.tagName === "TEXTAREA" || target.isContentEditable) return; // typing an answer
        const q = this.current;
        const key = event.key;
        const option = /^[1-9]$/.test(key) ? Number(key) - 1 : this.letterIndex(key);

        if (option >= 0) {
          if (q.type !== "open" && option < q.options.length) this.toggle(option);
        } else if (key === "Enter") {
          if (["BUTTON", "A", "SUMMARY"].includes(target.tagName)) return; // let it click itself
          this.primary();
        } else if (key === "ArrowRight" || key === "ArrowLeft") {
          this.go(this.pos + (key === "ArrowRight" ? 1 : -1));
        } else if (key.toLowerCase() === "s") {
          if (q.flags) this.setFlag(q, "starred", !q.flags.starred);
        } else {
          return;
        }
        event.preventDefault();
      },

      // A UI text with {placeholders} filled in; "<key>.one" when n is 1, as in app/locales.
      fmt(key, vars = {}) {
        if (vars.n == 1 && this.texts[`${key}.one`]) key = `${key}.one`;
        return this.texts[key].replace(/\{(\w+)\}/g, (_, name) => vars[name]);
      },

      get answeredLabel() {
        return this.fmt("quiz.answered_of", { n: this.answeredCount, total: this.questions.length });
      },

      letter(i) {
        return LETTERS[i];
      },

      letterIndex(key) {
        return key.length === 1 ? LETTERS.indexOf(key.toUpperCase()) : -1;
      },

      // The shortcut hint, fitted to the current question's options.
      get keyHint() {
        const n = this.current.type === "open" ? 0 : this.current.options.length;
        const pick = n ? this.fmt("quiz.keys_options", { n: Math.min(n, 9), letter: this.letter(n - 1) }) : "";
        return [pick, this.texts["quiz.keys"]].filter(Boolean).join(" · ");
      },

      isAnswered(q) {
        return q.type === "open" ? q.text_answer.trim() !== "" : q.selected.length > 0;
      },

      go(i) {
        if (i < 0 || i >= this.questions.length) return;
        this.flushText();
        this.pos = i;
        window.scrollTo({ top: 0 });
      },

      // State classes of option i: chosen, and after checking correct / wrong (a wrong pick).
      optionClass(i) {
        const chosen = this.current.selected.includes(i);
        const correct = this.current.solution?.correct.includes(i) ?? false;
        return { chosen, correct, wrong: !!this.current.solution && chosen && !correct };
      },

      toggle(i) {
        const q = this.current;
        if (q.solution) return; // locked once revealed
        if (q.type === "single") {
          q.selected = [i];
        } else {
          q.selected = q.selected.includes(i)
            ? q.selected.filter((x) => x !== i)
            : [...q.selected, i].sort((a, b) => a - b);
        }
        this.save(q, { selected: q.selected });
      },

      queueText(q) {
        clearTimeout(this.textTimers[q.id]);
        this.textTimers[q.id] = setTimeout(() => this.saveText(q), 800);
      },

      saveText(q) {
        if (!(q.id in this.textTimers)) return; // nothing typed since the last save
        clearTimeout(this.textTimers[q.id]);
        delete this.textTimers[q.id];
        this.save(q, { text_answer: q.text_answer });
      },

      flushText() {
        for (const q of this.questions) this.saveText(q);
      },

      save(q, body) {
        this.pending++;
        this.status = this.texts["quiz.saving"];
        this.queue = this.queue.then(async () => {
          try {
            const response = await fetch(q.url, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
            if (response.status === 409) {
              // Finished or out of time: the attempt page redirects to the review.
              window.location.reload();
              return;
            }
            this.status = this.texts[response.ok ? "quiz.saved" : "quiz.save_failed"];
          } catch {
            this.status = this.texts["quiz.save_failed"];
          } finally {
            this.pending--;
          }
        });
      },

      async check(q) {
        // Store the latest text first so the solution is judged against it.
        this.saveText(q);
        await this.queue;
        try {
          const response = await fetch(q.revealUrl, { method: "POST" });
          if (response.status === 409) {
            window.location.reload();
            return;
          }
          if (!response.ok) throw new Error(response.status);
          q.solution = await response.json();
        } catch {
          this.status = this.texts["quiz.reveal_failed"];
        }
      },

      // Stars and disabled flags change the question itself, not this attempt.
      async setFlag(q, flag, value) {
        const response = await fetch(q.flagUrls[flag], {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        });
        if (response.ok) q.flags[flag] = value;
      },

      submit() {
        const open = this.questions.length - this.answeredCount;
        const message = open
          ? this.fmt("quiz.finish_confirm_unanswered", { n: open })
          : this.texts["quiz.finish_confirm"];
        if (confirm(message)) this.finish();
      },

      async finish() {
        this.flushText();
        await this.queue;
        document.getElementById("finish-form").submit();
      },
    };
  });
});
