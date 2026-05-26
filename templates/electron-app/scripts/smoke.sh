#!/usr/bin/env bash
# Canonical packaged-binary smoke test for Electron apps.
#
# Convention hook: release/'s electron-app.yml detects this via
# hashFiles('scripts/smoke.sh') and runs it per-platform after
# build + notarization, before artifact upload.
#
# Static integrity checks only — verifies the binary exists and
# is the correct file type. Consumers needing dynamic tests
# (e.g. Playwright --project=packaged) override this file with
# their own scripts/smoke.sh.
#
# Env vars (provided by the workflow):
#   PLATFORM     mac | linux | windows
#   ARCH         arm64 | x64 | ...
#   PACKAGE_DIR  electron-builder output directory
#   APP_NAME     product name from package.json build config
set -euo pipefail

: "${APP_NAME:?smoke.sh: APP_NAME env var is required}"
: "${PACKAGE_DIR:?smoke.sh: PACKAGE_DIR env var is required}"
: "${PLATFORM:?smoke.sh: PLATFORM env var is required}"

app_name_lower="$(echo "${APP_NAME}" | tr '[:upper:]' '[:lower:]')"

echo "smoke.sh: PLATFORM=${PLATFORM} ARCH=${ARCH:-} APP_NAME=${APP_NAME}"

case "${PLATFORM}" in
  mac)
    binary="${PACKAGE_DIR}/mac-${ARCH}/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
    if [ ! -e "${binary}" ]; then
      echo "smoke.sh: binary not found: ${binary}" >&2
      echo "smoke.sh: PACKAGE_DIR contents:" >&2
      ls -la "${PACKAGE_DIR}" >&2 || true
      exit 1
    fi
    file "${binary}" | grep -q 'Mach-O' || {
      echo "smoke.sh: not a Mach-O: ${binary}" >&2
      exit 1
    }
    echo "smoke.sh: macOS smoke OK (${binary})"
    ;;

  linux)
    binary="${PACKAGE_DIR}/linux-unpacked/${app_name_lower}"
    if [ ! -e "${binary}" ]; then
      echo "smoke.sh: binary not found: ${binary}" >&2
      echo "smoke.sh: PACKAGE_DIR contents:" >&2
      ls -la "${PACKAGE_DIR}" >&2 || true
      exit 1
    fi
    file "${binary}" | grep -q 'ELF' || {
      echo "smoke.sh: not an ELF: ${binary}" >&2
      exit 1
    }
    echo "smoke.sh: linux smoke OK (${binary})"
    ;;

  windows)
    binary="${PACKAGE_DIR}/win-unpacked/${APP_NAME}.exe"
    if [ ! -e "${binary}" ]; then
      echo "smoke.sh: binary not found: ${binary}" >&2
      echo "smoke.sh: PACKAGE_DIR contents:" >&2
      ls -la "${PACKAGE_DIR}" >&2 || true
      exit 1
    fi
    [ -s "${binary}" ] || {
      echo "smoke.sh: binary is empty: ${binary}" >&2
      exit 1
    }
    echo "smoke.sh: windows smoke OK (${binary})"
    ;;

  *)
    echo "smoke.sh: unknown PLATFORM='${PLATFORM}' (expected mac|linux|windows)" >&2
    exit 2
    ;;
esac
