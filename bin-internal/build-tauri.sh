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
#   TAURI_DIR   path to Tauri project root (default: ".")
#   BUNDLES     bundle targets (passed to --bundles; empty = tauri.conf default)
#   REPO_ROOT   consumer repo root (default: $GITHUB_WORKSPACE or cwd)

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
REPO_ROOT="${REPO_ROOT:-${GITHUB_WORKSPACE:-$(pwd)}}"

cd "${TAURI_DIR}"

extra=()
if [ -n "${BUNDLES:-}" ]; then
  extra=(--bundles "${BUNDLES}")
fi

if [ -f "${REPO_ROOT}/app-bin/build-tauri.sh" ]; then
  bash "${REPO_ROOT}/app-bin/build-tauri.sh" "${extra[@]+"${extra[@]}"}"
else
  npx tauri build "${extra[@]+"${extra[@]}"}"
fi
