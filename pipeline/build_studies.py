"""
build_studies.py
Run all registered studies and write their JSON payloads + history.

Outputs:
  docs/data/studies/index.json
  docs/data/studies/<slug>.json          ← latest snapshot
  docs/data/studies/<slug>-history.json  ← appended history of snapshots

Run after build_data.py and before generate_site.py.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from studies import STUDIES

ROOT_DIR    = Path(__file__).parent.parent
DATA_DIR    = ROOT_DIR / "docs" / "data"
STUDIES_DIR = DATA_DIR / "studies"


def _atomic_write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def main():
    STUDIES_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    index_entries = []
    for mod in STUDIES:
        slug = mod.SLUG
        print(f"==> Study: {slug}")
        payload = mod.compute(DATA_DIR)

        meta = {
            "slug":        slug,
            "title":       mod.TITLE,
            "subtitle":    mod.SUBTITLE,
            "category":    getattr(mod, "CATEGORY", "Other"),
            "tags":        list(getattr(mod, "TAGS", [])),
            "computed_at": now,
        }

        snapshot = {**meta, **payload}
        _atomic_write(STUDIES_DIR / f"{slug}.json", snapshot)

        # Append to history (skip the heavy `sections`/`methodology` HTML).
        hist_path = STUDIES_DIR / f"{slug}-history.json"
        if hist_path.exists():
            try:
                history = json.loads(hist_path.read_text())
            except json.JSONDecodeError:
                history = {"slug": slug, "snapshots": []}
        else:
            history = {"slug": slug, "snapshots": []}

        new_row = {
            "computed_at": now,
            **(payload.get("history_row") or {}),
        }
        # Only append if last row differs (avoid spamming history with identical builds)
        last = history["snapshots"][-1] if history["snapshots"] else None
        if last and {k: v for k, v in last.items() if k != "computed_at"} == \
                    {k: v for k, v in new_row.items() if k != "computed_at"}:
            print("    (history unchanged — not appended)")
        else:
            history["snapshots"].append(new_row)
            _atomic_write(hist_path, history)
            print(f"    history snapshots: {len(history['snapshots'])}")

        index_entries.append(meta)

    _atomic_write(STUDIES_DIR / "index.json", {"studies": index_entries})
    print(f"==> {len(index_entries)} studies written to {STUDIES_DIR}")


if __name__ == "__main__":
    main()
