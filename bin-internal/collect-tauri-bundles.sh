#!/usr/bin/env bash
# Collect distributable bundle artifacts from the Tauri build output.
#
# Env vars:
#   TAURI_DIR   path to Tauri project root (default: ".")
#   PLATFORM    mac | linux | windows
#
# Writes dir=<path> to $GITHUB_OUTPUT (or stdout when unset).

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
bundle_root="${TAURI_DIR}/src-tauri/target/release/bundle"

if [ ! -d "${bundle_root}" ]; then
  echo "::error::bundle root not found: ${bundle_root}"
  exit 1
fi

out="${GITHUB_WORKSPACE:-$(pwd)}/bundle-${PLATFORM:?PLATFORM required}"
mkdir -p "${out}"

# Distributable artifacts per platform:
#   mac:     .dmg, .app.tar.gz (updater), .unsigned-app.tar.gz (post-hoc
#            reseal payload — present only on the post-hoc unsigned build,
#            consumed by sign-notarize-mac, not shipped)
#   linux:   .deb, .rpm, .AppImage
#   windows: .msi, .exe (NSIS)
while IFS= read -r f; do
  cp "${f}" "${out}/"
done < <(find "${bundle_root}" -type f \
           \( -name '*.dmg' \
              -o -name '*.app.tar.gz' \
              -o -name '*.unsigned-app.tar.gz' \
              -o -name '*.deb' \
              -o -name '*.rpm' \
              -o -name '*.AppImage' \
              -o -name '*.msi' \
              -o -name '*.exe' \))

count=$(find "${out}" -maxdepth 1 -type f | wc -l | tr -d ' ')
echo "Collected ${count} artifacts:"
ls -la "${out}"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "dir=${out}" >> "${GITHUB_OUTPUT}"
else
  echo "dir=${out}"
fi
