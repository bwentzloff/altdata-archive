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

# docs/data/players.json and players.xml
git status --short | grep -v "^??" | awk '{print $2}' | grep -E "^docs/data/players\.(json|xml)$" | xargs -r git add
commit_if_staged "build: refresh players aggregate json/xml"

# docs/games HTML - remaining (soccer, existing modified)
git status --short | grep -v "^??" | awk '{print $2}' | grep -E "^docs/games/" | grep -E "^docs/games/soccer-" | head -200 | xargs -r git add
commit_if_staged "build: refresh games html soccer"

git status --short | grep -v "^??" | awk '{print $2}' | grep -E "^docs/games/" | xargs -r git add
commit_if_staged "build: refresh games html existing"

# docs/data/players by alpha - narrow ranges
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  files=$(git status --short | grep -v "^??" | awk '{print $2}' | grep -E "^docs/data/players/${letter}" | head -200)
  if [ -n "$files" ]; then
    echo "$files" | xargs git add
    commit_if_staged "build: refresh data players ${letter}"
  fi
done

# docs/players HTML by alpha - narrow ranges
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  files=$(git status --short | grep -v "^??" | awk '{print $2}' | grep -E "^docs/players/${letter}" | head -200)
  if [ -n "$files" ]; then
    echo "$files" | xargs git add
    commit_if_staged "build: refresh players html ${letter}"
  fi
done

echo ""
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..main)"
git status --short | grep -v "^??" | wc -l
