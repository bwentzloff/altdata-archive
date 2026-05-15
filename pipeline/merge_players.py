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


def _is_suffix(token):
    """True for name suffixes like Jr., Sr., II, III, IV, V — but NOT bare 'JR'/'SR' (those are first names)."""
    t = token.strip()
    # Roman numeral suffixes
    if t in ("II", "III", "IV", "V"):
        return True
    # Must end with period to count as Jr./Sr. abbreviation
    t_lower = t.lower()
    return t_lower in ("jr.", "sr.")


def flip_name(name):
    """
    Normalize inverted CFL-style names to 'FirstName LastName [Suffix]'.

    Handles:
      'Abbott, Samson'          → 'Samson Abbott'
      'Adams Jr., Vernon'       → 'Vernon Adams Jr.'
      'Allen, Jr., Will'        → 'Will Allen Jr.'
      'Steven Mitchell, Jr.'    → 'Steven Mitchell Jr.'  (comma-before-suffix strip)
    Leaves names without a comma unchanged.
    """
    if not name:
        return ""
    if "," not in name:
        return name

    parts = [p.strip() for p in name.split(",")]

    # Separate suffix tokens from name tokens
    suffixes   = [p for p in parts if _is_suffix(p)]
    non_suffix = [p for p in parts if not _is_suffix(p)]

    suffix_str = " ".join(suffixes)

    if len(non_suffix) == 2:
        # Standard "Last, First" inversion — flip it
        last, first = non_suffix[0], non_suffix[1]
        result = f"{first} {last}"
    elif len(non_suffix) == 1:
        # "FirstName LastName, Jr." — name already correct, just drop the comma
        result = non_suffix[0]
    else:
        # Multi-part ambiguous — join non-suffix tokens, first is last item
        last  = non_suffix[0]
        first = " ".join(non_suffix[1:])
        result = f"{first} {last}"

    if suffix_str:
        result += f" {suffix_str}"
    return result


