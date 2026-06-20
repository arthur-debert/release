#!/usr/bin/env bash
# Flatten the per-platform Tauri bundle artifacts into a single dir of
# release assets, sign-mode-agnostic (WS5, #816).
#
# The release job downloads each platform's artifact into its own subdir
# (NOT merge-multiple) so the signed and unsigned mac `.dmg` — which share a
# filename — never collide on merge. This script copies the right set into
# $ASSETS_DIR:
#
#   - post-hoc + mac: ship `bundle-mac-signed/` (the codesigned + notarized
#     dmg) and SKIP the unsigned `bundle-mac/` entirely — its
#     `.unsigned-app.tar.gz` reseal payload and unsigned `.dmg` must never
#     reach the release.
#   - inline (or post-hoc without mac): ship `bundle-mac/` (tauri signed it
#     in-build); there is no `bundle-mac-signed/`.
#   - linux / windows: always shipped from their own `bundle-<platform>/`
#     dirs (one signing mode, no signed variant).
#
# Partial-release prevention lives in the job graph (a failed build/sign
# blocks this job); this script just selects assets.
#
# Env vars:
#   DOWNLOAD_DIR   dir holding the per-artifact subdirs (bundle-*/)
#   ASSETS_DIR     output dir to populate with the flat asset set
#   SIGN_MODE      inline | post-hoc
#   BUILD_MAC      true | false

set -euo pipefail

DOWNLOAD_DIR="${DOWNLOAD_DIR:?DOWNLOAD_DIR required}"
ASSETS_DIR="${ASSETS_DIR:?ASSETS_DIR required}"
SIGN_MODE="${SIGN_MODE:-inline}"
BUILD_MAC="${BUILD_MAC:-true}"

mkdir -p "${ASSETS_DIR}"

post_hoc_mac=false
if [ "${SIGN_MODE}" = "post-hoc" ] && [ "${BUILD_MAC}" = "true" ]; then
  post_hoc_mac=true
fi

shipped=0
for dir in "${DOWNLOAD_DIR}"/bundle-*/; do
  [ -d "${dir}" ] || continue
  name="$(basename "${dir}")"

  if [ "${post_hoc_mac}" = "true" ]; then
    # Post-hoc mac: the signed dmg ships, the unsigned mac bundle does not.
    case "${name}" in
      bundle-mac) echo "skip ${name} (unsigned; superseded by bundle-mac-signed)"; continue ;;
    esac
  else
    # Inline (or no mac): there is no signed variant to ship.
    case "${name}" in
      bundle-mac-signed) echo "skip ${name} (only present in post-hoc mode)"; continue ;;
    esac
  fi

  # Copy this artifact's files (flat) into the asset dir.
  while IFS= read -r f; do
    cp "${f}" "${ASSETS_DIR}/"
    shipped=$((shipped + 1))
  done < <(find "${dir}" -type f)
done

if [ "${shipped}" -eq 0 ]; then
  echo "::error::no release assets staged from ${DOWNLOAD_DIR} (sign-mode=${SIGN_MODE}, build-mac=${BUILD_MAC})"
  exit 1
fi

echo "staged ${shipped} asset(s):"
ls -la "${ASSETS_DIR}"
