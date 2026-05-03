// search.js — client-side fuzzy search over the static search_index.json
// Saves searches to localStorage. ROOT is injected by the template.

(function () {
  const LS_KEY = "altsports-saved-searches";
  let index = [];
  let loaded = false;

  // ── Simple trigram similarity ──────────────────────────────────────────
  function trigrams(str) {
    const s = " " + str.toLowerCase().replace(/[^a-z0-9 ]/g, "") + " ";
    const set = new Set();
    for (let i = 0; i < s.length - 2; i++) set.add(s.slice(i, i + 3));
    return set;
  }

  function trigramScore(a, b) {
    const ta = trigrams(a);
    const tb = trigrams(b);
    let common = 0;
    ta.forEach(t => { if (tb.has(t)) common++; });
    return (2 * common) / (ta.size + tb.size);
  }

  function search(query) {
    if (!query || query.length < 2) return [];
    const q = query.toLowerCase();
    const results = [];
    for (const player of index) {
      const name = player.name.toLowerCase();
      // Exact prefix wins
      let score = 0;
      if (name.startsWith(q)) {
        score = 1.2;
      } else if (name.includes(q)) {
        score = 1.0;
      } else {
        score = trigramScore(q, name);
      }
      if (score > 0.25) {
        results.push({ player, score });
      }
    }
    results.sort((a, b) => b.score - a.score);
    return results.slice(0, 50).map(r => r.player);
  }

  // ── Saved searches ─────────────────────────────────────────────────────
  function getSaved() {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || "[]");
    } catch { return []; }
  }

  function setSaved(arr) {
    localStorage.setItem(LS_KEY, JSON.stringify(arr));
  }

  function saveSearch(query) {
    const arr = getSaved().filter(s => s !== query);
    arr.unshift(query);
    setSaved(arr.slice(0, 20));
    renderSaved();
  }

  function removeSearch(query) {
    setSaved(getSaved().filter(s => s !== query));
    renderSaved();
  }

  function renderSaved() {
    const saved = getSaved();
    let container = document.getElementById("saved-searches");
    if (!container) {
      container = document.createElement("div");
      container.id = "saved-searches";
      document.getElementById("search-ui").appendChild(container);
    }
    if (saved.length === 0) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = "<strong>Saved searches:</strong> ";
    saved.forEach(q => {
      const span = document.createElement("span");
      span.className = "saved-search-item";
      span.textContent = q;
      span.title = "Click to search again";
      span.addEventListener("click", () => {
        document.getElementById("search-input").value = q;
        doSearch(q);
      });
      const del = document.createElement("button");
      del.className = "saved-search-delete";
      del.textContent = "×";
      del.title = "Remove saved search";
      del.setAttribute("aria-label", "Remove " + q);
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        removeSearch(q);
      });
      span.appendChild(del);
      container.appendChild(span);
    });
  }

  // ── Render results ─────────────────────────────────────────────────────
  function renderResults(results, query) {
    const ul = document.getElementById("search-results");
    const meta = document.getElementById("search-meta");
    ul.innerHTML = "";
    if (!query) {
      meta.textContent = "";
      return;
    }
    meta.textContent = results.length
      ? results.length + " result" + (results.length !== 1 ? "s" : "")
      : `No results for "${query}"`;

    results.forEach(p => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = ROOT + "players/" + p.id + ".html";
      a.textContent = p.name;
      li.appendChild(a);
      const meta2 = document.createElement("span");
      meta2.className = "result-meta";
      const parts = [];
      if (p.positions && p.positions.length) parts.push(p.positions.join("/"));
      if (p.leagues && p.leagues.length) parts.push(p.leagues.join(", "));
      if (p.ambiguous) parts.push("⚠ ambiguous");
      meta2.textContent = parts.join(" · ");
      li.appendChild(meta2);
      ul.appendChild(li);
    });
  }

  // ── Main search handler ─────────────────────────────────────────────────
  let debounceTimer;
  function doSearch(query) {
    const results = search(query.trim());
    renderResults(results, query.trim());
  }

  // ── Init ───────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("search-input");
    if (!input) return;

    renderSaved();

    // Load index
    fetch(ROOT + "data/search_index.json")
      .then(r => r.json())
      .then(data => {
        index = data;
        loaded = true;
        // If there's a saved query in URL or input, run it
        const params = new URLSearchParams(window.location.search);
        const q = params.get("q") || "";
        if (q) {
          input.value = q;
          doSearch(q);
        }
      });

    input.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      const q = this.value;
      debounceTimer = setTimeout(() => doSearch(q), 150);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && this.value.trim()) {
        saveSearch(this.value.trim());
        // Update URL without reload
        const url = new URL(window.location);
        url.searchParams.set("q", this.value.trim());
        history.replaceState({}, "", url);
      }
    });
  });
})();
