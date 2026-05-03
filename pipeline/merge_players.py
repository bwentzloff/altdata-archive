"""
merge_players.py
Clusters player records by fuzzy name match (within the same sport/position
where possible) and produces a canonical players_merged.json.

Each canonical player has:
  - canonical_id: stable slug (e.g. "osirus-mitchell")
  - canonical_name: best display name
  - appearances: list of raw player rows that matched
  - positions: deduplicated list
  - ambiguous: True if same name+position matched records from incompatible
                leagues where they could be different people
"""

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

RAW = Path(__file__).parent / "raw"
OUT = Path(__file__).parent / "merged"
OUT.mkdir(exist_ok=True)

MERGE_THRESHOLD = 88   # token_sort_ratio score to consider a match
AMBIGUOUS_THRESHOLD = 72  # below this = definitely different person


def slugify(name):
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s-]", "", name).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    return name


def normalize_name(name):
    """Lowercase, strip punctuation, collapse whitespace."""
    n = name.lower()
    n = re.sub(r"['\.\-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def skill_position(pos):
    """Broad category for position — helps avoid merging same-name DE and WR."""
    offense_skill = {"QB", "RB", "FB", "WR", "TE", "K", "P", "LS"}
    offense_line = {"OL", "T", "G", "C"}
    defense = {"DL", "DE", "DT", "NT", "LB", "OLB", "ILB", "MLB",
                "CB", "SAF", "S", "FS", "SS", "DB"}
    special = {"K", "P", "LS"}
    p = (pos or "").upper()
    if p in offense_skill:
        return "offense_skill"
    if p in offense_line:
        return "offense_line"
    if p in defense:
        return "defense"
    return "other"


def _infer_aaf_position(stats):
    """Infer a rough position from scraped AAF season stat totals."""
    py   = float(stats.get("passing_yards",   0) or 0)
    ry   = float(stats.get("rushing_yards",   0) or 0)
    recy = float(stats.get("receiving_yards", 0) or 0)
    tack = float(stats.get("tackles",         0) or 0)
    if py > ry and py > recy:
        return "QB"
    if recy > 0 and recy >= ry:
        return "WR"
    if ry > 0:
        return "RB"
    if tack > 0:
        return "DB"
    return ""


def main():
    players = json.loads((RAW / "players.json").read_text())
    sports = json.loads((RAW / "sports.json").read_text())
    sport_map = {s["id"]: s for s in sports}

    # ── Inject AAF 2019 players from scraper output ──────────────────────
    aaf_season_file = RAW / "aaf_2019_season.json"
    if aaf_season_file.exists():
        aaf_season = json.loads(aaf_season_file.read_text())
        # Deduplicate by player URL (the stable footballdb key)
        seen_urls: dict = {}
        for entry in aaf_season:
            url = entry.get("player_url", "")
            if url and url not in seen_urls:
                seen_urls[url] = entry
        aaf_players = []
        for i, (url, entry) in enumerate(sorted(seen_urls.items())):
            pos = _infer_aaf_position(entry.get("stats", {}))
            synthetic_id = 100000 + i
            aaf_players.append({
                "id":         synthetic_id,
                "full_name":  entry["name"],
                "short_name": entry["name"],
                "first_name": entry["name"].split()[0] if entry["name"] else "",
                "last_name":  " ".join(entry["name"].split()[1:]) if entry["name"] else "",
                "position":   pos,
                "team":       entry.get("team_abbr", ""),
                "sport_id":   8,   # AAF
                "league":     "AAF",
                "_aaf_url":   url,
                # unused fields set to None so format matches SQL players
                "sportradar_id": None, "college": None, "jersey": None,
                "height": None, "weight": None, "college_stats": None,
            })
        # Write for build_data.py to pick up the URL→ID mapping
        (RAW / "aaf_players.json").write_text(
            json.dumps(aaf_players, indent=2), encoding="utf-8"
        )
        players.extend(aaf_players)
        print(f"Injected {len(aaf_players)} AAF players (IDs 100000–{100000+len(aaf_players)-1})")

    print(f"Loaded {len(players)} player records (including injected)")

    # Group by normalized name for fast candidate lookup
    by_norm_name = defaultdict(list)
    for p in players:
        key = normalize_name(p["full_name"])
        by_norm_name[key].append(p)

    # Union-Find for clustering
    id_to_idx = {p["id"]: i for i, p in enumerate(players)}
    parent = list(range(len(players)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # For each player, find fuzzy matches in the full name pool
    all_norm_names = list(by_norm_name.keys())
    print("Running fuzzy name clustering ...")

    merged_count = 0

    # Pass 1: merge records with the exact same normalized name and compatible positions
    for norm, group in by_norm_name.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                p1, p2 = group[i], group[j]
                if skill_position(p1.get("position")) != skill_position(p2.get("position")):
                    continue
                i1, i2 = id_to_idx[p1["id"]], id_to_idx[p2["id"]]
                if find(i1) != find(i2):
                    union(i1, i2)
                    merged_count += 1

    # Pass 2: fuzzy-merge records with similar but not identical names
    for norm, group in by_norm_name.items():
        if len(norm) < 4:
            continue
        # Find close matches among all normalized names
        matches = process.extract(
            norm,
            all_norm_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=MERGE_THRESHOLD,
            limit=10,
        )
        for match_name, score, _ in matches:
            if match_name == norm:
                continue
            if score < MERGE_THRESHOLD:
                continue
            # Candidate records
            for p1 in group:
                for p2 in by_norm_name[match_name]:
                    if p1["id"] == p2["id"]:
                        continue
                    # Only merge if same broad position group
                    if skill_position(p1.get("position")) != skill_position(p2.get("position")):
                        continue
                    i1 = id_to_idx[p1["id"]]
                    i2 = id_to_idx[p2["id"]]
                    if find(i1) != find(i2):
                        union(i1, i2)
                        merged_count += 1

    print(f"Merged {merged_count} player record pairs")

    # Build clusters
    clusters = defaultdict(list)
    for i, p in enumerate(players):
        clusters[find(i)].append(p)

    print(f"Produced {len(clusters)} canonical players")

    # Build canonical player objects
    canonical_players = []
    slug_counts = defaultdict(int)

    for root_idx, records in clusters.items():
        # Pick best name: prefer the one with the most complete data
        def record_score(r):
            score = 0
            sr = r.get("sportradar_id")
            if sr and isinstance(sr, str) and len(sr) > 10:
                score += 10
            if r.get("college"):
                score += 2
            if r.get("jersey") is not None:
                score += 1
            return score

        best = max(records, key=record_score)
        canonical_name = best["full_name"]
        base_slug = slugify(canonical_name)
        slug_counts[base_slug] += 1
        count = slug_counts[base_slug]
        canonical_id = base_slug if count == 1 else f"{base_slug}-{count}"

        # Collect all positions, leagues, sport_ids
        positions = list({r.get("position", "") for r in records if r.get("position")})
        leagues = list({r.get("league") for r in records if r.get("league")})
        sport_ids = list({r.get("sport_id") for r in records if r.get("sport_id")})
        sport_names = list({
            sport_map[sid]["name"] for sid in sport_ids if sid in sport_map
        })

        # Flag ambiguous: multiple records with differing positions (different person risk)
        unique_positions = {r.get("position", "").upper() for r in records}
        ambiguous = len(unique_positions) > 1 and any(
            skill_position(p1) != skill_position(p2)
            for p1 in unique_positions
            for p2 in unique_positions
            if p1 != p2
        )

        canonical_players.append({
            "canonical_id": canonical_id,
            "canonical_name": canonical_name,
            "positions": positions,
            "leagues": leagues,
            "sport_ids": sport_ids,
            "sport_names": sport_names,
            "ambiguous": ambiguous,
            "record_count": len(records),
            "appearances": [
                {
                    "id": r["id"],
                    "full_name": r["full_name"],
                    "team": r.get("team"),
                    "position": r.get("position"),
                    "sport_id": r.get("sport_id"),
                    "league": r.get("league"),
                    "jersey": r.get("jersey"),
                    "college": r.get("college"),
                    "college_stats": r.get("college_stats"),
                    "height": r.get("height"),
                    "weight": r.get("weight"),
                }
                for r in records
            ],
            # Map player_id -> canonical_id (for stat joins)
            "_raw_ids": [r["id"] for r in records],
        })

    # Write merged players
    out_path = OUT / "players_merged.json"
    out_path.write_text(json.dumps(canonical_players, indent=2), encoding="utf-8")
    print(f"Written {len(canonical_players)} canonical players -> {out_path}")

    # Write a flat id->canonical_id lookup for the stats pipeline
    id_lookup = {}
    for cp in canonical_players:
        for raw_id in cp["_raw_ids"]:
            id_lookup[str(raw_id)] = cp["canonical_id"]

    lookup_path = OUT / "id_to_canonical.json"
    lookup_path.write_text(json.dumps(id_lookup), encoding="utf-8")
    print(f"Written ID lookup -> {lookup_path}")


if __name__ == "__main__":
    main()
