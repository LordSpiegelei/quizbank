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
})();
