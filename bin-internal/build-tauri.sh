#!/usr/bin/env bash
# Build entry resolution for Tauri apps (in preference order):
#   1. app-bin/build-tauri.sh in the CONSUMER repo — legacy consumer-authored
#      hook (tracked by the consumer, so it survives a plain checkout)
#   2. npx tauri build — the standard entry
#
# (A "prefer bin/build" branch used to sit first; it was dead code post-WS7:
# bin/build is an untracked ephemeral mirror, absent on a plain checkout, so
# the guard never fired in CI — removed per release#588/#590.)
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")
#   BUNDLES      bundle targets (passed to --bundles; empty = tauri.conf default)
#   BUILD_PHASE  all (default) | compile
#                  all     — `tauri build` (compile + bundle), the inline path
#                  compile — `tauri build --no-bundle` (compile only); the
#                            post-hoc path bundles separately via
#                            bundle-tauri.sh so the bundle stays unsigned.
#   REPO_ROOT    consumer repo root (default: $GITHUB_WORKSPACE or cwd)
#
# The consumer hook (app-bin/build-tauri.sh) only fires in the default
# `all` phase — a consumer that opts into post-hoc signing uses the
# standard `npx tauri build`/`bundle` entry (the hook predates the split).

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
REPO_ROOT="${REPO_ROOT:-${GITHUB_WORKSPACE:-$(pwd)}}"
BUILD_PHASE="${BUILD_PHASE:-all}"

cd "${TAURI_DIR}"

extra=()
if [ -n "${BUNDLES:-}" ]; then
  extra=(--bundles "${BUNDLES}")
fi

case "${BUILD_PHASE}" in
  compile)
    # Compile only — no bundle, no sign. Runs tauri's beforeBuildCommand
    # (frontend / wasm) then cargo. The bundle step follows separately.
    npx tauri build --no-bundle
    ;;
  all)
    if [ -f "${REPO_ROOT}/app-bin/build-tauri.sh" ]; then
      bash "${REPO_ROOT}/app-bin/build-tauri.sh" "${extra[@]+"${extra[@]}"}"
    else
      npx tauri build "${extra[@]+"${extra[@]}"}"
    fi
    ;;
  *)
    echo "::error::unknown BUILD_PHASE: ${BUILD_PHASE} (expected all|compile)"
    exit 1
    ;;
esac
