#!/usr/bin/env bash
# LOCAL runner for the linux bundle smoke test (#841). The dev box is a mac, so
# the linux path runs in an `ubuntu:24.04` container (matching ubuntu-latest);
# the mac dmg/reseal path runs natively and is NOT covered here.
#
# One command:
#   bin-internal/bundle-smoke-local.sh
#   CONSUMER_REPO=phos-editor/app FORMATS="deb appimage" bin-internal/bundle-smoke-local.sh
#
# It mounts this release checkout + a fresh consumer clone into the container,
# installs the package-job's runtime deps + node/pnpm + cargo (container
# bootstrap the GH runner already provides), restores the latest consumer
# compile-linux artifact, and runs bundle-smoke.sh. Requires Docker + a `gh`
# login on the host (the token is passed in via GH_TOKEN).
#
# Env vars:
#   CONSUMER_REPO  consumer to pull source + artifact from (default phos-editor/app)
#   FORMATS        formats to assert (default "deb appimage")
#   GH_TOKEN       overrides the host `gh auth token`
set -euo pipefail

CONSUMER_REPO="${CONSUMER_REPO:-phos-editor/app}"
FORMATS="${FORMATS:-deb appimage}"
RELEASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Check tools BEFORE using them — under `set -e` a missing `gh` would otherwise
# die with a generic "command not found" instead of these helpful messages.
command -v docker >/dev/null 2>&1 || { echo "::error::docker not found — needed for the local linux smoke test"; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "::error::gh CLI not found — needed to pull the consumer artifact (and for the token)"; exit 1; }

GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null || true)}"
[ -n "${GH_TOKEN}" ] || { echo "::error::no GH_TOKEN and \`gh auth token\` empty — run \`gh auth login\` or pass GH_TOKEN"; exit 1; }

echo "Cloning ${CONSUMER_REPO} (shallow) for the project tree…"
clone="$(mktemp -d)"
trap 'rm -rf "${clone}"' EXIT
GH_TOKEN="${GH_TOKEN}" gh repo clone "${CONSUMER_REPO}" "${clone}/app" -- --depth 1 >/dev/null 2>&1 \
  || { echo "::error::failed to clone ${CONSUMER_REPO}"; exit 1; }

# The in-container script: install deps, resolve+restore the artifact, run smoke.
# Mirrors the CI job, plus the container-only bootstrap (node/pnpm/cargo/file)
# the GH ubuntu runner already ships. APPIMAGE_EXTRACT_AND_RUN=1: linuxdeploy's
# AppImages need FUSE, which a container lacks (does NOT change the dep answer).
# Capture the container exit explicitly (rc=0 guarded), so a failure inside the
# container can never be masked into a passing local run.
rc=0
docker run --rm \
  -e GH_TOKEN="${GH_TOKEN}" \
  -e CONSUMER_REPO="${CONSUMER_REPO}" \
  -e FORMATS="${FORMATS}" \
  -e APPIMAGE_EXTRACT_AND_RUN=1 \
  -e DEBIAN_FRONTEND=noninteractive \
  -v "${RELEASE_DIR}:/release:ro" \
  -v "${clone}/app:/app" \
  ubuntu:24.04 bash -euo pipefail -c '
    export PATH="/root/.cargo/bin:$PATH"
    apt-get update -qq >/tmp/apt.log 2>&1
    # Bootstrap deps the GH ubuntu runner already ships but a bare container
    # lacks: `file` (appimagetool needs file(1)); `sudo` (the install-deps script
    # calls `sudo apt-get`, and the runner has passwordless sudo); `jq` (the
    # bundle scripts read tauri.conf with it); curl/git/ca-certs (node/cargo/gh).
    apt-get install -y -qq ca-certificates curl git file sudo jq gh >>/tmp/apt.log 2>&1 \
      || { echo "bootstrap apt install failed"; tail -20 /tmp/apt.log; exit 1; }
    # The actual package-job linux deps under test:
    bash /release/bin-internal/install-tauri-linux-deps-package.sh
    # node + pnpm + cargo (runner provides via setup-node + image; here bootstrap)
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >>/tmp/apt.log 2>&1
    apt-get install -y -qq nodejs >>/tmp/apt.log 2>&1
    corepack enable >>/tmp/apt.log 2>&1
    if ! command -v cargo >/dev/null; then
      curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable >>/tmp/apt.log 2>&1
      . "$HOME/.cargo/env"
    fi
    cd /app
    git init -q 2>/dev/null || true   # some consumer prepare scripts want a repo
    # Resolve + restore the latest consumer compile-linux artifact.
    CHECKOUT_DIR=/app PLATFORM=linux bash /release/bin-internal/resolve-compile-artifact.sh
    # JS deps so `npx tauri` resolves the CLI (ignore-scripts: skip consumer hooks).
    pnpm install --frozen-lockfile --ignore-scripts >/tmp/pnpm.log 2>&1 \
      || pnpm install --ignore-scripts >/tmp/pnpm.log 2>&1 \
      || { echo "pnpm install failed"; tail -20 /tmp/pnpm.log; exit 1; }
    # Run the smoke test.
    CHECKOUT_DIR=/app PLATFORM=linux FORMATS="'"${FORMATS}"'" \
      bash /release/bin-internal/bundle-smoke.sh
  ' || rc=$?

if [ "${rc}" -ne 0 ]; then
  echo "::error::local bundle smoke FAILED (container exit ${rc})"
  exit "${rc}"
fi
echo "local bundle smoke PASSED (${PLATFORM:-linux} ${FORMATS})"
