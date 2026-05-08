(function () {
  const DEFAULT_GAME_ID = "04002611070524";
  const DEFAULT_LEAGUE = "af1-2025";
  const REFRESH_MS = 60000;

  const state = {
    gameId: DEFAULT_GAME_ID,
    league: DEFAULT_LEAGUE,
    pollTimer: null,
    inGameCards: [],
    seasonCards: [],
    inGameIndex: 0,
    seasonIndex: 0,
    rotatorTimer: null,
    leagueData: null,
    playerMap: new Map(),
    activePayload: null,
  };

  const el = {
    title: document.getElementById("gc-title"),
    subtitle: document.getElementById("gc-subtitle"),
    status: document.getElementById("gc-status"),
    scoreboard: document.getElementById("gc-scoreboard"),
    events: document.getElementById("gc-events"),
    scoring: document.getElementById("gc-scoring"),
    matchup: document.getElementById("gc-matchup"),
    boxscore: document.getElementById("gc-boxscore"),
    playbyplay: document.getElementById("gc-playbyplay"),
    gameInput: document.getElementById("game-id-input"),
    leagueInput: document.getElementById("league-input"),
    controls: document.getElementById("gc-controls"),
    inGameStage: document.getElementById("in-game-leaders"),
    seasonStage: document.getElementById("season-leaders"),
    inGameRotator: document.getElementById("in-game-rotator"),
    seasonRotator: document.getElementById("season-rotator"),
    historyModal: document.getElementById("player-history-modal"),
    historyTitle: document.getElementById("history-title"),
    historyContent: document.getElementById("history-content"),
  };

  function escapeHtml(value) {
    const str = String(value ?? "");
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normName(name) {
    return String(name || "")
      .replace(/^\s*\d+\s+/, "")
      .replace(/[^a-zA-Z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function toNumber(value) {
    const num = parseFloat(String(value ?? "").replace(/,/g, ""));
    return Number.isFinite(num) ? num : 0;
  }

  function fmtTime(ts) {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function parseQuery() {
    const params = new URLSearchParams(window.location.search);
    state.gameId = params.get("gameId") || DEFAULT_GAME_ID;
    state.league = params.get("league") || DEFAULT_LEAGUE;
    el.gameInput.value = state.gameId;
    el.leagueInput.value = state.league;
  }

  function syncQuery() {
    const url = new URL(window.location.href);
    url.searchParams.set("gameId", state.gameId);
    url.searchParams.set("league", state.league);
    window.history.replaceState({}, "", url);
  }

  async function fetchJson(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return res.json();
  }

  async function loadLeagueData() {
    const leagueUrl = `data/leagues/${encodeURIComponent(state.league)}.json`;
    try {
      state.leagueData = await fetchJson(leagueUrl);
      state.playerMap = new Map();
      for (const p of state.leagueData.players || []) {
        state.playerMap.set(normName(p.canonical_name), p);
      }
    } catch (err) {
      state.leagueData = null;
      state.playerMap = new Map();
      updateStatus(`Season data unavailable for ${state.league}.`, true);
    }
  }

  function updateStatus(text, isWarn) {
    el.status.textContent = text;
    el.status.style.color = isWarn ? "#d98b8b" : "var(--text-dim)";
  }

  function renderScoreboard(data) {
    const teams = data.teams || [{ name: "Team A" }, { name: "Team B" }];
    const scores = data.scores || [{ value: "0" }, { value: "0" }];

    el.scoreboard.innerHTML = `
      <div class="score-grid">
        <section class="team-panel">
          <h2 class="team-name">${escapeHtml(teams[0]?.name || "Away")}</h2>
          <p class="team-score">${escapeHtml(scores[0]?.value || "0")}</p>
        </section>
        <section class="game-state">
          <p><strong>${escapeHtml(data.time_remaining || "Live")}</strong></p>
          <p>${escapeHtml(data.quarter || "")}</p>
          <p>${escapeHtml(data.down_to_go || "")} | ${escapeHtml(data.ball_on || "")}</p>
        </section>
        <section class="team-panel">
          <h2 class="team-name">${escapeHtml(teams[1]?.name || "Home")}</h2>
          <p class="team-score">${escapeHtml(scores[1]?.value || "0")}</p>
        </section>
      </div>
    `;

    el.title.textContent = `${teams[0]?.name || "Away"} at ${teams[1]?.name || "Home"}`;
    el.subtitle.textContent = `Game ID ${state.gameId} | League ${state.league}`;
  }

  function renderStringList(target, items, emptyText) {
    if (!items || !items.length) {
      target.innerHTML = `<li>${escapeHtml(emptyText)}</li>`;
      return;
    }
    target.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  }

  function renderMatchup(data) {
    const rows = data.matchup || [];
    if (!rows.length) {
      el.matchup.innerHTML = "<p>No matchup data.</p>";
      return;
    }
    const headA = escapeHtml(data.teams?.[0]?.name || "Away");
    const headB = escapeHtml(data.teams?.[1]?.name || "Home");
    const body = rows.map((r) => `
      <tr>
        <td>${escapeHtml(r[0] || "")}</td>
        <td>${escapeHtml(r[1] || "")}</td>
        <td>${escapeHtml(r[2] || "")}</td>
      </tr>
    `).join("");
    el.matchup.innerHTML = `
      <div class="table-container">
        <table class="stat-table">
          <thead><tr><th>Stat</th><th>${headA}</th><th>${headB}</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
  }

  function parseCategoryTables(categoryHtml) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(categoryHtml || "", "text/html");
    const teamBlocks = Array.from(doc.querySelectorAll(".table-container"));

    return teamBlocks.map((block) => {
      const teamName = block.querySelector("h5")?.textContent?.trim() || "Team";
      const table = block.querySelector("table.player-stats[id]");
      if (!table) {
        return { teamName, headers: [], rows: [] };
      }

      const headers = Array.from(table.querySelectorAll("thead tr th")).map((th) =>
        th.textContent?.trim().toLowerCase() || ""
      );

      const rows = Array.from(table.querySelectorAll("tbody tr"))
        .filter((tr) => !tr.querySelector(".dataTables_empty"))
        .map((tr) => {
          const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent?.trim() || "");
          const mapped = {};
          headers.forEach((h, i) => {
            mapped[h] = cells[i] || "";
          });
          return mapped;
        });

      return { teamName, headers, rows };
    });
  }

  function renderCategoryTable(catName, parsedTeams) {
    const blocks = parsedTeams.map((team) => {
      const headers = team.headers;
      const rowsHtml = team.rows
        .map((row) => {
          const playerRaw = row.player || "";
          const playerNorm = normName(playerRaw);
          const canonical = state.playerMap.get(playerNorm);
          const pid = canonical?.canonical_id || "";

          const cells = headers.map((h) => {
            if (h === "player") {
              if (pid) {
                return `<td><button class="player-link" data-player-id="${escapeHtml(pid)}">${escapeHtml(playerRaw)}</button></td>`;
              }
              return `<td>${escapeHtml(playerRaw)}</td>`;
            }
            return `<td>${escapeHtml(row[h] || "")}</td>`;
          }).join("");
          return `<tr>${cells}</tr>`;
        })
        .join("");

      const headerHtml = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("");
      return `
        <section class="mini-card">
          <h4>${escapeHtml(team.teamName)}</h4>
          <div class="table-container">
            <table class="stat-table">
              <thead><tr>${headerHtml}</tr></thead>
              <tbody>${rowsHtml || `<tr><td colspan="${headers.length || 1}">No entries</td></tr>`}</tbody>
            </table>
          </div>
        </section>
      `;
    }).join("");

    return `
      <section class="box-category">
        <h3>${escapeHtml(catName)}</h3>
        <div class="box-category-grid">${blocks || "<p>No data.</p>"}</div>
      </section>
    `;
  }

  function renderBoxScore(data) {
    const categories = data.boxscore || [];
    if (!categories.length) {
      el.boxscore.innerHTML = "<p>No box score data available.</p>";
      return [];
    }

    const parsed = [];
    el.boxscore.innerHTML = categories.map((cat) => {
      const teams = parseCategoryTables(cat.content || "");
      parsed.push({ category: cat.category || "Category", teams });
      return renderCategoryTable(cat.category || "Category", teams);
    }).join("");
    return parsed;
  }

  function renderPlayByPlay(data) {
    const plays = data.playbyplay || [];
    if (!plays.length) {
      el.playbyplay.innerHTML = "<p>No play-by-play available.</p>";
      return;
    }
    el.playbyplay.innerHTML = plays.map((play) => {
      const text = play.away_description || play.home_description || "";
      return `
        <div class="play-row">
          <div class="play-meta">${escapeHtml(play.team || "")} | ${escapeHtml(play.time || "")}</div>
          <div>${escapeHtml(text)}</div>
        </div>
      `;
    }).join("");
  }

  function buildInGameLeaders(parsedCategories) {
    const byCategory = new Map();
    for (const c of parsedCategories) {
      byCategory.set((c.category || "").toLowerCase(), c.teams || []);
    }

    function flattenRows(category, ydsKey, tdKey) {
      const teams = byCategory.get(category.toLowerCase()) || [];
      const out = [];
      for (const team of teams) {
        for (const row of team.rows || []) {
          const rawName = row.player || "";
          if (!rawName) continue;
          out.push({
            team: team.teamName,
            player: rawName,
            yds: toNumber(row[ydsKey]),
            td: toNumber(row[tdKey]),
          });
        }
      }
      out.sort((a, b) => b.yds - a.yds || b.td - a.td);
      return out.slice(0, 5);
    }

    const pass = flattenRows("passing", "yds", "td");
    const rush = flattenRows("rushing", "yds", "td");
    const rec = flattenRows("receiving", "yds", "td");

    return [
      { title: "Passing Yards", rows: pass, valueLabel: (r) => `${r.yds} yds | ${r.td} TD` },
      { title: "Rushing Yards", rows: rush, valueLabel: (r) => `${r.yds} yds | ${r.td} TD` },
      { title: "Receiving Yards", rows: rec, valueLabel: (r) => `${r.yds} yds | ${r.td} TD` },
    ].filter((card) => card.rows.length);
  }

  function buildSeasonLeaders(parsedCategories) {
    if (!state.leagueData?.players?.length) return [];

    const gamePlayers = new Set();
    parsedCategories.forEach((c) => {
      (c.teams || []).forEach((team) => {
        (team.rows || []).forEach((row) => {
          const p = state.playerMap.get(normName(row.player || ""));
          if (p?.canonical_id) gamePlayers.add(p.canonical_id);
        });
      });
    });

    const seasonPlayers = state.leagueData.players.filter((p) => gamePlayers.has(p.canonical_id));

    function topBy(statKey, label) {
      const rows = seasonPlayers
        .map((p) => ({
          player: p.canonical_name,
          playerId: p.canonical_id,
          value: toNumber(p.stats?.[statKey]),
        }))
        .filter((r) => r.value > 0)
        .sort((a, b) => b.value - a.value)
        .slice(0, 5);

      return { title: label, rows, valueLabel: (r) => `${r.value}` };
    }

    return [
      topBy("passing_yards", "Season Passing Yards"),
      topBy("rushing_yards", "Season Rushing Yards"),
      topBy("receiving_yards", "Season Receiving Yards"),
    ].filter((card) => card.rows.length);
  }

  function leaderCardHtml(card, idx, total, rotateId, isSeason) {
    const rowsHtml = card.rows.map((r) => {
      const mapped = state.playerMap.get(normName(r.player));
      const pid = isSeason ? (r.playerId || "") : (mapped?.canonical_id || "");
      const player = r.player || "Unknown";
      const nameCell = pid
        ? `<button class="leader-name-btn" data-player-id="${escapeHtml(pid)}">${escapeHtml(player)}</button>`
        : escapeHtml(player);
      return `
        <div class="leader-row">
          <div>${nameCell}</div>
          <div class="leader-value">${escapeHtml(card.valueLabel(r))}</div>
        </div>
      `;
    }).join("");

    return `
      <section class="leader-card" data-rotate-id="${escapeHtml(rotateId)}">
        <h4>${escapeHtml(card.title)}</h4>
        ${rowsHtml || "<p>No data</p>"}
      </section>
      <span class="sr-only">${idx + 1}/${total}</span>
    `;
  }

  function renderLeaderStage(stageEl, rotEl, cards, index, rotateId, isSeason) {
    if (!cards.length) {
      stageEl.innerHTML = "<p>No leader data.</p>";
      rotEl.textContent = "0/0";
      return;
    }
    const safe = index % cards.length;
    stageEl.innerHTML = leaderCardHtml(cards[safe], safe, cards.length, rotateId, isSeason);
    rotEl.textContent = `${safe + 1}/${cards.length}`;
  }

  function attachPlayerClickHandlers() {
    document.querySelectorAll("[data-player-id]").forEach((node) => {
      if (node.dataset.bound === "1") return;
      node.dataset.bound = "1";
      node.addEventListener("click", async (evt) => {
        evt.preventDefault();
        const playerId = node.getAttribute("data-player-id");
        if (playerId) {
          await openPlayerHistory(playerId);
        }
      });
    });
  }

  async function openPlayerHistory(playerId) {
    el.historyTitle.textContent = "Player History";
    el.historyContent.innerHTML = "<p>Loading player history...</p>";
    if (!el.historyModal.open) {
      el.historyModal.showModal();
    }

    try {
      const player = await fetchJson(`data/players/${encodeURIComponent(playerId)}.json`);
      const logs = (player.game_log || []).slice().sort((a, b) =>
        String(b.date_str || "").localeCompare(String(a.date_str || ""))
      );

      const seasonTotals = Object.entries(player.season_totals || {}).map(([season, stats]) => ({ season, stats }));

      const totalsHtml = seasonTotals.length
        ? seasonTotals.map((entry) => {
            const statPairs = Object.entries(entry.stats || {}).slice(0, 8);
            const rows = statPairs.map(([k, v]) => `<div class="leader-row"><div>${escapeHtml(k)}</div><div>${escapeHtml(v)}</div></div>`).join("");
            return `<section class="mini-card"><h4>${escapeHtml(entry.season)}</h4>${rows}</section>`;
          }).join("")
        : "<p>No season totals available.</p>";

      const gameRows = logs.slice(0, 12).map((g) => {
        const statText = Object.entries(g.stats || {})
          .map(([k, v]) => `${k}: ${v}`)
          .join(" | ");
        return `<tr><td>${escapeHtml(g.date_str || "")}</td><td>${escapeHtml(g.display || "")}</td><td>${escapeHtml(statText)}</td></tr>`;
      }).join("");

      el.historyTitle.textContent = player.canonical_name || playerId;
      el.historyContent.innerHTML = `
        <div class="history-grid">${totalsHtml}</div>
        <h4>Recent Games</h4>
        <table class="stat-table">
          <thead><tr><th>Date</th><th>Game</th><th>Stats</th></tr></thead>
          <tbody>${gameRows || "<tr><td colspan=\"3\">No game log.</td></tr>"}</tbody>
        </table>
      `;
    } catch (err) {
      el.historyContent.innerHTML = `<p>Unable to load player history for ${escapeHtml(playerId)}.</p>`;
    }
  }

  function rotateLeaders() {
    if (!state.inGameCards.length && !state.seasonCards.length) return;
    if (state.inGameCards.length) {
      state.inGameIndex = (state.inGameIndex + 1) % state.inGameCards.length;
      renderLeaderStage(el.inGameStage, el.inGameRotator, state.inGameCards, state.inGameIndex, "ingame", false);
    }
    if (state.seasonCards.length) {
      state.seasonIndex = (state.seasonIndex + 1) % state.seasonCards.length;
      renderLeaderStage(el.seasonStage, el.seasonRotator, state.seasonCards, state.seasonIndex, "season", true);
    }
    attachPlayerClickHandlers();
  }

  function startLeaderRotation() {
    if (state.rotatorTimer) {
      window.clearInterval(state.rotatorTimer);
    }
    state.rotatorTimer = window.setInterval(rotateLeaders, 9000);
  }

  async function refreshGame() {
    try {
      updateStatus(`Refreshing game ${state.gameId}...`, false);
      const payload = await fetchJson(`https://altfantasysports.com/api/v2/game_status/${encodeURIComponent(state.gameId)}`);
      const data = payload.data || {};

      state.activePayload = data;
      renderScoreboard(data);
      renderStringList(el.events, (data.events || []).map((e) => e.description), "No recent events.");
      renderStringList(el.scoring, data.scoring_summary || [], "No scoring yet.");
      renderMatchup(data);
      const parsedCategories = renderBoxScore(data);
      renderPlayByPlay(data);

      state.inGameCards = buildInGameLeaders(parsedCategories);
      state.seasonCards = buildSeasonLeaders(parsedCategories);
      state.inGameIndex = 0;
      state.seasonIndex = 0;
      renderLeaderStage(el.inGameStage, el.inGameRotator, state.inGameCards, state.inGameIndex, "ingame", false);
      renderLeaderStage(el.seasonStage, el.seasonRotator, state.seasonCards, state.seasonIndex, "season", true);

      attachPlayerClickHandlers();
      updateStatus(`Updated at ${fmtTime(Date.now())}. Auto-refresh every 60 seconds.`, false);
    } catch (err) {
      updateStatus(`Failed to refresh game ${state.gameId}: ${err.message}`, true);
    }
  }

  async function bootstrap(loadLeague) {
    if (loadLeague) {
      await loadLeagueData();
    }
    await refreshGame();
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
    }
    state.pollTimer = window.setInterval(refreshGame, REFRESH_MS);
  }

  function bindControls() {
    el.controls.addEventListener("submit", async (evt) => {
      evt.preventDefault();
      const nextGame = el.gameInput.value.trim();
      const nextLeague = el.leagueInput.value.trim() || DEFAULT_LEAGUE;
      if (!nextGame) return;

      const leagueChanged = nextLeague !== state.league;
      state.gameId = nextGame;
      state.league = nextLeague;
      syncQuery();
      await bootstrap(leagueChanged);
    });
  }

  function bindTabs() {
    const tabBtns = document.querySelectorAll(".gc-tab-btn");
    const tabPanes = document.querySelectorAll(".gc-tab-pane");

    tabBtns.forEach((btn) => {
      btn.addEventListener("click", (evt) => {
        evt.preventDefault();
        const tabName = btn.getAttribute("data-tab");

        // Remove active from all buttons and panes
        tabBtns.forEach((b) => b.classList.remove("active"));
        tabPanes.forEach((p) => p.classList.remove("active"));

        // Add active to clicked button and corresponding pane
        btn.classList.add("active");
        const activePane = document.querySelector(`.gc-tab-pane[data-tab="${tabName}"]`);
        if (activePane) {
          activePane.classList.add("active");
        }
      });
    });
  }

  function init() {
    parseQuery();
    bindControls();
    bindTabs();
    startLeaderRotation();
    bootstrap(true);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
