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

# Collect the payload tarballs. `mapfile`/`readarray` is bash 4+; the macOS
# runner ships bash 3.2, so read into the array with a portable while-loop.
tarballs=()
while IFS= read -r t; do
  tarballs+=("${t}")
done < <(find "${ARTIFACT_DIR}" -type f -name '*.unsigned-app.tar.gz')

if [ "${#tarballs[@]}" -eq 0 ]; then
  echo "::error::no *.unsigned-app.tar.gz found under ${ARTIFACT_DIR} — was this an unsigned post-hoc mac build?"
  exit 1
fi
if [ "${#tarballs[@]}" -gt 1 ]; then
  echo "::error::expected one unsigned .app payload, found ${#tarballs[@]}: ${tarballs[*]}"
  exit 1
fi

tar -C "${WORK_DIR}" -xzf "${tarballs[0]}"

# Exactly one .app must come out — fail loudly on zero or many rather than
# silently signing an arbitrary one.
apps=()
while IFS= read -r a; do
  apps+=("${a}")
done < <(find "${WORK_DIR}" -maxdepth 1 -type d -name '*.app')

if [ "${#apps[@]}" -eq 0 ]; then
  echo "::error::no .app extracted from ${tarballs[0]}"
  exit 1
fi
if [ "${#apps[@]}" -gt 1 ]; then
  echo "::error::expected one .app in ${tarballs[0]}, found ${#apps[@]}: ${apps[*]}"
  exit 1
fi
app="${apps[0]}"

echo "extracted ${app}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "app=${app}" >> "${GITHUB_OUTPUT}"
else
  echo "app=${app}"
fi
