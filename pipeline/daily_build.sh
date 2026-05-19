#!/usr/bin/env bash
# daily_build.sh — Incremental backfill + full site rebuild + git push.
#
# Run from any directory:   ./pipeline/daily_build.sh
# Or via cron:              0 4 * * * /Users/brian/Projects/altdata-archive/pipeline/daily_build.sh
#
# What it does each run:
#   0. Export fresh SQL        — mysqldump players/stats/sports/games from DB
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

# Pull any commits made elsewhere before regenerating, so the deploy box
# stays in sync with origin/main and avoids diverged-history push failures.
git pull --no-rebase

# ── 0. Export fresh SQL from the database ─────────────────────────────────────
echo "── Database export ─────────────────────────────────────"
$PYTHON pipeline/export_db.py
echo ""

# ── 1. College stats backfill ────────────────────────────────────────────────
echo "── College stats (footballdb.com FBS) ──────────────────"
$PYTHON pipeline/scrape_college.py --batch 20
echo ""

# ── 2. NFL stats backfill ────────────────────────────────────────────────────
echo "── NFL stats (ESPN API) ────────────────────────────────"
$PYTHON pipeline/scrape_nfl.py --batch 30
echo ""

# ── 3. CFL historical backfill (2019, 2021, 2022) ────────────────────────────
echo "── CFL historical (footballdb.com) ─────────────────────"
$PYTHON pipeline/scrape_cfl.py --batch 5
echo ""

# ── 3b. ELF historical backfill (2021, 2022) ─────────────────────────────────
# Data comes from cached Wayback snapshot of sportsmetrics.football
# Re-run only refreshes from cache (no network call if cache exists)
echo "── ELF historical (sportsmetrics.football via Wayback) ─"
$PYTHON pipeline/scrape_elf.py
echo ""

# ── 3c. NLL historical (2019-20 through 2023-24) ─────────────────────────────
echo "── NLL historical (nll.com player pages) ───────────────"
$PYTHON pipeline/scrape_nll_historical.py --batch 5
echo ""

# ── 3d. PLL (Premier Lacrosse League, 2019+) ─────────────────────────────────
echo "── PLL (stats.premierlacrosseleague.com / Wayback) ─────"
$PYTHON pipeline/scrape_pll.py
echo ""

# ── 3e. PUL (Premier Ultimate League, 2022-present) ──────────────────────────
echo "── PUL (pul-stats-hub.pages.dev player pages) ──────────"
$PYTHON pipeline/scrape_pul.py --batch 10
echo ""

# ── 3f. FCF (Fan Controlled Football, 2021-2023) ─────────────────────────────
echo "── FCF (fcf.io / Wayback) ──────────────────────────────"
$PYTHON pipeline/scrape_fcf.py
echo ""

# ── 3g. Athletes Unlimited (Softball/Lacrosse/Basketball/Volleyball) ─────────
echo "── Athletes Unlimited (auprosports.com / Wayback) ──────"
$PYTHON pipeline/scrape_au.py
echo ""

# ── 3h. DGPT (Disc Golf Pro Tour, 2019-2024 via pdga.com) ────────────────────
# NOTE: No --batch limit because cache is incomplete; must fetch until complete
# even though PDGA rate-limits (HTTP 429). Uses exponential backoff retry logic.
echo "── DGPT (pdga.com/players/stats) ───────────────────────"
#$PYTHON pipeline/scrape_dgpt.py
echo "You commented this out for now"
echo ""

# NOTE: Arena Football League (arenafan.com) is blocked (403) for all automated
# requests.  AFL data from the source SQL is already in the system.
# Revisit if a Playwright-based scraper is added later.

# ── 3i. IFL (Indoor Football League) ─────────────────────────────────────────
# Wikipedia scraper covers historical season totals (2009-2025).
# Official goifl.com scraper (PrestoSports printable decorator via Googlebot UA)
# covers the current season's boxscores.
echo "── IFL (Wikipedia historical + goifl.com current) ───────"
$PYTHON pipeline/scrape_ifl.py --batch 2
$PYTHON pipeline/scrape_ifl_official.py
echo ""

# ── 3j. AF1 (Arena Football One, official DigitalShift site) ─────────────────
echo "── AF1 (official DigitalShift site) ─────────────────────"
$PYTHON pipeline/scrape_af1.py
echo ""

# ── 3k. NAL (National Arena League, Wikipedia season articles) ───────────────
echo "── NAL (Wikipedia season articles) ──────────────────────"
$PYTHON pipeline/scrape_nal.py
echo ""

# ── 3k. LFA (Liga de Fútbol Americano Mexico, embedded Google Sheets) ────────
echo "── LFA (lfa.mx stats pages) ────────────────────────────"
$PYTHON pipeline/scrape_lfa.py --batch 2
echo ""

# ── 3l. X-League Japan (Wikipedia) ───────────────────────────────────────────
echo "── X-League Japan (Wikipedia) ──────────────────────────"
$PYTHON pipeline/scrape_xleague.py
echo ""
# ── 3m. Player social media (ESPN, leagues, Wikipedia) ──────────────────────
echo "── Player social media (ESPN/league sites/Wikipedia) ───"
$PYTHON pipeline/scrape_player_socials.py --batch 10
echo ""
# ── 3. Merge players ─────────────────────────────────────────────────────────
echo "── Player images (Wikimedia Commons, ~1 min) ───────────"
$PYTHON pipeline/scrape_images.py --max-seconds 60
echo ""

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
