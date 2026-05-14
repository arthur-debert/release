#!/bin/bash
# Claude Code on the web — environment setup script.
#
# version: 2026-05-14-1600-main   # bumps on every change so re-pasting is trivial
#
# Paste this into your Claude Code on the web environment at:
#   claude.ai/code -> environment selector -> settings icon -> Setup script
#
# What it does
# ------------
#   1. Installs gh (GitHub CLI). Cloud sessions don't ship gh by default,
#      and the pr-review-respond skill (plus any cross-repo work) needs it.
#   2. Clones arthur-debert/release@cloud and:
#        - copies skills/* into ~/.claude/skills/
#        - copies env/CLAUDE.md into ~/.claude/CLAUDE.md
#      Both are read by Claude Code in cloud sessions at session start.
#
# Why standalone skills (no plugin marketplace)
# ---------------------------------------------
#   Plugin marketplaces work in cloud only up to the marketplace-fetch step.
#   The /plugin install step requires an interactive trust prompt that the
#   cloud UI doesn't expose (no /plugin command). Standalone skills under
#   ~/.claude/skills/ bypass that gate entirely — files on disk, no install,
#   no prompt.
#
# Also configure (same dialog, "Environment variables" field)
# -----------------------------------------------------------
#   GH_TOKEN=<fine-grained PAT>
#
#   Suggested PAT permissions per related-repo group (one PAT per group
#   is cleaner than one PAT spanning everything):
#     Contents:      Read and write
#     Issues:        Read and write
#     Pull requests: Read and write
#     Metadata:      Read   (auto-required)
#
#   gh reads GH_TOKEN automatically; no `gh auth login` step needed.
#   GH_TOKEN unlocks cross-repo gh API operations (issues, PRs, comments)
#   that the session's default MCP scope blocks.
#
# Snapshot lifecycle
# ------------------
#   This script runs once per environment, then Anthropic snapshots the
#   filesystem and reuses the snapshot for every subsequent session in this
#   environment. The script auto-re-runs when:
#     - you edit it (e.g. bumping the version comment above)
#     - you change the allowed network hosts
#     - the snapshot reaches its ~7-day TTL
#   To pull a new skill version mid-cycle: bump the version comment and
#   re-paste — that invalidates the snapshot and re-clones on next session.
#
# Network access level
# --------------------
#   Leave at the default "Trusted" — the apt mirrors and github.com are
#   in the default allowlist, so this script works without any tweaks.

set -e

# --- 1. Install gh ---------------------------------------------------------

if command -v gh >/dev/null 2>&1; then
  echo "gh already installed: $(gh --version | head -1)"
else
  # `apt update` fails non-fatally here. The pre-installed deadsnakes
  # (Python) and ondrej (PHP) PPAs intermittently 403 from
  # launchpadcontent.net behind the sandbox HTTPS proxy, which makes
  # `apt update` exit non-zero even though the standard Ubuntu repos
  # succeed. We only need noble/main for gh, so stale-list-with-bad-PPAs
  # is enough. The `|| true` pattern is Anthropic's documented advice for
  # non-critical setup-script commands.
  apt update || true
  apt install -y gh
  gh --version | head -1
fi

# --- 2. Clone arthur-debert/release and install skills + CLAUDE.md --------

SRC_REPO="https://github.com/arthur-debert/release.git"
SRC_BRANCH="main"
CLONE_DIR="/tmp/arthur-debert-release"
DEST="$HOME/.claude"

mkdir -p "$DEST/skills"

# Fresh clone every setup-script run. Idempotent: anything previously in
# $DEST from a prior run gets overwritten by the new version.
rm -rf "$CLONE_DIR"
git clone --depth 1 --branch "$SRC_BRANCH" "$SRC_REPO" "$CLONE_DIR"

# Install each skill directory verbatim.
if [ -d "$CLONE_DIR/skills" ]; then
  for skill_dir in "$CLONE_DIR/skills"/*/; do
    [ -d "$skill_dir" ] || continue
    skill_name=$(basename "$skill_dir")
    rm -rf "$DEST/skills/$skill_name"
    cp -r "$skill_dir" "$DEST/skills/$skill_name"
    echo "installed skill: $skill_name"
  done
else
  echo "warning: skills/ not found in clone — no skills installed" >&2
fi

# Install user-level CLAUDE.md (portfolio-wide instructions).
if [ -f "$CLONE_DIR/env/CLAUDE.md" ]; then
  cp "$CLONE_DIR/env/CLAUDE.md" "$DEST/CLAUDE.md"
  echo "wrote: ~/.claude/CLAUDE.md"
else
  echo "warning: env/CLAUDE.md not found in clone — no user-level CLAUDE.md installed" >&2
fi

# Cleanup the clone scratch dir so it doesn't end up in the snapshot.
rm -rf "$CLONE_DIR"

echo "Setup complete. ~/.claude contents:"
ls -la "$DEST"