def normalize_name(name):
    """Lowercase, strip punctuation, collapse whitespace."""
    n = flip_name(name).lower()
    # Normalize typographic/curly apostrophes and quotes to straight apostrophe
    # so "Ta\u2019amu" and "Ta'amu" merge into the same cluster.
    n = n.replace('\u2019', "'").replace('\u2018', "'").replace('\u02bc', "'")
    # Collapse dotted initials before general punctuation strip so
    # "C.J." → "cj" (not "c j") and "T.J." → "tj"
    n = re.sub(r"\b([a-z])\.([a-z])\.", r"\1\2", n)
    n = re.sub(r"[',\.\-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def skill_position(pos):
    """Broad category for position — helps avoid merging same-name players from incompatible roles."""
    p = (pos or "").upper()
    if p == "QB":
        return "quarterback"
    if p in {"RB", "FB", "WR", "TE"}:
        return "offense_skill"
    if p in {"K", "P", "LS"}:
        return "kicker_special"
    if p in {"OL", "T", "G", "C"}:
        return "offense_line"
    if p in {"DL", "DE", "DT", "NT", "LB", "OLB", "ILB", "MLB",
             "CB", "SAF", "S", "FS", "SS", "DB"}:
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

    # ── Inject CFL historical players from scraper output ─────────────────
    cfl_historical_file = RAW / "cfl_historical_players.json"
    if cfl_historical_file.exists():
        cfl_players = json.loads(cfl_historical_file.read_text())
        players.extend(cfl_players)
        if cfl_players:
            ids = [p["id"] for p in cfl_players]
            print(f"Injected {len(cfl_players)} CFL historical players (IDs {min(ids)}–{max(ids)})")

    # ── Inject ELF historical players from scraper output ─────────────────
    elf_historical_file = RAW / "elf_historical_players.json"
    if elf_historical_file.exists():
        elf_players = json.loads(elf_historical_file.read_text())
        players.extend(elf_players)
        if elf_players:
            ids = [p["id"] for p in elf_players]
            print(f"Injected {len(elf_players)} ELF historical players (IDs {min(ids)}–{max(ids)})")

    # ── Inject new league players from individual scrapers ─────────────────
    _new_league_files = [
        ("nll_historical_players.json", "NLL historical"),
        ("pll_players.json",            "PLL"),
        ("pul_players.json",            "PUL"),
        ("fcf_players.json",            "FCF"),
        ("xfl_2020_players.json",       "XFL 2020"),
        ("nal_players.json",            "NAL"),
        ("au_players.json",             "Athletes Unlimited"),
        ("dgpt_players.json",           "DGPT"),
    ]
    for fname, label in _new_league_files:
        _f = RAW / fname
        if _f.exists():
            _ps = json.loads(_f.read_text())
            if _ps:
                players.extend(_ps)
                ids = [p["id"] for p in _ps]
                print(f"Injected {len(_ps)} {label} players (IDs {min(ids)}–{max(ids)})")

    # ── Inject coaching staff from scraper output ─────────────────────────
    coaches_file = RAW / "football_coaches.json"
    coaches_list = []
    if coaches_file.exists():
        coaches_list = json.loads(coaches_file.read_text())
        ids = [c["id"] for c in coaches_list]
        print(f"Loaded {len(coaches_list)} coaches (IDs {min(ids) if ids else 'none'}–{max(ids) if ids else 'none'})")

    print(f"Loaded {len(players)} player records + {len(coaches_list)} coaches (including injected)")

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

    def positions_compatible(p1, p2):
        """Return True if the two records could be the same person based on position.
        'other' (unknown/empty) is treated as compatible with any position."""
        s1 = skill_position(p1.get("position"))
        s2 = skill_position(p2.get("position"))
        return s1 == "other" or s2 == "other" or s1 == s2

    # Pass 1: merge records with the exact same normalized name and compatible positions
    for norm, group in by_norm_name.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                p1, p2 = group[i], group[j]
                if not positions_compatible(p1, p2):
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
            # When last names are identical, the full-name score is inflated by the
            # shared suffix — apply a stricter first-name similarity check.
            norm_tokens   = norm.split()
            match_tokens  = match_name.split()
            if (len(norm_tokens) >= 2 and len(match_tokens) >= 2
                    and norm_tokens[-1] == match_tokens[-1]):
                first_a = " ".join(norm_tokens[:-1])
                first_b = " ".join(match_tokens[:-1])
                if fuzz.ratio(first_a, first_b) < 85:
                    continue  # first names too different — skip
            # Candidate records
            for p1 in group:
                for p2 in by_norm_name[match_name]:
                    if p1["id"] == p2["id"]:
                        continue
                    # Only merge if same broad position group
                    if not positions_compatible(p1, p2):
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
        canonical_name = flip_name(best["full_name"])   # ensure "First Last" order
        base_slug = slugify(canonical_name)
        slug_counts[base_slug] += 1
        count = slug_counts[base_slug]
        canonical_id = base_slug if count == 1 else f"{base_slug}-{count}"

        # Collect all positions, leagues, sport_ids
        positions = sorted({r.get("position", "") for r in records if r.get("position")})
        leagues = sorted({r.get("league") for r in records if r.get("league")})
        sport_ids = sorted({r.get("sport_id") for r in records if r.get("sport_id")})
        sport_names = sorted({
            sport_map[sid]["name"] for sid in sport_ids if sid in sport_map
        })

        # Flag ambiguous: multiple records with differing positions (different person risk)
        unique_positions = {(r.get("position") or "").upper() for r in records}
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

    # ──────────────────────────────────────────────────────────────────────
    # Coach merging — same logic as players but output to coaches_merged.json
    # ──────────────────────────────────────────────────────────────────────

    if coaches_list:
        print("\n=== MERGING COACHES ===\n")

        # Group coaches by normalized name
        coaches_by_norm_name = defaultdict(list)
        for c in coaches_list:
            key = normalize_name(c["full_name"])
            coaches_by_norm_name[key].append(c)

        # Union-Find for coach clustering
        coach_id_to_idx = {c["id"]: i for i, c in enumerate(coaches_list)}
        coach_parent = list(range(len(coaches_list)))

        def coach_find(x):
            while coach_parent[x] != x:
                coach_parent[x] = coach_parent[coach_parent[x]]
                x = coach_parent[x]
            return x

        def coach_union(x, y):
            px, py = coach_find(x), coach_find(y)
            if px != py:
                coach_parent[px] = py

        # Coaches should be matched more strictly — same name and same/compatible role
        all_coach_norms = list(coaches_by_norm_name.keys())
        coach_merged_count = 0

        # Pass 1: exact name + role matches
        for norm, group in coaches_by_norm_name.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    c1, c2 = group[i], group[j]
                    # Merge if same name and role (head coaches, coordinators, etc.)
                    role1 = c1.get("position", "").lower()
                    role2 = c2.get("position", "").lower()
                    if role1 == role2:
                        i1, i2 = coach_id_to_idx[c1["id"]], coach_id_to_idx[c2["id"]]
                        if coach_find(i1) != coach_find(i2):
                            coach_union(i1, i2)
                            coach_merged_count += 1

        # Pass 2: fuzzy match similar names (less strict than players)
        for norm, group in coaches_by_norm_name.items():
            if len(norm) < 4:
                continue
            matches = process.extract(
                norm,
                all_coach_norms,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=95,  # Higher threshold for coaches
                limit=5,
            )
            for match_name, score, _ in matches:
                if match_name == norm:
                    continue
                for c1 in group:
                    for c2 in coaches_by_norm_name[match_name]:
                        if c1["id"] == c2["id"]:
                            continue
                        role1 = c1.get("position", "").lower()
                        role2 = c2.get("position", "").lower()
                        if role1 == role2:
                            i1 = coach_id_to_idx[c1["id"]]
                            i2 = coach_id_to_idx[c2["id"]]
                            if coach_find(i1) != coach_find(i2):
                                coach_union(i1, i2)
                                coach_merged_count += 1

        print(f"Merged {coach_merged_count} coach record pairs")

        # Build coach clusters
        coach_clusters = defaultdict(list)
        for i, c in enumerate(coaches_list):
            coach_clusters[coach_find(i)].append(c)

        print(f"Produced {len(coach_clusters)} canonical coaches")

        # Build canonical coach objects
        canonical_coaches = []
        coach_slug_counts = defaultdict(int)

        for root_idx, records in coach_clusters.items():
            # Pick best record (prefer one with complete data)
            best = max(records, key=lambda r: len(r.get("full_name", "")))
            canonical_name = flip_name(best["full_name"])
            base_slug = slugify(canonical_name)
            coach_slug_counts[base_slug] += 1
            count = coach_slug_counts[base_slug]
            canonical_id = base_slug if count == 1 else f"{base_slug}-{count}"

            # Collect roles, leagues
            roles = sorted({r.get("position", "") for r in records if r.get("position")})
            leagues = sorted({r.get("league") for r in records if r.get("league")})
            years = sorted(set(r.get("_year") for r in records if r.get("_year")))

            canonical_coaches.append({
                "canonical_id": canonical_id,
                "canonical_name": canonical_name,
                "roles": roles,
                "leagues": leagues,
                "years": years,
                "record_count": len(records),
                "appearances": [
                    {
                        "id": r["id"],
                        "full_name": r["full_name"],
                        "team": r.get("team"),
                        "role": r.get("position"),
                        "league": r.get("league"),
                        "year": r.get("_year"),
                    }
                    for r in records
                ],
                "_raw_ids": [r["id"] for r in records],
            })

        # Write merged coaches
        coaches_out_path = OUT / "coaches_merged.json"
        coaches_out_path.write_text(
            json.dumps(canonical_coaches, indent=2), encoding="utf-8"
        )
        print(f"Written {len(canonical_coaches)} canonical coaches -> {coaches_out_path}")

        # Write coach ID lookup
        coach_id_lookup = {}
        for cc in canonical_coaches:
            for raw_id in cc["_raw_ids"]:
                coach_id_lookup[str(raw_id)] = cc["canonical_id"]

        coach_lookup_path = OUT / "id_to_canonical_coaches.json"
        coach_lookup_path.write_text(json.dumps(coach_id_lookup), encoding="utf-8")
        print(f"Written coach ID lookup -> {coach_lookup_path}")
    else:
        print("\nNo coaches data found.")


if __name__ == "__main__":
    main()
