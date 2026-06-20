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
# Partial-release prevention: the job graph blocks this job on a failed
# build/sign, AND this script HARD-FAILS if the expected mac bundle for the
# mode is missing/empty (a missing-but-expected artifact must not silently ship
# a mac-less release). See the require_nonempty check below.
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

# Idempotent output: clear any prior contents so a rerun / local reuse never
# ships stale assets (and BATS can't pass through a regression on leftovers).
rm -rf "${ASSETS_DIR}"
mkdir -p "${ASSETS_DIR}"

post_hoc_mac=false
if [ "${SIGN_MODE}" = "post-hoc" ] && [ "${BUILD_MAC}" = "true" ]; then
  post_hoc_mac=true
fi

# True if a bundle subdir exists and holds at least one file.
bundle_nonempty() {
  local d="${DOWNLOAD_DIR}/$1"
  [ -d "${d}" ] && [ -n "$(find "${d}" -type f -print -quit 2>/dev/null)" ]
}

# Partial-release guard: when mac is built, the mode's expected mac bundle MUST
# be present and non-empty, else hard-error rather than ship a mac-less release.
if [ "${BUILD_MAC}" = "true" ]; then
  if [ "${post_hoc_mac}" = "true" ]; then
    bundle_nonempty bundle-mac-signed || {
      echo "::error::post-hoc + build-mac: signed mac bundle (bundle-mac-signed) is missing or empty — refusing a partial (mac-less) release"
      exit 1
    }
  else
    bundle_nonempty bundle-mac || {
      echo "::error::inline + build-mac: mac bundle (bundle-mac) is missing or empty — refusing a partial (mac-less) release"
      exit 1
    }
  fi
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
