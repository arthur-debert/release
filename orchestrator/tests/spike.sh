#!/usr/bin/env bash
# Phase A spike: validate session opens, persists, resumes.
#
# Usage: orchestrator/tests/spike.sh [repo-path]
# Default repo: $HOME/h/release (this repo, dogfooded).
#
# Run via:  uv run tests/spike.sh
# Or:       uv run orc run <repo> "<prompt>"  for ad-hoc

set -euo pipefail

REPO="${1:-$HOME/h/release}"

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is set. Unset it before running the spike." >&2
  exit 1
fi

echo "=== Step 1: fresh session against $REPO ==="
uv run orc run "$REPO" \
  "Read README.md briefly. In one sentence, what does the take-iii branch change about how files get propagated to consumer repos?"

echo
echo "=== Step 2: stored session id ==="
uv run orc sessions list

echo
echo "=== Step 3: resume — does it see prior context? ==="
uv run orc resume "$REPO" \
  "What did I just ask you about? Answer in one sentence — do not re-read any file."

echo
echo "Spike done. Validation: did step 3's answer reference step 1's question?"
echo "  - Yes: session persistence works."
echo "  - No / re-reads file: SDK's resume parameter may differ from what we tried."
echo "    Re-run with -v on each orc call to inspect raw message shape."
