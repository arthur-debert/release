#!/bin/bash
# Claude Code on the web — environment setup script.
#
# version: 2026-06-01-docker-hub-egress
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
#
#   ONE exception: Docker-delivery testing (`docker pull`, `apt install
#   ./pkg.deb` / `brew install <tap>/<formula>` inside a container — the
#   "test the delivery mechanism, not the binary" pattern in CLAUDE.md).
#   `docker pull` against Docker Hub is NOT covered by the default
#   Trusted allowlist and fails with a TLS/connect error at the registry
#   handshake. Add these three hosts to the environment's allowed network
#   hosts (environment selector -> settings -> network/allowed hosts) at
#   environment creation — setting it there is the cleanest fix and
#   unblocks the Docker suite directly:
#
#     registry-1.docker.io            # Docker Hub registry API (v2)
#     auth.docker.io                  # token/auth handshake (anon + login)
#     production.cloudfront.docker.com # blob CDN (image layer downloads)
#
#   Changing the allowed-hosts list invalidates the snapshot and re-runs
#   this script on the next session (see "Snapshot lifecycle" above), so
#   no extra step is needed beyond saving the network setting.

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

# NOTE: lefthook is installed below in §2, AFTER the arthur-debert/release
# clone — it pins to LEFTHOOK_VERSION from the clone's shared
# templates/commons/bin/gate-tool-versions.sh so the snapshot bakes the SAME
# version the gate provisioners reconcile to (no separate literal to drift,
# release#531). The binary is filesystem-root state cached in the snapshot;
# `lefthook install` (writing .git/hooks/pre-commit) stays per-session in each
# consumer's SessionStart hook.

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
#   lua5.4 + luarocks       — Lua runtime + package manager (for nvim plugins)
#   busted + vusted + luacheck — Lua test runners + lint (lex-fmt/nvim CI parity)
#   neovim (≥0.11)  — Ubuntu apt ships 0.9.5 which is too old for current
#                     nvim-lspconfig; we install the official stable tarball
#                     from github.com/neovim/neovim/releases instead
#   xvfb            — virtual framebuffer; binary is env-side, starting the
#                     :99 daemon is per-repo in bin/setup-dev-env.sh for
#                     GUI-test consumers (lexed, phos-app, future Electron)
#   libnss3-tools   — provides `certutil` so the canonical setup-dev-env.sh
#                     can import the sandbox-egress CA into the per-user
#                     Chromium NSS DB (needed by every Electron / Playwright
#                     consumer; lexed surfaced this first)
#   uuid-runtime    — provides `uuidgen` (padz live-tests; cheap, ~30KB)
#   Tauri/GTK system libs — required to build Tauri apps from source
#                           (phos-core today; any future Tauri consumer)

# bats — single-binary apt install
if ! command -v bats >/dev/null 2>&1; then
  apt install -y bats || echo "warning: bats install failed" >&2
  command -v bats >/dev/null 2>&1 && echo "installed bats: $(bats --version | head -1)"
fi

# neovim ≥0.11 — the apt package on noble is 0.9.5, but the pinned
# nvim-lspconfig in lex-fmt/nvim refuses to load on <0.11 ("nvim-lspconfig
# support for Nvim 0.10 or older is deprecated"). On 0.9.5 the symptom is
# silent: lazy.setup returns, plugins register, but require("lspconfig")
# never succeeds and every LSP-attach test hangs. Fetch the official
# stable tarball and overlay it under /usr/local. Idempotent — re-check
# version before re-downloading.
NVIM_MIN_MAJOR=0
NVIM_MIN_MINOR=11
_nvim_ok() {
  command -v nvim >/dev/null 2>&1 || return 1
  local v major minor
  v=$(nvim --version 2>/dev/null | head -1 | sed -E 's/^NVIM v([0-9]+\.[0-9]+).*/\1/')
  major="${v%%.*}"
  minor="${v##*.}"
  # Defensive: bail out if either component didn't parse as a number
  # (e.g. an unusual nvim --version format).
  case "${major}" in ''|*[!0-9]*) return 1 ;; esac
  case "${minor}" in ''|*[!0-9]*) return 1 ;; esac
  if [ "${major}" -gt "${NVIM_MIN_MAJOR}" ] \
     || { [ "${major}" -eq "${NVIM_MIN_MAJOR}" ] && [ "${minor}" -ge "${NVIM_MIN_MINOR}" ]; }; then
    echo "nvim already installed: $(nvim --version | head -1)"
    return 0
  fi
  return 1
}
if ! _nvim_ok; then
  case "$(uname -m)" in
    x86_64|amd64) _nvim_arch=linux-x86_64 ;;
    aarch64|arm64) _nvim_arch=linux-arm64 ;;
    *) _nvim_arch="" ;;
  esac
  if [ -n "${_nvim_arch}" ]; then
    _nvim_tmp=$(mktemp -d)
    if curl -fsSL "https://github.com/neovim/neovim/releases/download/stable/nvim-${_nvim_arch}.tar.gz" \
         -o "${_nvim_tmp}/nvim.tgz" && \
       tar -xzf "${_nvim_tmp}/nvim.tgz" -C "${_nvim_tmp}" && \
       cp -r "${_nvim_tmp}/nvim-${_nvim_arch}/." /usr/local/; then
      hash -r 2>/dev/null || true
      command -v nvim >/dev/null 2>&1 && echo "installed nvim: $(nvim --version | head -1)"
    else
      echo "warning: nvim stable install failed" >&2
    fi
    rm -rf "${_nvim_tmp}"
  fi
