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

# Stage and commit modified+new docs/data/players files per letter
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  # Use git add with path prefix (handles all files including unicode)
  git add -- "docs/data/players/${letter}"* 2>/dev/null || true
  # Also catch any quoted/unicode names starting with this letter via git add -A on that prefix
  git add "docs/data/players/${letter}" 2>/dev/null || true
  commit_if_staged "build: refresh data players overflow ${letter}"
done

# Stage and commit new docs/players HTML files per letter
for letter in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
  git add -- "docs/players/${letter}"* 2>/dev/null || true
  commit_if_staged "build: refresh players html overflow ${letter}"
done

echo ""
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..main)"
git status --short | grep -v "^??" | wc -l
echo "Untracked in docs/:"
git status --short | grep "^??" | grep "^?? docs/" | wc -l
