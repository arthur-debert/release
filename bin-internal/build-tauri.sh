#!/usr/bin/env bash
# Compile a Tauri app's Rust binary + frontend WITHOUT bundling.
#
# This is the COMPILE half of the decomposed pipeline: the build job only
# ever compiles (`tauri build --no-bundle`), stages the compiled binary +
# built frontendDist as the `compile-<platform>` artifact, and a separate
# `package` job bundles it (bundle-tauri.sh). The bundle is produced unsigned;
# an optional `sign` job signs it afterwards. There is no fused
# compile+bundle+sign path anymore (the old `inline` sign-mode was removed —
# one decomposed pipeline, signing is an optional step).
#
# `tauri build --no-bundle` runs tauri's beforeBuildCommand (frontend / wasm)
# then cargo, so the built frontendDist is produced here and carried over to
# the package job (`tauri bundle` does NOT re-run the frontend build).
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"

cd "${TAURI_DIR}"

npx tauri build --no-bundle
