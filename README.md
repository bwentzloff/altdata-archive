# AltSports Archive — Build

## Setup (first time)
```bash
python3 -m venv .venv
.venv/bin/pip install rapidfuzz jinja2
```

## Full rebuild from SQL
```bash
.venv/bin/python pipeline/parse_sql.py      # SQL → raw JSON
.venv/bin/python pipeline/merge_players.py  # fuzzy-merge player records
.venv/bin/python pipeline/build_data.py     # aggregate stats, write site/data/
.venv/bin/python pipeline/generate_site.py  # render HTML pages
```

Or just run:
```bash
bash build.sh
```

## Deploy to GitHub Pages
The `site/` directory is the publish root.
Set GitHub Pages source to the `site/` folder (or move it to `docs/` if preferred).
CNAME is set to `archive.altfantasysports.com`.

## Adding new data
- Export new SQL and replace / append to `forarchive.sql`
- Re-run the full rebuild above
- Commit and push `site/`