fi

# xvfb — virtual framebuffer for headless GUI-app tests. Consumers that
# need a running display start `Xvfb :99` themselves (idempotent, in
# their bin/setup-dev-env.sh); this just ensures the binary is on
# disk so that start step works.
if ! command -v Xvfb >/dev/null 2>&1; then
  apt install -y xvfb || echo "warning: xvfb install failed" >&2
  command -v Xvfb >/dev/null 2>&1 && echo "installed xvfb: $(Xvfb -help 2>&1 | head -1)"
fi

# libnss3-tools — provides `certutil`, used by the canonical
# setup-dev-env.sh to import the sandbox-egress TLS-inspection CA into
# ~/.pki/nssdb so Chromium / Electron renderers stop throwing
# ERR_CERT_AUTHORITY_INVALID on HTTPS resources. The cert import is
# per-user (lives in $HOME) so it has to happen at session start; the
# binary that does the import is env-level state.
if ! command -v certutil >/dev/null 2>&1; then
  apt install -y libnss3-tools || echo "warning: libnss3-tools install failed" >&2
  # certutil has no clean version probe (`certutil -V` is a verification
  # subcommand that exits non-zero when invoked without args). Just
  # confirm the binary is on PATH.
  command -v certutil >/dev/null 2>&1 && echo "installed certutil ($(command -v certutil))"
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

# Playwright Chromium system deps — what `npx playwright install
# --with-deps` would install on a fresh Ubuntu, minus the packages
# already provided transitively by libgtk-3-dev / libwebkit2gtk-4.1-dev
# / libnss3-tools (installed above). Pre-installing them here is the
# fix for phos-app's cloud-session e2e flow: `playwright install
# --with-deps` invokes `apt update` internally, which 403s on the
# deadsnakes/ondrej PPAs in the sandbox and fails the whole step —
# even though every required package is already on disk.
#
# After this section the sandbox is ready for Playwright tests:
#   * Browsers download per-project via `npm install` (Playwright's
#     postinstall) and live in node_modules/.local-chromium/ — that
#     flow uses HTTPS to playwright.azureedge.net + cdn.playwright.dev,
#     both in the default Trusted allowlist; no apt involvement.
#   * `--with-deps` becomes unnecessary; consumers / agents should
#     invoke `npx playwright install` (no --with-deps) for browser
#     provisioning OR rely on npm's automatic install. The cloud
#     CLAUDE.md notes this so future sessions skip `--with-deps`.
PLAYWRIGHT_PKGS="libnspr4 libgbm1 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxrandr2 libasound2t64"
# shellcheck disable=SC2086  # intentional word-splitting
apt install -y ${PLAYWRIGHT_PKGS} \
  || echo "warning: Playwright system libs install failed" >&2

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

# busted + vusted + luacheck (luarocks install) — depend on luarocks being
# present. luacheck is the Lua linter that lex-fmt/nvim CI uses
# (`luacheck lua/`); without it cloud sessions can't run the lint target.
if command -v luarocks >/dev/null 2>&1; then
  if ! command -v busted >/dev/null 2>&1; then
    luarocks install --tree=/usr/local busted || echo "warning: busted install failed" >&2
  fi
  if ! command -v vusted >/dev/null 2>&1; then
    luarocks install --tree=/usr/local vusted || echo "warning: vusted install failed" >&2
  fi
  if ! command -v luacheck >/dev/null 2>&1; then
    # NOT --quiet here: the egress policy 403s luarocks.org's manifest URL,
    # luarocks transparently falls back to the moonrocks mirror and the
    # install succeeds, but --quiet treats the manifest fetch as fatal
    # and propagates a non-zero exit. Plain invocation reports the real
    # outcome.
    luarocks install --tree=/usr/local luacheck || echo "warning: luacheck install failed" >&2
  fi
