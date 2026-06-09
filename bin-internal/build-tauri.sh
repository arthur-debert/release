#!/usr/bin/env bash
# Build entry resolution for Tauri apps (in preference order):
#   1. bin/build — canonical take-iii entry, release-core init managed
#   2. app-bin/build-tauri.sh in the CONSUMER repo — legacy hook
#   3. npx tauri build — minimal fallback
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

if [ -x "${REPO_ROOT}/bin/build" ]; then
  "${REPO_ROOT}/bin/build" "${extra[@]+"${extra[@]}"}"
elif [ -f "${REPO_ROOT}/app-bin/build-tauri.sh" ]; then
  bash "${REPO_ROOT}/app-bin/build-tauri.sh" "${extra[@]+"${extra[@]}"}"
else
  npx tauri build "${extra[@]+"${extra[@]}"}"
fi
