#!/usr/bin/env bash
# Full build pipeline with all scrapers
set -e
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  AltSports Archive — Full Build  $(date "+%Y-%m-%d %H:%M:%S")  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

echo "==> Export fresh SQL from database ..."
$PYTHON pipeline/export_db.py || echo "WARN: Database export failed, continuing..."

echo "==> Parse SQL ..."
$PYTHON pipeline/parse_sql.py

echo "==> College stats backfill (footballdb.com) ..."
$PYTHON pipeline/scrape_college.py --batch 20 || echo "WARN: College scraper failed, continuing..."

echo "==> NFL stats backfill (ESPN API) ..."
$PYTHON pipeline/scrape_nfl.py --batch 200 || echo "WARN: NFL scraper failed, continuing..."

#echo "==> AAF historical backfill (footballdb.com) ..."
#$PYTHON pipeline/scrape_aaf.py --batch 5 || echo "WARN: AAF scraper failed, continuing..."

echo "==> CFL historical ..."
$PYTHON pipeline/scrape_cfl.py --batch 5 || echo "WARN: CFL scraper failed, continuing..."

echo "==> ELF historical ..."
$PYTHON pipeline/scrape_elf.py || echo "WARN: ELF scraper failed, continuing..."

echo "==> NLL historical ..."
$PYTHON pipeline/scrape_nll_historical.py --batch 5 || echo "WARN: NLL scraper failed, continuing..."

echo "==> PLL ..."
$PYTHON pipeline/scrape_pll.py || echo "WARN: PLL scraper failed, continuing..."

echo "==> PUL ..."
$PYTHON pipeline/scrape_pul.py --batch 10 || echo "WARN: PUL scraper failed, continuing..."

echo "==> FCF ..."
$PYTHON pipeline/scrape_fcf.py || echo "WARN: FCF scraper failed, continuing..."

echo "==> Athletes Unlimited ..."
$PYTHON pipeline/scrape_au.py || echo "WARN: AU scraper failed, continuing..."

echo "==> AAF gamelogs ..."
$PYTHON pipeline/scrape_aaf_gamelogs.py || echo "WARN: AAF gamelog scraper failed, continuing..."

echo "==> XFL 2020 historical ..."
$PYTHON pipeline/scrape_xfl_2020.py || echo "WARN: XFL 2020 scraper failed, continuing..."

echo "==> IFL ..."
$PYTHON pipeline/scrape_ifl.py --batch 2 || echo "WARN: IFL scraper failed, continuing..."

echo "==> IFL (official goifl.com) ..."
$PYTHON pipeline/scrape_ifl_official.py || echo "WARN: IFL official scraper failed, continuing..."

echo "==> AF1 ..."
$PYTHON pipeline/scrape_af1.py || echo "WARN: AF1 scraper failed, continuing..."

echo "==> NAL ..."
$PYTHON pipeline/scrape_nal.py || echo "WARN: NAL scraper failed, continuing..."

echo "==> LFA ..."
$PYTHON pipeline/scrape_lfa.py --batch 2 || echo "WARN: LFA scraper failed, continuing..."

echo "==> X-League Japan ..."
$PYTHON pipeline/scrape_xleague.py --all-pdf || echo "WARN: X-League scraper failed, continuing..."

echo "==> Player social media ..."
$PYTHON pipeline/scrape_player_socials.py --batch 10 || echo "WARN: Player socials scraper failed, continuing..."

echo "==> Player images ..."
$PYTHON pipeline/scrape_images.py --max-seconds 60 || echo "WARN: Image scraper failed, continuing..."

echo "==> Player news via Google News RSS ..."
$PYTHON pipeline/scrape_player_news.py --entries-per-feed 1000 || echo "WARN: News scraper failed, continuing..."

echo "==> Merge players ..."
$PYTHON pipeline/merge_players.py

echo "==> Build data files ..."
$PYTHON pipeline/build_data.py

echo "==> Run studies ..."
$PYTHON pipeline/build_studies.py || echo "WARN: Studies build failed, continuing..."

echo "==> Generate HTML ..."
$PYTHON pipeline/generate_site.py

# Ensure docs/ exists and add .nojekyll to skip Jekyll processing
mkdir -p docs
touch docs/.nojekyll

echo ""
echo "==> Build complete! Site is in docs/"
