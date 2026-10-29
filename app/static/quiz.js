// Exam runner (Alpine.js): one question at a time, every change is saved immediately.
// Data comes from the #quiz-data JSON block rendered by app/routes/attempts.py.
document.addEventListener("alpine:init", () => {
  Alpine.data("quiz", () => {
    const data = JSON.parse(document.getElementById("quiz-data").textContent);
    return {
      questions: data.questions,
      texts: data.texts,
      feedback: data.feedback, // "instant": a Prüfen button reveals each solution
      remaining: data.remaining, // seconds, from the server's deadline; null without time limit
      elapsed: data.elapsed, // seconds since the start; shown when there is no countdown
      index: 0,
      status: "",
      pending: 0,
      queue: Promise.resolve(), // saves run one after another so they arrive in order
      textTimers: {},

      get current() {
        return this.questions[this.index];
      },

      get isLast() {
        return this.index === this.questions.length - 1;
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
        this.index = first === -1 ? 0 : first;
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
        window.addEventListener("beforeunload", (event) => {
          if (this.pending) event.preventDefault();
        });
      },

      letter(i) {
        return String.fromCharCode(65 + i);
      },

      isAnswered(q) {
        return q.type === "open" ? q.text_answer.trim() !== "" : q.selected.length > 0;
      },

      go(i) {
        if (i < 0 || i >= this.questions.length) return;
        this.flushText();
        this.index = i;
        window.scrollTo({ top: 0 });
      },

      optionClass(i) {
        const s = this.current.solution;
        if (!s) return {};
        const correct = s.correct.includes(i);
        return { correct, wrong: !correct && this.current.selected.includes(i) };
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

      submit() {
        const open = this.questions.filter((q) => !this.isAnswered(q)).length;
        const message = open
          ? this.texts["quiz.finish_confirm_unanswered"].replace("{n}", open)
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
