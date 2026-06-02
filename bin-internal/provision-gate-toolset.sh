#!/usr/bin/env bash
# provision-gate-toolset.sh — install the lint/format toolset the hardened gate
# (lefthook.yml) needs.
#
# This is PROVISIONING, a distinct concern from running the gate — kept in its own
# script (not inline in a composite action) so it is testable outside GitHub
# Actions, reusable across contexts, and branches by OS / batches installs. The
# arm-gate composite calls this thinly (actions are thin, they call scripts); the
# session-start bootstrap (templates/commons/bin/setup-dev-env.sh §0) and the
# cloud provisioning (env/setup.sh) are the other contexts this same unit is meant
# to converge onto — one definition of "arm the gate toolset", invoked everywhere.
#
# The gate is HARD — a missing tool FAILS the gate, it does not skip (release#412)
# — so this installs the UNION across release's own gate and the consumer gate
# (extra tools are harmless): lefthook + prettier + markdownlint (npm), ruff
# (pinned) + yamllint (pip), shellcheck + actionlint (OS package manager, or
# actionlint's pinned installer on Linux).
#
# Idempotent: only missing tools are installed. Batched: one call per package
# manager. Pinned: ruff and actionlint versions are fixed (overridable via env)
# so a new upstream release can't turn an unrelated PR red — same rationale as the
# pip-bootstrap smoke's yq pin. Keep RUFF_VERSION in sync with setup-dev-env.sh.

set -euo pipefail

RUFF_VERSION="${RUFF_VERSION:-0.15.12}"
ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-1.7.7}"

log() { printf 'provision-gate-toolset: %s\n' "$1" >&2; }

# Run with sudo when not already root and sudo is available (CI runners install
# system packages as a non-root user with passwordless sudo; containers run as
# root with no sudo).
_maybe_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

# --- npm globals: lefthook + the markdown/format linters (batched) -----------
npm_missing=()
for tool in lefthook prettier markdownlint; do
  command -v "$tool" >/dev/null 2>&1 || npm_missing+=("$tool")
done
if [ "${#npm_missing[@]}" -gt 0 ]; then
  # markdownlint's package is markdownlint-cli; map it.
  pkgs=()
  for t in "${npm_missing[@]}"; do
    case "$t" in
      markdownlint) pkgs+=("markdownlint-cli") ;;
      *) pkgs+=("$t") ;;
    esac
  done
  log "npm install -g ${pkgs[*]}"
  npm install -g "${pkgs[@]}"
fi

# --- pip tools: ruff (pinned) + yamllint (batched) ---------------------------
# Always (re)install ruff at the pin so a drifted local/runner ruff can't change
# findings; yamllint alongside it in one call.
log "pip install ruff==${RUFF_VERSION} yamllint"
pip install "ruff==${RUFF_VERSION}" yamllint

# --- system tools: shellcheck + actionlint, branched by OS -------------------
need_shellcheck=0; command -v shellcheck >/dev/null 2>&1 || need_shellcheck=1
need_actionlint=0; command -v actionlint >/dev/null 2>&1 || need_actionlint=1

os="$(uname -s)"
case "$os" in
  Darwin)
    # Homebrew ships both — batch whatever is missing into one call.
    brew_pkgs=()
    [ "$need_shellcheck" -eq 1 ] && brew_pkgs+=("shellcheck")
    [ "$need_actionlint" -eq 1 ] && brew_pkgs+=("actionlint")
    if [ "${#brew_pkgs[@]}" -gt 0 ]; then
      command -v brew >/dev/null 2>&1 || { log "ERROR: brew not found on macOS"; exit 1; }
      log "brew install ${brew_pkgs[*]}"
      brew install "${brew_pkgs[@]}"
    fi
    ;;
  Linux)
    # Both Linux installs need root (apt; writing actionlint to /usr/local/bin).
    # If anything is actually missing and we can neither be root nor escalate,
    # fail FAST with an actionable message rather than letting apt / the
    # /usr/local/bin write die later on a cryptic permission error.
    if { [ "$need_shellcheck" -eq 1 ] || [ "$need_actionlint" -eq 1 ]; } \
       && [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
      log "ERROR: need to install shellcheck/actionlint but running as non-root without sudo."
      log "       Re-run as root (or with sudo available), or pre-install them."
      exit 1
    fi
    # apt has shellcheck but NOT actionlint; install shellcheck via apt (one
    # update + install), actionlint via its pinned official installer.
    if [ "$need_shellcheck" -eq 1 ]; then
      command -v apt-get >/dev/null 2>&1 || { log "ERROR: apt-get not found on Linux"; exit 1; }
      log "apt-get install shellcheck"
      _maybe_sudo apt-get update -qq
      _maybe_sudo apt-get install -y shellcheck
    fi
    if [ "$need_actionlint" -eq 1 ]; then
      log "download actionlint ${ACTIONLINT_VERSION} -> /usr/local/bin"
      url='https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash'
      # /usr/local/bin is root-owned on standard runners; install via sudo when
      # available. Pipe curl|bash (process substitution under sudo can fail on FDs).
      if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        curl -sSfL "$url" | sudo bash -s -- "$ACTIONLINT_VERSION" /usr/local/bin
      else
        curl -sSfL "$url" | bash -s -- "$ACTIONLINT_VERSION" /usr/local/bin
      fi
    fi
    ;;
  *)
    if [ "$need_shellcheck" -eq 1 ] || [ "$need_actionlint" -eq 1 ]; then
      log "ERROR: unsupported OS '$os' — cannot provision shellcheck/actionlint"
      exit 1
    fi
    ;;
esac

log "done."
