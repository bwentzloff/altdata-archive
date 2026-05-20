import json
from pathlib import Path

# Remove X-League player entries that only have 'wikipedia_awards' as their source
# and output a filtered file (overwriting the original)

INFILE = Path(__file__).parent / "raw" / "xleague_players.json"

with open(INFILE, "r", encoding="utf-8") as f:
    players = json.load(f)

filtered = [
    p for p in players
    if any(s != "wikipedia_awards" for s in p.get("_sources", []))
]

with open(INFILE, "w", encoding="utf-8") as f:
    json.dump(filtered, f, indent=2, ensure_ascii=False)

print(f"Filtered: {len(players) - len(filtered)} Wikipedia-only entries removed. {len(filtered)} remain.")
