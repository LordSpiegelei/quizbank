// App shell behaviour: light/dark switch and the sidebar's course filter.
// base.html applies the saved theme before the first paint; the CSS reads data-theme.
(() => {
  const button = document.getElementById("theme-toggle");
  if (button) {
    const root = document.documentElement;
    const order = ["auto", "light", "dark"];
    const current = () => root.dataset.theme || "auto";
    const show = () => (button.textContent = button.dataset[current()]);

    show();
    button.addEventListener("click", () => {
      const next = order[(order.indexOf(current()) + 1) % order.length];
      if (next === "auto") delete root.dataset.theme;
      else root.dataset.theme = next;
      show();
      try {
        if (next === "auto") localStorage.removeItem("theme");
        else localStorage.setItem("theme", next);
      } catch {
        // storage blocked: the choice lasts until the next page
      }
    });
  }

  // Filters the course list as you type; the full list is already in the page.
  const search = document.getElementById("nav-search");
  search?.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    for (const li of document.querySelectorAll(".nav-courses li")) {
      li.hidden = q !== "" && !li.textContent.toLowerCase().includes(q);
    }
  });

  // "⋯" menus are <details>: close them on a click elsewhere or with Escape.
  const openMenus = () => document.querySelectorAll("details.menu[open]");
  document.addEventListener("click", (event) => {
    for (const menu of openMenus()) if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const menu of openMenus()) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });

  // Radio switches (tabs, filters) record their choice in the URL ...
  for (const input of document.querySelectorAll("input[data-url-param]")) {
    input.addEventListener("change", () => {
      const url = new URL(location.href);
      url.searchParams.set(input.dataset.urlParam, input.value);
      history.replaceState(null, "", url);
    });
  }
  // ... and every form returns there: the current query wins over the template's fallback.
  document.addEventListener("submit", (event) => {
    const next = event.target.querySelector('input[name="next"]');
    if (!next) return;
    const target = new URL(next.value, location.href);
    for (const [key, value] of new URL(location.href).searchParams) target.searchParams.set(key, value);
    next.value = target.pathname + target.search + target.hash;
  });

  // Self-assessment: show the rubric total while typing (the server validates again).
  for (const output of document.querySelectorAll("output[data-sum-of]")) {
    const inputs = output.form.querySelectorAll(`input[name="${output.dataset.sumOf}"]`);
    const update = () => {
      const total = [...inputs].reduce((sum, i) => sum + (Number(i.value.replace(",", ".")) || 0), 0);
      output.value = (Math.round(total * 100) / 100).toLocaleString(document.documentElement.lang);
    };
    for (const input of inputs) input.addEventListener("input", update);
  }

  // Copy buttons: copy the textarea just before them, then say so.
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.previousElementSibling.value);
      button.textContent = button.dataset.done;
    });
  }
  // AI review: fetch a suggestion, show the feedback and prefill the self-assessment form
  // below (the user still saves it).
  for (const box of document.querySelectorAll("[data-ai-review]")) {
    const form = box.parentElement.querySelector("form.assess");
    const feedback = box.querySelector(".ai-feedback");
    const apply = (scores) => {
      const inputs = form.querySelectorAll('input[name="rubric"], input[name="self_score"]');
      inputs.forEach((input, i) => {
        if (scores[i] === undefined) return;
        input.value = scores[i];
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    };
    box.querySelector("[data-ai-apply]")?.addEventListener("click", () => apply(JSON.parse(box.dataset.scores)));
    box.querySelector("[data-ai-run]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const label = button.textContent;
      button.disabled = true;
      button.textContent = box.dataset.busy;
      box.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(box.dataset.aiReview, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: box.querySelector("select[name=model]").value }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "");
        box.dataset.scores = JSON.stringify(data.scores);
        feedback.innerHTML = data.feedback_html;
        apply(data.scores);
        const note = document.createElement("p");
        note.className = "muted";
        note.textContent = box.dataset.applied;
        feedback.append(note);
      } catch (error) {
        feedback.textContent = "";
        const message = document.createElement("p");
        message.className = "ai-error";
        message.textContent = [box.dataset.failed, error.message].filter(Boolean).join(" ");
        feedback.append(message);
      } finally {
        button.disabled = false;
        button.textContent = label;
        box.removeAttribute("aria-busy");
      }
    });
  }
  // Exam builder: `accept` and the token meter follow the chosen model (the server checks again).
  const builder = document.querySelector("form[data-builder]");
  if (builder) {
    const { model, files, material } = builder.elements;
    const meter = builder.querySelector("[data-meter]");
    const hint = builder.querySelector("[data-accept-hint]");
    const imageTypes = builder.dataset.imageTypes.split(",");
    const reserve = () => {
      const count = ["single", "multiple", "open"].reduce((n, k) => n + (Number(builder.elements[k].value) || 0), 0);
      return 1500 + Math.min(count, 5) * 600;
    };
    let context = Number(builder.dataset.context) || 0;
    const update = () => {
      const picked = [...files.files];
      const images = picked.filter((f) => imageTypes.some((ext) => f.name.toLowerCase().endsWith(ext)));
      const textBytes = picked.reduce((n, f) => n + f.size, 0) - images.reduce((n, f) => n + f.size, 0);
      const used = Math.round((material.value.length + textBytes) / 4 + images.length * 1500 + reserve());
      meter.textContent = meter.dataset.template.replace("{used}", used).replace("{limit}", context);
      meter.classList.toggle("over", context > 0 && used > context);
    };
    const refresh = async () => {
      const path = model.value.split("/").map(encodeURIComponent).join("/");
      try {
        const response = await fetch(builder.dataset.infoUrl.replace("__model__", path));
        if (!response.ok) return;
        const info = await response.json();
        context = info.context_tokens;
        files.accept = info.accept.join(",");
        hint.textContent = hint.dataset.template.replace("{formats}", info.accept.join(" "));
      } catch { /* the server reports problems on submit */ }
      update();
    };
    model.addEventListener("change", refresh);
    for (const el of [material, files, ...["single", "multiple", "open"].map((k) => builder.elements[k])]) {
      el.addEventListener("input", update);
    }
    update();
  }

  // Draft page: while something is generating, reload as soon as the server has news.
  const poller = document.querySelector("[data-draft-poll]");
  if (poller) {
    const tick = async () => {
      try {
        const data = await (await fetch(poller.dataset.draftPoll)).json();
        if (data.version !== poller.dataset.version) return location.reload();
      } catch { /* try again */ }
      setTimeout(tick, 2000);
    };
    setTimeout(tick, 2000);
  }
})();
