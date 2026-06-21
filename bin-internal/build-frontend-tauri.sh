#!/usr/bin/env bash
# Build a Tauri app's FRONTEND (the JS/WASM half), as its own pipeline step.
#
# This is the FRONTEND half of the decomposed compile (#835). Tauri normally
# runs the frontend build implicitly as `tauri build`'s `beforeBuildCommand`,
# fusing an uncacheable JS/WASM build with the sccache/rust-cache'd rust compile
# into one opaque step. We split them so the run UI attributes time to each
# phase: this script runs `beforeBuildCommand` EXPLICITLY, then build-tauri.sh
# runs the rust compile with `beforeBuildCommand` DISABLED (so it isn't re-run).
#
# Consumer-agnostic: it reads `build.beforeBuildCommand` from the consumer's
# tauri.conf.json and runs it verbatim via the shell, from TAURI_DIR (the dir
# tauri itself runs beforeBuildCommand from). Absent/empty → no-op (a consumer
# with no frontend build, or one using a dev-server only). The WASM bundle cache
# (restored before this step) still lets wasm-pack short-circuit on a hit, since
# this runs the SAME beforeBuildCommand tauri would have.
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"

cd "${TAURI_DIR}"

conf="src-tauri/tauri.conf.json"
cmd=""
if [ -f "${conf}" ]; then
  cmd=$(jq -r '.build.beforeBuildCommand // empty' "${conf}")
fi

if [ -z "${cmd}" ]; then
  echo "no build.beforeBuildCommand in ${conf} — skipping frontend build"
  exit 0
fi

echo "running frontend build (beforeBuildCommand): ${cmd}"
# Run through the shell so a consumer's compound command (e.g. "pnpm build &&
# wasm-pack build") works exactly as tauri would invoke it.
sh -c "${cmd}"
