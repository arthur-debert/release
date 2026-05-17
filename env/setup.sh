#!/bin/bash
# Claude Code on the web — environment setup script.
#
# version: 2026-05-16-cross-repo-apt   # bumps on every change so re-pasting is trivial
#
# Paste this into your Claude Code on the web environment at:
#   claude.ai/code -> environment selector -> settings icon -> Setup script
#
# What it does
# ------------
#   1. Installs gh (GitHub CLI). Cloud sessions don't ship gh by default,
#      and the pr-review-respond skill (plus any cross-repo work) needs it.
#   2. Clones arthur-debert/release@main and:
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

set -euo pipefail

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

# Install lefthook globally. The binary is filesystem-root state, so it
# belongs in env setup (cached in the snapshot, not re-installed per
# session). `lefthook install` — which writes .git/hooks/pre-commit inside
# the cloned repo — is per-session and belongs in a SessionStart hook in
# each consumer repo.
#
# Pinned to a specific version so the env is reproducible across snapshot
# rebuilds. Bump deliberately when there's a reason; otherwise the same
# version ships everywhere.
LEFTHOOK_VERSION="2.1.6"
if command -v lefthook >/dev/null 2>&1; then
  echo "lefthook already installed: $(lefthook version)"
else
  npm install -g "lefthook@${LEFTHOOK_VERSION}"
  echo "installed lefthook: $(lefthook version)"
fi

# --- Stack-specific OS-level tools ---------------------------------------
#
# Same logic as gh + lefthook: filesystem-root state, snapshotted in the env,
# reused across every session in every consumer of the relevant stack. Each
# install is best-effort — if one fails (registry hiccup, transient network
# issue), the env still ships everything that succeeded.
#
# What's installed here:
#   bats            — shell test framework (rust-cli e2e + ad-hoc shell tests)
#   @vscode/vsce    — VS Code extension packaging CLI
#   ovsx            — Open VSX marketplace publisher (alternative to vsce publish)
#   lua5.4 + luarocks   — Lua runtime + package manager (for nvim plugins)
#   busted + vusted — Lua test runners (vusted = Neovim-headless wrapper around busted)
#   neovim          — nvim binary (needed by lex-fmt/nvim and any consumer that
#                     invokes nvim during tests; the snapshot ubuntu image
#                     does not ship it by default)
#   xvfb            — virtual framebuffer; binary is env-side, starting the
#                     :99 daemon is per-repo in scripts/setup-dev-env.sh for
#                     GUI-test consumers (lexed, arami-app, future Electron)
#   uuid-runtime    — provides `uuidgen` (padz live-tests; cheap, ~30KB)
#   Tauri/GTK system libs — required to build Tauri apps from source
#                           (arami-core today; any future Tauri consumer)

# bats — single-binary apt install
if ! command -v bats >/dev/null 2>&1; then
  apt install -y bats || echo "warning: bats install failed" >&2
  command -v bats >/dev/null 2>&1 && echo "installed bats: $(bats --version | head -1)"
fi

# neovim — apt's `neovim` package
if ! command -v nvim >/dev/null 2>&1; then
  apt install -y neovim || echo "warning: neovim install failed" >&2
  command -v nvim >/dev/null 2>&1 && echo "installed neovim: $(nvim --version | head -1)"
fi

# xvfb — virtual framebuffer for headless GUI-app tests. Consumers that
# need a running display start `Xvfb :99` themselves (idempotent, in
# their scripts/setup-dev-env.sh); this just ensures the binary is on
# disk so that start step works.
if ! command -v Xvfb >/dev/null 2>&1; then
  apt install -y xvfb || echo "warning: xvfb install failed" >&2
  command -v Xvfb >/dev/null 2>&1 && echo "installed xvfb: $(Xvfb -help 2>&1 | head -1)"
fi

# uuid-runtime — provides `uuidgen`, used by padz live-tests.
if ! command -v uuidgen >/dev/null 2>&1; then
  apt install -y uuid-runtime || echo "warning: uuid-runtime install failed" >&2
  command -v uuidgen >/dev/null 2>&1 && echo "installed uuid-runtime: uuidgen $(uuidgen --version 2>&1 | head -1 || echo present)"
fi

# Tauri / GTK system libs — required to compile Tauri apps from source.
# `apt install` is already idempotent and fast when every package is in
# place (~1s on a warm snapshot), so we don't try to short-circuit with
# dpkg probes: a single-package guard would miss the case where one of
# the six was installed manually but the others are absent.
TAURI_PKGS="libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev \
  libayatana-appindicator3-dev librsvg2-dev libjavascriptcoregtk-4.1-dev"
# shellcheck disable=SC2086  # intentional word-splitting of TAURI_PKGS
apt install -y ${TAURI_PKGS} \
  || echo "warning: Tauri/GTK system libs install failed" >&2

# vsce + ovsx — VS Code extension packaging/publishing CLIs via npm
if ! command -v vsce >/dev/null 2>&1; then
  npm install -g @vscode/vsce ovsx || echo "warning: vsce/ovsx install failed" >&2
  command -v vsce >/dev/null 2>&1 && echo "installed vsce: $(vsce --version)"
fi

# Lua + luarocks (apt) — needed for nvim plugin testing
if ! command -v luarocks >/dev/null 2>&1; then
  apt install -y lua5.4 luarocks || echo "warning: lua/luarocks install failed" >&2
  command -v luarocks >/dev/null 2>&1 && echo "installed luarocks: $(luarocks --version | head -1)"
fi

# busted + vusted (luarocks install) — depend on luarocks being present
if command -v luarocks >/dev/null 2>&1; then
  if ! command -v busted >/dev/null 2>&1; then
    luarocks install --tree=/usr/local busted || echo "warning: busted install failed" >&2
  fi
  if ! command -v vusted >/dev/null 2>&1; then
    luarocks install --tree=/usr/local vusted || echo "warning: vusted install failed" >&2
  fi
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
