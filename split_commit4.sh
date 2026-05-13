#!/usr/bin/env bash
set -e
cd /Users/brian/Projects/altdata-archive

commit_if_staged() {
  local msg="$1"
  if ! git diff --cached --quiet; then
    git commit -m "$msg"
    echo "✓ committed: $msg"
  else
    echo "  (nothing staged for: $msg)"
  fi
}

# New docs/games HTML by sport token
for token in cricket-bbl cricket-bpl cricket-cpl cricket-hnd cricket-ilt20 cricket-ipl cricket-lpl cricket-mlc cricket-npl cricket-odi cricket-psl cricket-sa20 cricket-t20i cricket-tests cricket-wbbl cricket-wpl soccer-mls soccer-nwsl soccer-usl curling-curling curling-cz; do
  git add -- "docs/games/${token}-"* 2>/dev/null || true
  commit_if_staged "build: add games html ${token}"
done

# Remaining new docs/games HTML
git add -- docs/games/ 2>/dev/null || true
commit_if_staged "build: add games html remaining"

# New docs/players HTML per letter
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  git add -- "docs/players/${letter}"* 2>/dev/null || true
  commit_if_staged "build: add players html new ${letter}"
done

# New docs/data/players per letter (cricket/soccer players)
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  git add -- "docs/data/players/${letter}"* 2>/dev/null || true
  commit_if_staged "build: add data players new ${letter}"
done

echo ""
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..main)"
git status --short | wc -l
