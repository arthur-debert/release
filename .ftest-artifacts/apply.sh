#!/usr/bin/env bash
# usage: apply.sh <source-repo> <worktree-path> <howto-file>
set -euo pipefail
SRC="$1"; WT="$2"; HOWTO="$3"
ART=/Users/adebert/h/release/.ftest-artifacts
INFRA_SKILLS="diagnose gh-pr-review-loop grill-me grill-with-docs handoff improve-codebase-architecture pr-review-respond qa release-issue-relay request-refactor-plan review tdd teach to-issues triage ubiquitous-language zoom-out"

# fresh worktree (detached, throwaway)
if [ -d "$WT" ]; then git -C "$SRC" worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"; fi
git -C "$SRC" worktree add --detach "$WT" HEAD >/dev/null 2>&1

# minimal footprint:
# 1) CLAUDE.md -> stub pointing at release-core how-to
cp "$ART/CLAUDE-stub.md" "$WT/CLAUDE.md"
# 2) strip synced infra skills (keep app-domain skills)
for s in $INFRA_SKILLS; do rm -rf "$WT/.claude/skills/$s"; done
# 3) remove the synced ORIENTATION include target so nothing falls back to it
rm -f "$WT/.release/ORIENTATION.md" 2>/dev/null || true
# 4) prototype `release-core how-to` output (the command doesn't exist yet)
cp "$ART/$HOWTO" "$WT/HOWTO-PROTOTYPE.md"

echo "applied: $WT"
echo "  remaining skills: $(ls "$WT/.claude/skills" 2>/dev/null | tr '\n' ' ')"
