/**
 * AltSports Archive — table enhancements
 * - Sortable columns (click header)
 * - Live filter/search input per table
 * - Column visibility toggle
 */

// ── Stat label map ──────────────────────────────────────────────────────────
const STAT_LABELS = {
  // Passing
  passing_yards:         "Pass Yds",
  passing_tds:           "Pass TD",
  interceptions_lost:    "INT",
  completions:           "Comp",
  attempts:              "Att",
  completion_pct:        "Comp%",
  passing_first_downs:   "Pass 1D",
  sacks:                 "Sacks",
  sack_yards_lost:       "Sack Yds",
  qb_rating:             "QBR",
  // Rushing
  rushing_yards:         "Rush Yds",
  rushing_tds:           "Rush TD",
  carries:               "Car",
  rushing_first_downs:   "Rush 1D",
  rushing_long:          "Rush Lng",
  yards_per_carry:       "YPC",
  // Receiving
  receptions:            "Rec",
  receiving_yards:       "Rec Yds",
  receiving_tds:         "Rec TD",
  targets:               "Tgt",
  receiving_first_downs: "Rec 1D",
  receiving_long:        "Rec Lng",
  yards_per_reception:   "YPR",
  // Kicking
  field_goals_made:      "FGM",
  field_goals_attempted: "FGA",
  field_goal_pct:        "FG%",
  extra_points_made:     "XPM",
  extra_points_attempted:"XPA",
  kickoffs:              "KO",
  kickoff_yards:         "KO Yds",
  touchbacks:            "TB",
  punt_yards:            "Punt Yds",
  punts:                 "Punts",
  punt_avg:              "Punt Avg",
  punt_long:             "Punt Lng",
  // Defense
  tackles:               "Tck",
  solo_tackles:          "Solo",
  assisted_tackles:      "Ast",
  tackles_for_loss:      "TFL",
  sacks_defense:         "Sacks",
  forced_fumbles:        "FF",
  fumble_recoveries:     "FR",
  interceptions:         "INT",
  passes_defended:       "PD",
  defensive_tds:         "Def TD",
  // Special teams
  return_yards:          "Ret Yds",
  return_tds:            "Ret TD",
  // Generic / shared
  fumbles:               "Fum",
  fumbles_lost:          "Fum Lost",
  conversions:           "Conv",
  two_point_conversions: "2PT",
  touchdowns:            "TD",
  points:                "Pts",
  assists:               "Ast",
  goals:                 "Goals",
  blocks:                "Blk",
  turnovers:             "TO",
  // Disc (AUDL/UFA)
  completions_disc:      "Comp",
  throwing_yards:        "Throw Yds",
  throwing_tds:          "Throw TD",
  receiving_yards_disc:  "Rec Yds",
  receiving_tds_disc:    "Rec TD",
  // Basketball (BIG3)
  three_pointers_made:   "3PM",
  three_pointers_att:    "3PA",
  two_pointers_made:     "2PM",
  two_pointers_att:      "2PA",
  free_throws_made:      "FTM",
  free_throws_att:       "FTA",
  rebounds:              "Reb",
  offensive_rebounds:    "OReb",
  defensive_rebounds:    "DReb",
  steals:                "Stl",
};

function statLabel(key) {
  if (STAT_LABELS[key]) return STAT_LABELS[key];
  // fallback: title-case with spaces
  return key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

// ── Apply labels to all <th> that have data-stat ────────────────────────────
function applyStatLabels() {
  document.querySelectorAll("th[data-stat]").forEach(th => {
    th.textContent = statLabel(th.dataset.stat);
    th.title = th.dataset.stat; // keep raw name accessible on hover
  });
}

// ── Make a table sortable ────────────────────────────────────────────────────
function makeSortable(table) {
  const ths = table.querySelectorAll("thead th");
  let lastCol = -1, lastDir = 1;

  ths.forEach((th, col) => {
    th.style.cursor = "pointer";
    th.setAttribute("title", (th.title ? th.title + " — " : "") + "click to sort");
    th.addEventListener("click", () => {
      const dir = (col === lastCol) ? -lastDir : 1;
      lastCol = col; lastDir = dir;

      ths.forEach(h => h.classList.remove("sort-asc", "sort-desc"));
      th.classList.add(dir === 1 ? "sort-asc" : "sort-desc");

      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows).filter(r => !r.classList.contains("week-group-row"));
      rows.sort((a, b) => {
        const av = cellVal(a.cells[col]);
        const bv = cellVal(b.cells[col]);
        return dir * (isNaN(av - bv) ? String(av).localeCompare(String(bv)) : av - bv);
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
}

function cellVal(cell) {
  if (!cell) return "";
  const v = cell.textContent.trim();
  const n = parseFloat(v);
  return isNaN(n) ? v : n;
}

// ── Add a filter input above a table ────────────────────────────────────────
function addFilter(table) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-controls";

  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = "Filter…";
  input.className = "table-filter";
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    const tbody = table.tBodies[0];
    Array.from(tbody.rows).forEach(row => {
      if (row.classList.contains("week-group-row")) return;
      const match = !q || row.textContent.toLowerCase().includes(q);
      row.style.display = match ? "" : "none";
    });
  });

  wrapper.appendChild(input);
  table.parentNode.insertBefore(wrapper, table);
  return wrapper;
}

// ── Column chooser ───────────────────────────────────────────────────────────
function addColumnChooser(table, controlsEl) {
  const ths = Array.from(table.querySelectorAll("thead th"));
  if (ths.length <= 3) return; // not worth it for tiny tables

  const btn = document.createElement("button");
  btn.className = "col-chooser-btn";
  btn.textContent = "Columns ▾";

  const panel = document.createElement("div");
  panel.className = "col-chooser-panel hidden";

  ths.forEach((th, i) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener("change", () => toggleColumn(table, i, cb.checked));
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + th.textContent.trim()));
    panel.appendChild(label);
  });

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    panel.classList.toggle("hidden");
  });
  document.addEventListener("click", () => panel.classList.add("hidden"));

  controlsEl.appendChild(btn);
  controlsEl.appendChild(panel);
}

function toggleColumn(table, colIdx, visible) {
  table.querySelectorAll(`tr`).forEach(row => {
    const cell = row.cells[colIdx];
    if (cell) cell.style.display = visible ? "" : "none";
  });
}

// ── Init all enhanced tables ─────────────────────────────────────────────────
function initTables() {
  applyStatLabels();

  document.querySelectorAll("table.data-table").forEach(table => {
    const controls = addFilter(table);
    addColumnChooser(table, controls);
    makeSortable(table);
  });
}

document.addEventListener("DOMContentLoaded", initTables);
