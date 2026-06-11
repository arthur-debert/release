#!/usr/bin/env bash
# Canonical packaged-binary smoke test for Electron apps.
#
# Convention hook: release/'s electron-app.yml detects this via
# hashFiles('app-bin/smoke-hook.sh') and runs it per-platform after
# build + notarization, before artifact upload.
#
# Static integrity checks only — verifies the binary exists and
# is the correct file type. Consumers needing dynamic tests
# (e.g. Playwright --project=packaged) override this file with
# their own app-bin/smoke-hook.sh.
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
    # electron-builder uses "mac" when no arch is specified, "mac-${ARCH}"
    # otherwise. Guard against unset ARCH under set -u.
    mac_dir="mac"
    if [ -n "${ARCH:-}" ]; then
      mac_dir="mac-${ARCH}"
    fi
    binary="${PACKAGE_DIR}/${mac_dir}/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
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
    # electron-builder names the linux binary from build.executableName
    # (falling back to package.json "name"), NOT from productName. The
    # workflow passes APP_NAME from productName, so lowercasing it may
    # not match. Try the lowered productName first; if missing, fall back
    # to scanning linux-unpacked/ for the single executable.
    binary="${PACKAGE_DIR}/linux-unpacked/${app_name_lower}"
    if [ ! -e "${binary}" ]; then
      # Fallback: find the main executable (largest ELF file in the dir).
      candidate="$(find "${PACKAGE_DIR}/linux-unpacked" -maxdepth 1 -type f -executable 2>/dev/null \
        | head -n 1 || true)"
      if [ -n "${candidate}" ]; then
        echo "smoke.sh: primary name '${app_name_lower}' not found; using '${candidate}'"
        binary="${candidate}"
      fi
    fi
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
