#!/usr/bin/env bash
# Bundle a pre-compiled Tauri app UNSIGNED, for ONE format (or format list).
#
# Runs after build-tauri.sh compiles (`tauri build --no-bundle`). The package
# job calls this once PER format (resolve-tauri-bundles.sh decides which formats
# run) so each format is its own named step with its own timing in the run UI
# (#835). `--no-sign` keeps the bundle unsigned so the reusable
# sign-notarize-mac workflow can codesign + notarize it later. Re-running
# `tauri bundle` to re-seal a SIGNED app would strip the signature (the WS0
# spike caveat); the signer reseals with hdiutil instead, never this script.
#
# The mac app-forcing rule (ensuring `app` is bundled so the post-hoc signer has
# a payload) and the post-bundle .app tarball packaging now live in
# resolve-tauri-bundles.sh and package-mac-app.sh respectively — this script is
# purely "bundle the requested format(s)".
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")
#   BUNDLES      bundle format(s) for THIS step (passed to --bundles; empty =
#                tauri.conf default — generally one format per call, e.g. "deb")

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"

cd "${TAURI_DIR}"

bundles="${BUNDLES:-}"

extra=()
if [ -n "${bundles}" ]; then
  extra=(--bundles "${bundles}")
fi

npx tauri bundle --no-sign "${extra[@]+"${extra[@]}"}"
