// Light/dark switch: automatic (system) -> light -> dark, remembered in localStorage.
// base.html applies the saved choice before the first paint; Pico reads data-theme.
(() => {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
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
})();
