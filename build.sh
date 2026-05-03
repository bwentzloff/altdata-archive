#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "==> Parsing SQL ..."
.venv/bin/python pipeline/parse_sql.py

echo "==> Merging players ..."
.venv/bin/python pipeline/merge_players.py

echo "==> Building data files ..."
.venv/bin/python pipeline/build_data.py

echo "==> Generating HTML ..."
.venv/bin/python pipeline/generate_site.py

echo "==> Done. Site is in site/"
