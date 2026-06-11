#!/usr/bin/env bash
# Canonical packaged-bundle smoke test for Tauri apps.
#
# Convention hook: release/'s tauri-app.yml detects this via
# hashFiles('app-bin/smoke-hook.sh') and runs it per-platform after
# bundle collection, before artifact upload.
#
# Env vars (provided by the workflow):
#   PLATFORM    mac | linux | windows
#   BUNDLE_DIR  absolute path to collected bundles
#   TAURI_DIR   tauri project directory (default ".")
#   APP_NAME    product name from tauri.conf.json
#
# Read-only checks only — runs after signing/notarization.
set -euo pipefail

: "${APP_NAME:?smoke.sh: APP_NAME env var is required}"
: "${BUNDLE_DIR:?smoke.sh: BUNDLE_DIR env var is required}"
: "${PLATFORM:?smoke.sh: PLATFORM env var is required}"

echo "smoke.sh: PLATFORM=${PLATFORM} APP_NAME=${APP_NAME}"
echo "smoke.sh: bundle contents:"
ls -la "${BUNDLE_DIR}"

case "${PLATFORM}" in
  mac)
    dmg="$(find "${BUNDLE_DIR}" -maxdepth 1 -name "${APP_NAME}_*.dmg" -print -quit)"
    if [ -z "${dmg}" ]; then
      echo "smoke.sh: no ${APP_NAME}_*.dmg found in ${BUNDLE_DIR}" >&2
      exit 1
    fi
    echo "smoke.sh: mounting ${dmg}"
    mount_point="$(mktemp -d /tmp/smoke-test.XXXXXX)"
    trap 'hdiutil detach "${mount_point}" -quiet 2>/dev/null || true; rm -rf "${mount_point}"' EXIT
    hdiutil attach -nobrowse -readonly -mountpoint "${mount_point}" "${dmg}"

    app_bundle="${mount_point}/${APP_NAME}.app"
    if [ ! -d "${app_bundle}" ]; then
      echo "smoke.sh: ${APP_NAME}.app not found inside mounted dmg" >&2
      ls -la "${mount_point}" >&2 || true
      exit 1
    fi

    app_binary="${app_bundle}/Contents/MacOS/${APP_NAME}"
    if [ ! -x "${app_binary}" ]; then
      echo "smoke.sh: binary missing or not executable: ${app_binary}" >&2
      exit 1
    fi
    file "${app_binary}" | grep -q 'Mach-O' || {
      echo "smoke.sh: binary is not a Mach-O: ${app_binary}" >&2
      exit 1
    }
    echo "smoke.sh: macOS smoke OK"
    ;;

  linux)
    appimage="$(find "${BUNDLE_DIR}" -maxdepth 1 -iname '*.AppImage' -print -quit)"
    if [ -z "${appimage}" ]; then
      echo "smoke.sh: no .AppImage found in ${BUNDLE_DIR}" >&2
      exit 1
    fi
    if [ ! -x "${appimage}" ]; then
      echo "smoke.sh: AppImage not executable: ${appimage}" >&2
      exit 1
    fi
    file "${appimage}" | grep -q 'ELF' || {
      echo "smoke.sh: AppImage is not an ELF: ${appimage}" >&2
      exit 1
    }
    deb="$(find "${BUNDLE_DIR}" -maxdepth 1 -name '*.deb' -print -quit || true)"
    if [ -n "${deb}" ]; then
      [ -s "${deb}" ] || { echo "smoke.sh: .deb exists but is empty" >&2; exit 1; }
      echo "smoke.sh: .deb present (${deb})"
    fi
    echo "smoke.sh: linux smoke OK"
    ;;

  windows)
    found=""
    for ext in msi exe; do
      art="$(find "${BUNDLE_DIR}" -maxdepth 1 -iname "*.${ext}" -print -quit || true)"
      if [ -n "${art}" ]; then
        [ -s "${art}" ] || { echo "smoke.sh: ${art} is empty" >&2; exit 1; }
        echo "smoke.sh: found installer ${art}"
        found="${art}"
      fi
    done
    if [ -z "${found}" ]; then
      echo "smoke.sh: no .msi or .exe installer found in ${BUNDLE_DIR}" >&2
      exit 1
    fi
    echo "smoke.sh: windows smoke OK"
    ;;

  *)
    echo "smoke.sh: unknown PLATFORM='${PLATFORM}' (expected mac|linux|windows)" >&2
    exit 2
    ;;
esac
