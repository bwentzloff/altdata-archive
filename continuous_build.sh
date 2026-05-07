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
    
    # Split players alphabetically, with heavy hitters (j, d, c, t, m, a) split further
    CHUNKS=(
        "docs/assets"
        "docs/data"
        "docs/hof"
        "docs/leagues"
        "docs/games"
        "docs/players/aa*.html docs/players/ab*.html docs/players/ac*.html docs/players/ad*.html"
        "docs/players/ae*.html docs/players/af*.html docs/players/ag*.html docs/players/ah*.html"
        "docs/players/ai*.html docs/players/aj*.html docs/players/ak*.html docs/players/al*.html"
        "docs/players/am*.html docs/players/an*.html docs/players/ao*.html docs/players/ap*.html"
        "docs/players/aq*.html docs/players/ar*.html docs/players/as*.html docs/players/at*.html"
        "docs/players/au*.html docs/players/av*.html docs/players/aw*.html docs/players/ax*.html"
        "docs/players/ay*.html docs/players/az*.html"
        "docs/players/ba*.html docs/players/bb*.html docs/players/bc*.html docs/players/bd*.html"
        "docs/players/be*.html docs/players/bf*.html docs/players/bg*.html docs/players/bh*.html"
        "docs/players/bi*.html docs/players/bj*.html docs/players/bk*.html docs/players/bl*.html"
        "docs/players/bm*.html docs/players/bn*.html docs/players/bo*.html docs/players/bp*.html"
        "docs/players/bq*.html docs/players/br*.html docs/players/bs*.html docs/players/bt*.html"
        "docs/players/bu*.html docs/players/bv*.html docs/players/bw*.html docs/players/bx*.html"
        "docs/players/by*.html docs/players/bz*.html"
        "docs/players/ca*.html docs/players/cb*.html docs/players/cc*.html docs/players/cd*.html"
        "docs/players/ce*.html docs/players/cf*.html docs/players/cg*.html docs/players/ch*.html"
        "docs/players/ci*.html docs/players/cj*.html docs/players/ck*.html docs/players/cl*.html"
        "docs/players/cm*.html docs/players/cn*.html docs/players/co*.html docs/players/cp*.html"
        "docs/players/cq*.html docs/players/cr*.html docs/players/cs*.html docs/players/ct*.html"
        "docs/players/cu*.html docs/players/cv*.html docs/players/cw*.html docs/players/cx*.html"
        "docs/players/cy*.html docs/players/cz*.html"
        "docs/players/da*.html docs/players/db*.html docs/players/dc*.html docs/players/dd*.html"
        "docs/players/de*.html docs/players/df*.html docs/players/dg*.html docs/players/dh*.html"
        "docs/players/di*.html docs/players/dj*.html docs/players/dk*.html docs/players/dl*.html"
        "docs/players/dm*.html docs/players/dn*.html docs/players/do*.html docs/players/dp*.html"
        "docs/players/dq*.html docs/players/dr*.html docs/players/ds*.html docs/players/dt*.html"
        "docs/players/du*.html docs/players/dv*.html docs/players/dw*.html docs/players/dx*.html"
        "docs/players/dy*.html docs/players/dz*.html"
        "docs/players/e*.html"
        "docs/players/f*.html"
        "docs/players/g*.html"
        "docs/players/h*.html"
        "docs/players/ia*.html docs/players/ib*.html docs/players/ic*.html docs/players/id*.html"
        "docs/players/ie*.html docs/players/if*.html docs/players/ig*.html docs/players/ih*.html"
        "docs/players/ii*.html docs/players/ij*.html docs/players/ik*.html docs/players/il*.html"
        "docs/players/im*.html docs/players/in*.html docs/players/io*.html docs/players/ip*.html"
        "docs/players/iq*.html docs/players/ir*.html docs/players/is*.html docs/players/it*.html"
        "docs/players/iu*.html docs/players/iv*.html docs/players/iw*.html docs/players/ix*.html"
        "docs/players/iy*.html docs/players/iz*.html"
        "docs/players/ja*.html docs/players/jb*.html docs/players/jc*.html docs/players/jd*.html"
        "docs/players/je*.html docs/players/jf*.html docs/players/jg*.html docs/players/jh*.html"
        "docs/players/ji*.html docs/players/jj*.html docs/players/jk*.html docs/players/jl*.html"
        "docs/players/jm*.html docs/players/jn*.html docs/players/jo*.html docs/players/jp*.html"
        "docs/players/jq*.html docs/players/jr*.html docs/players/js*.html docs/players/jt*.html"
        "docs/players/ju*.html docs/players/jv*.html docs/players/jw*.html docs/players/jx*.html"
        "docs/players/jy*.html docs/players/jz*.html"
        "docs/players/k*.html"
        "docs/players/l*.html"
        "docs/players/ma*.html docs/players/mb*.html docs/players/mc*.html docs/players/md*.html"
        "docs/players/me*.html docs/players/mf*.html docs/players/mg*.html docs/players/mh*.html"
        "docs/players/mi*.html docs/players/mj*.html docs/players/mk*.html docs/players/ml*.html"
        "docs/players/mm*.html docs/players/mn*.html docs/players/mo*.html docs/players/mp*.html"
        "docs/players/mq*.html docs/players/mr*.html docs/players/ms*.html docs/players/mt*.html"
        "docs/players/mu*.html docs/players/mv*.html docs/players/mw*.html docs/players/mx*.html"
        "docs/players/my*.html docs/players/mz*.html"
        "docs/players/n*.html"
        "docs/players/o*.html"
        "docs/players/p*.html"
        "docs/players/q*.html"
        "docs/players/r*.html"
        "docs/players/s*.html"
        "docs/players/ta*.html docs/players/tb*.html docs/players/tc*.html docs/players/td*.html"
        "docs/players/te*.html docs/players/tf*.html docs/players/tg*.html docs/players/th*.html"
        "docs/players/ti*.html docs/players/tj*.html docs/players/tk*.html docs/players/tl*.html"
        "docs/players/tm*.html docs/players/tn*.html docs/players/to*.html docs/players/tp*.html"
        "docs/players/tq*.html docs/players/tr*.html docs/players/ts*.html docs/players/tt*.html"
        "docs/players/tu*.html docs/players/tv*.html docs/players/tw*.html docs/players/tx*.html"
        "docs/players/ty*.html docs/players/tz*.html"
        "docs/players/u*.html"
        "docs/players/v*.html"
        "docs/players/w*.html"
        "docs/players/x*.html"
        "docs/players/y*.html"
        "docs/players/z*.html"
        "docs/players/*.csv docs/players/*.json docs/players/*.xml"
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
                
                echo "  ⏸ Waiting 5 seconds before next chunk..."
                sleep 5
            fi
        fi
    done
    
    echo ""
    echo "▶ Run #$RUN complete. Waiting 5 minutes before next full build..."
    sleep 300
    
done
