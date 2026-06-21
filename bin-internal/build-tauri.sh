#!/usr/bin/env bash
# Compile a Tauri app's Rust binary WITHOUT bundling and WITHOUT (re-)running
# the frontend build.
#
# This is the RUST-COMPILE half of the decomposed pipeline. The frontend
# (tauri's `beforeBuildCommand`: JS/WASM) is built in a SEPARATE prior step
# (build-frontend-tauri.sh) so the run UI shows per-phase timing (#835): an
# uncacheable JS/WASM build vs. the sccache/rust-cache'd rust compile. To keep
# this step purely the rust compile, we DISABLE `beforeBuildCommand` here via a
# config override (`-c '{"build":{"beforeBuildCommand":""}}'`) — an empty
# command is a no-op, so tauri does NOT re-run the frontend build that the prior
# step already produced.
#
# Within the broader pipeline this build job only ever compiles: it stages the
# compiled binary + built frontendDist as the `compile-<platform>` artifact, and
# a separate `package` job bundles it (bundle-tauri.sh), unsigned; an optional
# `sign` job signs it afterwards. There is no fused compile+bundle+sign path.
# `tauri bundle` does NOT re-run the frontend either, so the frontendDist staged
# here is what gets bundled.
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"

cd "${TAURI_DIR}"

# `-c '{"build":{"beforeBuildCommand":""}}'` merges over the loaded
# tauri.conf.json, blanking beforeBuildCommand so the rust compile does not
# re-run the frontend build (build-frontend-tauri.sh ran it already).
npx tauri build --no-bundle -c '{"build":{"beforeBuildCommand":""}}'
