#!/usr/bin/env bash
# daily_build.sh — Incremental backfill + full site rebuild + git push.
#
# Run from any directory:   ./pipeline/daily_build.sh
# Or via cron:              0 4 * * * /Users/brian/Projects/altdata-archive/pipeline/daily_build.sh
#
# What it does each run:
#   1. College stats backfill  — footballdb.com FBS pages (20 per run)
#   2. NFL stats backfill      — ESPN public API (30 players per run)
#   3. Merge players           — cluster raw records into canonical players
#   4. Build data files        — aggregate stats, write docs/data/
#   5. Generate site HTML      — render Jinja2 templates → docs/
#   6. Commit + push docs/     — if any files changed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PYTHON=".venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: virtualenv not found at $PROJECT_DIR/.venv" >&2
    echo "       Run:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

echo "╔══════════════════════════════════════════════════════╗"
echo "║  AltSports Archive — daily build  $(date '+%Y-%m-%d %H:%M:%S')  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. College stats backfill ────────────────────────────────────────────────
echo "── College stats (footballdb.com FBS) ──────────────────"
$PYTHON pipeline/scrape_college.py --batch 20
echo ""

# ── 2. NFL stats backfill ────────────────────────────────────────────────────
echo "── NFL stats (ESPN API) ────────────────────────────────"
$PYTHON pipeline/scrape_nfl.py --batch 30
echo ""

# NOTE: Arena Football League (arenafan.com) is blocked (403) for all automated
# requests.  AFL data from the source SQL is already in the system.
# Revisit if a Playwright-based scraper is added later.

# ── 3. Merge players ─────────────────────────────────────────────────────────
echo "── Merge players ───────────────────────────────────────"
$PYTHON pipeline/merge_players.py
echo ""

# ── 4. Build data files ──────────────────────────────────────────────────────
echo "── Build data ──────────────────────────────────────────"
$PYTHON pipeline/build_data.py
echo ""

# ── 5. Generate site HTML ────────────────────────────────────────────────────
echo "── Generate site ───────────────────────────────────────"
$PYTHON pipeline/generate_site.py
echo ""

# ── 6. Commit and push if docs/ changed ─────────────────────────────────────
echo "── Git ─────────────────────────────────────────────────"
if git diff --quiet docs/; then
    echo "No changes in docs/ — nothing to commit."
else
    TODAY="$(date '+%Y-%m-%d')"
    git add docs/
    git commit -m "Daily build ${TODAY}: backfill + rebuild"
    git push
    echo "Pushed to origin."
fi

echo ""
echo "=== Done at $(date '+%H:%M:%S') ==="
