// theme.js — cycles dark → light → cool, persists to localStorage
(function () {
  const THEMES = ["dark", "light", "cool"];
  const stored = localStorage.getItem("altsports-theme");
  const initial = THEMES.includes(stored) ? stored : "dark";
  document.documentElement.setAttribute("data-theme", initial);

  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;

    function updateLabel(theme) {
      const labels = { dark: "◑", light: "○", cool: "◈" };
      btn.textContent = labels[theme] || "◑";
      btn.title = "Theme: " + theme + " (click to cycle)";
    }

    updateLabel(initial);

    btn.addEventListener("click", function () {
      const current = document.documentElement.getAttribute("data-theme");
      const idx = THEMES.indexOf(current);
      const next = THEMES[(idx + 1) % THEMES.length];
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("altsports-theme", next);
      updateLabel(next);
    });
  });
})();
