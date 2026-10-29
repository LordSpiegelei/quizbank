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
})();
