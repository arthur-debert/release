#!/usr/bin/env bash
# Unpack the unsigned macOS .app payload(s) from a downloaded post-hoc
# build artifact, ahead of the sign-notarize-mac reseal (WS4).
#
# The build job's bundle-tauri.sh tars each `.app` as
# `<name>.unsigned-app.tar.gz` (artifact upload doesn't preserve a .app's
# symlinks / exec bits; a tarball round-trips). This extracts them into a
# work dir and writes the extracted .app path to $GITHUB_OUTPUT as `app`.
#
# Exactly one .app is expected per macOS Tauri build; more than one is an
# error (the reusable signer signs a single app/dmg pair).
#
# Env vars:
#   ARTIFACT_DIR  dir holding the downloaded unsigned artifact
#   WORK_DIR      dir to extract the .app into (default: $ARTIFACT_DIR/signing)

set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:?ARTIFACT_DIR required}"
WORK_DIR="${WORK_DIR:-${ARTIFACT_DIR}/signing}"
mkdir -p "${WORK_DIR}"

mapfile -t tarballs < <(find "${ARTIFACT_DIR}" -type f -name '*.unsigned-app.tar.gz')

if [ "${#tarballs[@]}" -eq 0 ]; then
  echo "::error::no *.unsigned-app.tar.gz found under ${ARTIFACT_DIR} — was this an unsigned post-hoc mac build?"
  exit 1
fi
if [ "${#tarballs[@]}" -gt 1 ]; then
  echo "::error::expected one unsigned .app payload, found ${#tarballs[@]}: ${tarballs[*]}"
  exit 1
fi

tar -C "${WORK_DIR}" -xzf "${tarballs[0]}"

app="$(find "${WORK_DIR}" -maxdepth 1 -type d -name '*.app' | head -1)"
[ -n "${app}" ] || { echo "::error::no .app extracted from ${tarballs[0]}"; exit 1; }

echo "extracted ${app}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "app=${app}" >> "${GITHUB_OUTPUT}"
else
  echo "app=${app}"
fi
