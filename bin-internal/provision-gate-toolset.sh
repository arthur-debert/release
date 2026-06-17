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
# (extra tools are harmless): lefthook + prettier + markdownlint (npm), ruff +
# yamllint + shellcheck (pip — shellcheck via the shellcheck-py wheel), and
# actionlint (pinned official downloader).
#
# RECONCILE, not install-if-missing (release#531): EVERY tool is pinned and
# (re)installed at its pin whenever the present binary reports a different
# version — a pre-existing floating brew/apt/npm binary is reconciled DOWN/UP to
# the pin, not silently accepted. install-if-missing was the bug: it let a dev
# box keep actionlint 1.7.12 while CI ran the pinned 1.7.7, so the same gate gave
# different verdicts. The pins + the gate_version_matches helper are the SINGLE
# source of truth in templates/commons/bin/gate-tool-versions.sh, shared with the
# SessionStart provisioner setup-dev-env.sh so the two can't diverge (release#498).

set -euo pipefail

# Pure-bash dir of this script (no external `dirname`/`pwd` — keeps working under
# the hermetic `env -i` PATH the tests run it with). When invoked as a bare name
# from within bin-internal/ (`bash provision-gate-toolset.sh`), BASH_SOURCE[0]
# has no `/`, so `%/*` is a no-op and leaves the filename — fall back to `.`.
_THIS_DIR="${BASH_SOURCE[0]%/*}"
[ "$_THIS_DIR" = "${BASH_SOURCE[0]}" ] && _THIS_DIR="."
# shellcheck source=/dev/null
. "${_THIS_DIR}/../templates/commons/bin/gate-tool-versions.sh"

log() { printf 'provision-gate-toolset: %s\n' "$1" >&2; }

# --- npm globals: lefthook + prettier + markdownlint, each at its pin ---------
# Reconcile to the pin, NOT install-if-missing: gate_version_matches reports a
# floating/absent binary as a miss, so it gets (re)installed at the exact pin.
# markdownlint's package is markdownlint-cli; the binary is `markdownlint`.
npm_pkgs=()
gate_version_matches lefthook     "$LEFTHOOK_VERSION"          || npm_pkgs+=("lefthook@${LEFTHOOK_VERSION}")
gate_version_matches prettier     "$PRETTIER_VERSION"          || npm_pkgs+=("prettier@${PRETTIER_VERSION}")
gate_version_matches markdownlint "$MARKDOWNLINT_CLI_VERSION"  || npm_pkgs+=("markdownlint-cli@${MARKDOWNLINT_CLI_VERSION}")
if [ "${#npm_pkgs[@]}" -gt 0 ]; then
  command -v npm >/dev/null 2>&1 || { log "ERROR: npm not found — cannot install ${npm_pkgs[*]}"; exit 1; }
  log "npm install -g ${npm_pkgs[*]}"
  npm install -g "${npm_pkgs[@]}"
fi

# --- pip tools: ruff + yamllint + shellcheck (via shellcheck-py), all pinned --
# Note: shellcheck rides the pip path — the shellcheck-py wheel bundles the real
# binary (mac + manylinux x86_64) — so it is pinned identically to ruff/yamllint, no
# apt-0.9.0-vs-brew-0.11 split. One pinned install reconciles all three whenever
# any drifts from its pin.
if ! gate_version_matches ruff "$RUFF_VERSION" \
  || ! gate_version_matches yamllint "$YAMLLINT_VERSION" \
  || ! gate_version_matches shellcheck "$SHELLCHECK_VERSION"; then
  command -v pip >/dev/null 2>&1 || { log "ERROR: pip not found — cannot install ruff/yamllint/shellcheck"; exit 1; }
  log "pip install ruff==${RUFF_VERSION} yamllint==${YAMLLINT_VERSION} shellcheck-py==${SHELLCHECK_PY_VERSION}"
  pip install "ruff==${RUFF_VERSION}" "yamllint==${YAMLLINT_VERSION}" "shellcheck-py==${SHELLCHECK_PY_VERSION}"
fi

