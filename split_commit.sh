#!/usr/bin/env bash
set -e
cd /Users/brian/Projects/altdata-archive

commit_if_staged() {
  local msg="$1"
  if ! git diff --cached --quiet || git status --short | grep -q "^A "; then
    git commit -m "$msg"
    echo "✓ committed: $msg"
  else
    echo "  (nothing staged for: $msg)"
  fi
}

# --- docs/data/leagues (65 files, split by letter group) ---
for rng in "docs/data/leagues/[g-h]*" "docs/data/leagues/[i-l]*" "docs/data/leagues/[m]*" "docs/data/leagues/[n-p]*" "docs/data/leagues/[q-s]*" "docs/data/leagues/[t-z]*"; do
  git add -- $rng 2>/dev/null || true
  lbl="${rng//docs\/data\/leagues\//}"
  commit_if_staged "build: refresh data leagues ${lbl}"
done

# --- docs/data/search_index.json and player-images.json ---
git add docs/data/search_index.json docs/data/player-images.json
commit_if_staged "build: refresh search index and player images"

# --- docs/data/games by sport token ---
for token in cricket-bbl cricket-bpl cricket-cpl cricket-hnd cricket-ilt20 cricket-ipl cricket-lpl cricket-mlc cricket-npl cricket-odi cricket-psl cricket-sa20 cricket-t20i cricket-tests cricket-wbbl cricket-wpl soccer-mls soccer-nwsl soccer-usl curling-events; do
  git add -- "docs/data/games/${token}-"* 2>/dev/null || true
  git add -- "docs/data/games/${token}."* 2>/dev/null || true
  commit_if_staged "build: refresh data games ${token}"
done

# --- docs/data/games remaining (non-new-sport, modified existing) ---
git add docs/data/games/
commit_if_staged "build: refresh data games existing"

# --- docs/games HTML by sport token ---
for token in cricket soccer curling; do
  git add -- "docs/games/${token}-"* 2>/dev/null || true
  commit_if_staged "build: refresh games html ${token}"
done

# --- docs/games HTML remaining ---
git add docs/games/
commit_if_staged "build: refresh games html existing"

# --- docs/data/players by narrow alpha ranges ---
for rng in "a" "b" "c" "d" "e-f" "g" "h" "i-j" "k" "l" "m" "n-o" "p" "q-r" "s" "t-u" "v-z"; do
  pat="docs/data/players/[${rng}]*"
  git add -- $pat 2>/dev/null || true
  commit_if_staged "build: refresh data players ${rng}"
done

# --- docs/players HTML by narrow alpha ranges ---
for rng in "a" "b" "c" "d" "e-f" "g" "h" "i-j" "k" "l" "m" "n-o" "p" "q-r" "s" "t-u" "v-z"; do
  pat="docs/players/[${rng}]*"
  git add -- $pat 2>/dev/null || true
  commit_if_staged "build: refresh players html ${rng}"
done

# --- remaining players.json, players.xml ---
git add docs/data/players.json docs/data/players.xml 2>/dev/null || true
commit_if_staged "build: refresh players aggregate json/xml"

echo ""
echo "All commits created. Commits ahead of origin/main:"
git rev-list --count origin/main..main
