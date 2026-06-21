#!/usr/bin/env bash
# Resolve the effective bundle-target list and emit per-format booleans, so the
# package job can run ONE named step per format → per-format timing in the run
# UI (#835). Centralizes the format resolution + the mac app-forcing rule that
# used to live inside bundle-tauri.sh.
#
# Resolution order:
#   1. BUNDLES env (matrix.t.bundles) if non-empty — a comma/space list, or
#      "all".
#   2. else tauri.conf.json `.bundle.targets` — a JSON string ("all" | "deb") or
#      array (["deb","appimage"]).
#   3. else empty → "all" for the platform (tauri's own default is everything).
# "all" expands per platform: linux → deb,rpm,appimage ; mac → app,dmg ;
# windows → nsis,msi.
#
# mac app-forcing rule (load-bearing — guards a real shipped bug): `app` MUST be
# in the mac target list even when a consumer asked only for `dmg`. A dmg-only
# bundle builds the .app then DELETES it, leaving no payload for the post-hoc
# signer to reseal → "no *.unsigned-app.tar.gz found". So we always include
# `app` on mac.
#
# Outputs (to $GITHUB_OUTPUT, else stdout) — per-format booleans the workflow
# gates each bundle step on:
#   deb, rpm, appimage   (linux)
#   app, dmg             (mac)
#   nsis, msi            (windows)
#
# Env vars:
#   BUNDLES    bundle targets (matrix.t.bundles); empty = tauri.conf default
#   TAURI_DIR  path to Tauri project root (default: ".")
#   PLATFORM   mac | linux | windows

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
PLATFORM="${PLATFORM:?PLATFORM required}"

cd "${TAURI_DIR}"

conf="src-tauri/tauri.conf.json"

# 1. Start from the BUNDLES env. Normalize commas to spaces for word-splitting.
raw="${BUNDLES:-}"

# 2. Fall back to tauri.conf .bundle.targets when BUNDLES is empty.
if [ -z "${raw}" ] && [ -f "${conf}" ]; then
  # .bundle.targets may be a string or an array; jq renders either as
  # space-joined words ("all" stays "all").
  raw=$(jq -r '
    (.bundle.targets // empty) as $t
    | if ($t | type) == "array" then ($t | join(" "))
      elif ($t | type) == "string" then $t
      else empty end
  ' "${conf}")
fi

# 3. Still empty → "all".
raw="${raw:-all}"

# Expand "all" per platform. An unknown PLATFORM is a hard error (the repo's
# fail-loud convention, cf. assert-tauri-bundle-binary.sh) — silently resolving
# to no formats would make every output false and the bundle steps skip, failing
# later in collect-tauri-bundles.sh with a confusing "bundle root not found".
case "${PLATFORM}" in
  linux)   all_targets="deb rpm appimage" ;;
  mac)     all_targets="app dmg" ;;
  windows) all_targets="nsis msi" ;;
  *)       echo "::error::unknown PLATFORM: ${PLATFORM} (expected mac | linux | windows)"; exit 1 ;;
esac

# Build the requested set (space-separated), expanding "all".
requested=""
for tok in ${raw//,/ }; do
  if [ "${tok}" = "all" ]; then
    requested="${requested} ${all_targets}"
  else
    requested="${requested} ${tok}"
  fi
done

# mac: always include `app` (reseal payload guarantee, see header).
if [ "${PLATFORM}" = "mac" ]; then
  requested="${requested} app"
fi

# Per-format membership test.
has() {
  local want="$1" t
  for t in ${requested}; do
    [ "${t}" = "${want}" ] && return 0
  done
  return 1
}

emit() {
  local key="$1" val="$2"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "${key}=${val}" >> "${GITHUB_OUTPUT}"
  else
    echo "${key}=${val}"
  fi
}

for fmt in deb rpm appimage app dmg nsis msi; do
  if has "${fmt}"; then emit "${fmt}" "true"; else emit "${fmt}" "false"; fi
done

echo "resolved bundle targets (${PLATFORM}):${requested}" >&2
