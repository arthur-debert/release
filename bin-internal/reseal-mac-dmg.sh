#!/usr/bin/env bash
# Reseal a macOS .dmg from a SIGNED .app, post-hoc (WS4 reuse win).
#
# The WS0 spike caveat: re-running `tauri bundle --bundles dmg` regenerates
# the .app from the binary and STRIPS the Developer ID signature. So once the
# inner .app is codesigned, the .dmg must be rebuilt with `hdiutil` from the
# signed .app — never re-bundled. This script does exactly the hdiutil reseal;
# the caller (sign-notarize-mac.yml) codesigns the .app before and the .dmg
# after.
#
# Env vars:
#   APP_PATH    path to the SIGNED .app bundle
#   DMG_OUT     output path for the resealed .dmg
#   VOLNAME     dmg volume name (default: the .app basename without .app)

set -euo pipefail

APP_PATH="${APP_PATH:?APP_PATH required}"
DMG_OUT="${DMG_OUT:?DMG_OUT required}"

[ -d "${APP_PATH}" ] || { echo "::error::APP_PATH not a directory: ${APP_PATH}"; exit 1; }

app_name="$(basename "${APP_PATH}")"
VOLNAME="${VOLNAME:-${app_name%.app}}"

stage="$(mktemp -d)"
trap 'rm -rf "${stage}"' EXIT

cp -R "${APP_PATH}" "${stage}/"
ln -s /Applications "${stage}/Applications"

rm -f "${DMG_OUT}"
hdiutil create \
  -volname "${VOLNAME}" \
  -srcfolder "${stage}" \
  -ov \
  -format UDZO \
  "${DMG_OUT}"

echo "resealed ${APP_PATH} -> ${DMG_OUT}"