fi

# Google Cloud SDK + Firestore emulator — required by supage's
# hermetic-emulator BATS integration suite (per supage/.github/workflows/ci.yml's
# `integration` job; supage PR #12).
#
# Java 21 (also required by the emulator binary) already ships with
# the Anthropic cloud base image — don't reinstall.
#
# Best-effort install: if the env-setup phase's egress blocks
# packages.cloud.google.com (the agent sandbox does, per supage PR #12's
# verification notes — env-setup phase may share the same allowlist),
# warn but don't fail the env setup. supage CI still covers the
# integration path end-to-end; cloud-session local verification is
# the convenience this section enables.
if ! command -v gcloud >/dev/null 2>&1; then
  if curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg 2>/dev/null \
      | gpg --dearmor -o /usr/share/keyrings/cloud-google.gpg 2>/dev/null; then
    echo "deb [signed-by=/usr/share/keyrings/cloud-google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list
    apt update || true
    apt install -y google-cloud-cli google-cloud-cli-firestore-emulator google-cloud-cli-beta \
      || echo "warning: google-cloud-cli install failed (apt-side egress to packages.cloud.google.com probably blocked)" >&2
    command -v gcloud >/dev/null 2>&1 && echo "installed gcloud: $(gcloud --version 2>/dev/null | head -1)"
  else
    echo "warning: gcloud apt key fetch failed (env-setup egress to packages.cloud.google.com blocked); supage Firestore emulator will not be available in cloud sessions" >&2
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

# Install bin/fetch-artifact onto /usr/local/bin so consumer
# bin/setup-dev-env.sh can call it without cloning release/. The
# script reads ./artifacts.json (per docs/artifacts-schema.md) and
# pulls pinned cross-repo artifacts from GH releases.
if [ -f "$CLONE_DIR/bin/fetch-artifact" ]; then
  install -m 0755 "$CLONE_DIR/bin/fetch-artifact" /usr/local/bin/fetch-artifact
  echo "installed fetch-artifact: $(fetch-artifact --version)"
else
  echo "warning: bin/fetch-artifact not found in clone — consumers that depend on it will hand-roll the fetch" >&2
fi

# Install bin/clone-lex-stack — the multi-repo bootstrap helper that
# the lex-multirepo skill drives. See skills/lex-multirepo/SKILL.md
# and the merged plan in lex-fmt/lex#661 for the design.
if [ -f "$CLONE_DIR/bin/clone-lex-stack" ]; then
  install -m 0755 "$CLONE_DIR/bin/clone-lex-stack" /usr/local/bin/clone-lex-stack
  echo "installed clone-lex-stack ($(command -v clone-lex-stack))"
else
  echo "warning: bin/clone-lex-stack not found in clone — the lex-multirepo skill will fall back to a manual gh clone loop" >&2
fi

# Install lefthook at the SHARED pin (single source of truth — same version the
# gate provisioners reconcile to, release#531). Sourced from the clone so the pin
# is authoritative, not re-declared here. The `:=` fallback is a last-resort
# safety net (mirrors setup-dev-env.sh): it only applies if the clone somehow
# lacks the shared file, and is kept matching it by tests/gate-tool-versions/.
if [ -f "$CLONE_DIR/templates/commons/bin/gate-tool-versions.sh" ]; then
  # shellcheck source=/dev/null
  . "$CLONE_DIR/templates/commons/bin/gate-tool-versions.sh"
fi
: "${LEFTHOOK_VERSION:=2.1.9}"
if command -v gate_version_matches >/dev/null 2>&1 \
  && gate_version_matches lefthook "$LEFTHOOK_VERSION"; then
  echo "lefthook already at pin: $(lefthook version)"
else
  npm install -g "lefthook@${LEFTHOOK_VERSION}"
  echo "installed lefthook: $(lefthook version)"
fi

# Cleanup the clone scratch dir so it doesn't end up in the snapshot.
rm -rf "$CLONE_DIR"

echo "Setup complete. ~/.claude contents:"
ls -la "$DEST"
