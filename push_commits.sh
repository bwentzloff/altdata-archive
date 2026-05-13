#!/usr/bin/env bash
# Push commits one by one from oldest to newest
set -e
cd /Users/brian/Projects/altdata-archive

commits=$(git rev-list --reverse origin/main..main)
total=$(echo "$commits" | wc -l | tr -d ' ')
count=0

for sha in $commits; do
  count=$((count + 1))
  msg=$(git log --oneline -1 "$sha" | cut -c9-)
  echo "[$count/$total] Pushing $sha: $msg"
  attempt=0
  pushed=0
  while [ $attempt -lt 8 ]; do
    attempt=$((attempt + 1))
    if git push --progress origin "${sha}:main" 2>&1; then
      pushed=1
      break
    fi
    # Check if remote already has it (sometimes push succeeds but client times out)
    remote_sha=$(git ls-remote origin refs/heads/main | awk '{print $1}')
    if [ "$remote_sha" = "$sha" ]; then
      pushed=1
      break
    fi
    echo "  ⟳ attempt $attempt failed, retrying in 5s..."
    sleep 5
  done
  if [ $pushed -eq 1 ]; then
    echo "  ✓ pushed"
  else
    echo "  ✗ FAILED after $attempt attempts at commit $count/$total: $sha"
    echo "  Stopping. Run 'git push origin ${sha}:main' to retry."
    exit 1
  fi
done

echo ""
echo "All $total commits pushed successfully!"
