#!/usr/bin/env bash
# Continuous build with chunked commits
# Runs full build, then commits and pushes docs/ in chunks, waits 5 minutes, repeats
# Run with Ctrl+C to stop

set -euo pipefail
cd "$(dirname "$0")"

echo "╔════════════════════════════════════════════════════════╗"
echo "║  AltSports Archive — Continuous Build (Chunked Pushes) ║"
echo "║  Press Ctrl+C to stop                                  ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""

RUN=0
while true; do
    RUN=$((RUN + 1))
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "RUN #$RUN — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════════════════════════"
    echo ""
    
    # Run the full build
    echo "▶ Starting full build..."
    bash build.sh || {
        echo "⚠ Build failed, but continuing to commit what we have..."
    }
    echo ""
    
    # Stage all docs/ changes
    git add docs/ 2>/dev/null || true
    echo ""
    CHUNKS=(
        "docs/assets"
        "docs/data"
        "docs/hof"
        "docs/leagues"
        "docs/games"
        "docs/players"
        "docs/*.html docs/*.txt docs/*.xml docs/.nojekyll"
    )
    
    for CHUNK in "${CHUNKS[@]}"; do
        echo "▶ Staging: $CHUNK"
        if git add $CHUNK 2>/dev/null; then
            if ! git diff --cached --quiet; then
                TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
                echo "  → Committing chunk..."
                git commit -m "build: Update $CHUNK — $TIMESTAMP" || true
                
                # Ensure no uncommitted changes before pulling
                if ! git diff --quiet; then
                    echo "  ⚠ Uncommitted changes remain, stashing temporarily..."
                    git stash
                    STASHED=1
                else
                    STASHED=0
                fi
                
                echo "  → Pulling remote changes..."
                if git pull --rebase origin main 2>/dev/null; then
                    # Restore stashed changes if we stashed
                    if [[ $STASHED -eq 1 ]]; then
                        git stash pop 2>/dev/null || true
                    fi
                    
                    echo "  → Pushing chunk..."
                    if git push origin main; then
                        echo "  ✓ Chunk pushed successfully"
                    else
                        echo "  ⚠ Push failed (may retry next iteration)"
                    fi
                else
                    echo "  ⚠ Rebase conflict, skipping push for this chunk"
                    # Restore stashed changes if we stashed
                    if [[ $STASHED -eq 1 ]]; then
                        git stash pop 2>/dev/null || true
                    fi
                    git rebase --abort 2>/dev/null || true
                fi
                
                echo "  ⏸ Waiting 5 minutes before next chunk..."
                sleep 300
            fi
        fi
    done
    
    echo ""
    echo "▶ Run #$RUN complete. Waiting 5 minutes before next full build..."
    sleep 300
    
done