# --- actionlint: pinned official downloader, identical on every OS -----------
# No standard pip/apt package (apt has none; brew floats), so use rhysd's pinned
# installer → /usr/local/bin on BOTH macOS and Linux. Reconcile to the pin.
if ! gate_version_matches actionlint "$ACTIONLINT_VERSION"; then
  # /usr/local/bin is root-owned on standard runners; fail FAST if we can neither
  # be root nor escalate, instead of dying later on a cryptic permission error.
  if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: need to install actionlint ${ACTIONLINT_VERSION} but running as non-root without sudo."
    log "       Re-run as root (or with sudo available), or pre-install it at the pin."
    exit 1
  fi
  command -v curl >/dev/null 2>&1 || { log "ERROR: curl not found — cannot download actionlint"; exit 1; }
  log "download actionlint ${ACTIONLINT_VERSION} -> /usr/local/bin"
  url='https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash'
  # Pipe curl|bash (process substitution under sudo can fail on FDs).
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    curl -sSfL "$url" | sudo bash -s -- "$ACTIONLINT_VERSION" /usr/local/bin
  else
    curl -sSfL "$url" | bash -s -- "$ACTIONLINT_VERSION" /usr/local/bin
  fi
fi

# --- yq (mikefarah/Go): pinned GH-release binary → /usr/local/bin -------------
# release_core.yamlio shells out to mikefarah's `yq -o=json` and `yq eval-all`;
# a kislyuk python-yq (jq wrapper) on /usr/bin/yq has neither and hard-fails the
# gate (release#755). No pip/apt package that matches the fleet, so — exactly like
# actionlint — download the pinned binary to /usr/local/bin (precedes /usr/bin, so
# it shadows any python stub). Reconcile to the pin: gate_version_matches compares
# the first dotted token, 4.x (mikefarah) vs 3.x (stub). Download to a temp file
# and only install once it is NON-EMPTY, so a failed/short download never leaves a
# truncated binary on PATH. The gate is HARD — after the attempt, RE-CHECK and
# exit non-zero if yq still is not at the pin, rather than silently succeeding with
# a missing/wrong yq (release#412). YQ_INSTALL_DIR (default /usr/local/bin) is an
# internal seam the tests point at a sandbox so the install never touches the real
# system path.
_yq_dir="${YQ_INSTALL_DIR:-/usr/local/bin}"
if ! gate_version_matches yq "$YQ_VERSION"; then
  if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
    log "ERROR: need to install yq ${YQ_VERSION} but running as non-root without sudo."
    log "       Re-run as root (or with sudo available), or pre-install mikefarah yq at the pin."
    exit 1
  fi
  command -v curl >/dev/null 2>&1 || { log "ERROR: curl not found — cannot download yq"; exit 1; }
  case "$(uname -s)" in
    Linux) _yq_os=linux ;;
    Darwin) _yq_os=darwin ;;
    *) log "ERROR: unsupported OS '$(uname -s)' for yq install"; exit 1 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) _yq_arch=amd64 ;;
    aarch64|arm64) _yq_arch=arm64 ;;
    *) log "ERROR: unsupported arch '$(uname -m)' for yq install"; exit 1 ;;
  esac
  _yq_url="https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_${_yq_os}_${_yq_arch}"
  log "download yq ${YQ_VERSION} -> ${_yq_dir}"
  # Explicit template — BSD/macOS `mktemp` rejects a bare (template-less) call.
  _yq_tmp="$(mktemp "${TMPDIR:-/tmp}/yq.XXXXXXXX")"
  if curl -sSfL "$_yq_url" -o "$_yq_tmp" && [ -s "$_yq_tmp" ]; then
    chmod +x "$_yq_tmp"
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
      sudo mv "$_yq_tmp" "${_yq_dir}/yq"
    else
      mv "$_yq_tmp" "${_yq_dir}/yq"
    fi
  fi
  rm -f "$_yq_tmp"
  # Hard gate: the download/install above is best-effort, so confirm the outcome.
  gate_version_matches yq "$YQ_VERSION" || {
    log "ERROR: yq is still not at ${YQ_VERSION} after the install attempt (download failed or empty)."
    exit 1
  }
fi

log "done."
