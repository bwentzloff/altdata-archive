#!/usr/bin/env bash
set -e
cd /Users/brian/Projects/altdata-archive

commit_if_staged() {
  local msg="$1"
  if ! git diff --cached --quiet || git diff --cached --name-only | grep -q .; then
    git commit -m "$msg"
    echo "✓ committed: $msg"
  else
    echo "  (nothing staged for: $msg)"
  fi
}

# 1. Core pipeline/template/study changes (small)
git add build.sh pipeline/scrape_nfl.py pipeline/build_studies.py pipeline/studies/ \
    templates/studies_index.html templates/study.html \
    docs/gamecenter.html docs/index.html docs/llms.txt \
    docs/data/studies/ docs/studies/ 2>/dev/null || true
commit_if_staged "Add Study 3 (teammate density), fix Study 2 copy, NFL retry logic, studies nav"

# 2. New player pages (untracked)
git add docs/players/anik-regan.html docs/players/chris-givens.html \
    docs/players/elly-fireside-ostergaard.html docs/players/erica-baken.html \
    docs/players/melissa-lafrance.html docs/players/mia-beeman-weber.html \
    docs/players/mickael-cote-2.html docs/players/rashad-carmichael.html \
    docs/players/robert-porter.html docs/players/sarah-meckstroth.html 2>/dev/null || true
commit_if_staged "build: add new player pages"

# 3. docs/players a-d
git add -- "docs/players/a"* "docs/players/b"* "docs/players/c"* "docs/players/d"* 2>/dev/null || true
commit_if_staged "build: update player pages a-d"

# 4. docs/players e-j
git add -- "docs/players/e"* "docs/players/f"* "docs/players/g"* "docs/players/h"* "docs/players/i"* "docs/players/j"* 2>/dev/null || true
commit_if_staged "build: update player pages e-j"

# 5. docs/players k-q
git add -- "docs/players/k"* "docs/players/l"* "docs/players/m"* "docs/players/n"* "docs/players/o"* "docs/players/p"* "docs/players/q"* 2>/dev/null || true
commit_if_staged "build: update player pages k-q"

# 6. docs/players r-z + numeric
git add -- docs/players/ 2>/dev/null || true
commit_if_staged "build: update player pages r-z"

echo ""
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..main)"
echo "Now running push_commits.sh ..."
echo ""
./push_commits.sh
