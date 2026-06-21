#!/usr/bin/env bash
# Flatten the per-platform Tauri bundle artifacts into a single dir of
# release assets, sign-agnostic (WS5, #816).
#
# The release job downloads each platform's artifact into its own subdir
# (NOT merge-multiple) so the signed and unsigned mac `.dmg` — which share a
# filename — never collide on merge. This script copies the right set into
# $ASSETS_DIR:
#
#   - signed + mac: ship `bundle-mac-signed/` (the codesigned + notarized
#     dmg) and SKIP the unsigned `bundle-mac/` entirely — its
#     `.unsigned-app.tar.gz` reseal payload and unsigned `.dmg` must never
#     reach the release.
#   - unsigned (or no mac): ship `bundle-mac/` (the unsigned bundle); there
#     is no `bundle-mac-signed/`.
#   - linux / windows: always shipped from their own `bundle-<platform>/`
#     dirs (never signed in this pipeline).
#
# Partial-release prevention: the job graph blocks this job on a failed
# build/package/sign, AND this script HARD-FAILS if the expected mac bundle for
# the signing state is missing/empty (a missing-but-expected artifact must not
# silently ship a mac-less release). See the bundle_nonempty check below.
#
# Env vars:
#   DOWNLOAD_DIR   dir holding the per-artifact subdirs (bundle-*/)
#   ASSETS_DIR     output dir to populate with the flat asset set
#   SIGNED         true | false  (did the mac sign job run? = preflight's
#                  `should-sign`)
#   BUILD_MAC      true | false

set -euo pipefail

DOWNLOAD_DIR="${DOWNLOAD_DIR:?DOWNLOAD_DIR required}"
ASSETS_DIR="${ASSETS_DIR:?ASSETS_DIR required}"
SIGNED="${SIGNED:-false}"
BUILD_MAC="${BUILD_MAC:-true}"

# Fail fast on a misconfigured flag rather than silently doing the wrong thing:
# a non-bool SIGNED (e.g. a `yes` typo) would otherwise be treated as "unsigned"
# and stage the UNSIGNED mac bundle even when the signed one is the one to ship.
case "${SIGNED}" in
  true|false) ;;
  *) echo "::error::unknown SIGNED: '${SIGNED}' (expected true|false)" >&2; exit 1 ;;
esac
case "${BUILD_MAC}" in
  true|false) ;;
  *) echo "::error::unknown BUILD_MAC: '${BUILD_MAC}' (expected true|false)" >&2; exit 1 ;;
esac

# Idempotent output: clear any prior contents so a rerun / local reuse never
# ships stale assets (and BATS can't pass through a regression on leftovers).
rm -rf "${ASSETS_DIR}"
mkdir -p "${ASSETS_DIR}"

signed_mac=false
if [ "${SIGNED}" = "true" ] && [ "${BUILD_MAC}" = "true" ]; then
  signed_mac=true
fi

# True if a bundle subdir exists and holds at least one file.
bundle_nonempty() {
  local d="${DOWNLOAD_DIR}/$1"
  [ -d "${d}" ] && [ -n "$(find "${d}" -type f -print -quit 2>/dev/null)" ]
}

# Partial-release guard: when mac is built, the expected mac bundle MUST be
# present and non-empty, else hard-error rather than ship a mac-less release.
if [ "${BUILD_MAC}" = "true" ]; then
  if [ "${signed_mac}" = "true" ]; then
    bundle_nonempty bundle-mac-signed || {
      echo "::error::signed + build-mac: signed mac bundle (bundle-mac-signed) is missing or empty — refusing a partial (mac-less) release"
      exit 1
    }
  else
    bundle_nonempty bundle-mac || {
      echo "::error::unsigned + build-mac: mac bundle (bundle-mac) is missing or empty — refusing a partial (mac-less) release"
      exit 1
    }
  fi
fi

shipped=0
for dir in "${DOWNLOAD_DIR}"/bundle-*/; do
  [ -d "${dir}" ] || continue
  name="$(basename "${dir}")"

  if [ "${signed_mac}" = "true" ]; then
    # Signed mac: the signed dmg ships, the unsigned mac bundle does not.
    case "${name}" in
      bundle-mac) echo "skip ${name} (unsigned; superseded by bundle-mac-signed)"; continue ;;
    esac
  else
    # Unsigned (or no mac): there is no signed variant to ship.
    case "${name}" in
      bundle-mac-signed) echo "skip ${name} (only present when signing ran)"; continue ;;
    esac
  fi

  # Copy this artifact's files (flat) into the asset dir, EXCLUDING the
  # `*.unsigned-app.tar.gz` reseal payload on every path. That tarball is
  # purely the signer's input (the post-hoc unsigned .app, consumed by
  # sign-notarize-mac); it is never a deliverable. The signed path already
  # drops it by skipping bundle-mac wholesale, but the unsigned path ships
  # bundle-mac directly, so the exclusion must live here to catch it.
  #
  # ONLY `*.unsigned-app.tar.gz` is excluded — NOT `*.app.tar.gz`. The latter
  # (+ its `.sig`) is tauri's UPDATER bundle, a legitimate auto-update
  # deliverable; this loop deliberately does NOT blanket-exclude it (that would
  # silently break updater consumers). What actually reaches the release still
  # depends on the path: on the UNSIGNED path bundle-mac ships directly, so the
  # updater bundle ships; on the SIGNED path bundle-mac is skipped wholesale and
  # only bundle-mac-signed ships, so the updater bundle ships only if the signer
  # carries it into bundle-mac-signed — which it does NOT yet (tracked as #845).
  # phos configures no updater, so it produces none today regardless.
  while IFS= read -r f; do
    case "$(basename "${f}")" in
      *.unsigned-app.tar.gz) echo "skip $(basename "${f}") (signer reseal payload; never a deliverable)"; continue ;;
    esac
    cp "${f}" "${ASSETS_DIR}/"
    shipped=$((shipped + 1))
  done < <(find "${dir}" -type f)
done

if [ "${shipped}" -eq 0 ]; then
  echo "::error::no release assets staged from ${DOWNLOAD_DIR} (signed=${SIGNED}, build-mac=${BUILD_MAC})"
  exit 1
fi

# Mac deliverable invariant: when mac is built, the staged set must contain
# EXACTLY ONE `.dmg` (the one signed + notarized dmg the maintainer wants) and
# ZERO `.unsigned-app.tar.gz` reseal payloads. Fail loud rather than publish a
# release with no dmg, two colliding dmgs, or the signer's internal payload.
if [ "${BUILD_MAC}" = "true" ]; then
  dmg_count=$(find "${ASSETS_DIR}" -maxdepth 1 -type f -name '*.dmg' | wc -l | tr -d ' ')
  reseal_count=$(find "${ASSETS_DIR}" -maxdepth 1 -type f -name '*.unsigned-app.tar.gz' | wc -l | tr -d ' ')
  if [ "${dmg_count}" -ne 1 ]; then
    echo "::error::mac deliverable invariant: expected exactly one .dmg in the release assets, found ${dmg_count}"
    exit 1
  fi
  if [ "${reseal_count}" -ne 0 ]; then
    echo "::error::mac deliverable invariant: a *.unsigned-app.tar.gz reseal payload reached the release assets (found ${reseal_count}) — it is never a deliverable"
    exit 1
  fi
fi

echo "staged ${shipped} asset(s):"
ls -la "${ASSETS_DIR}"
