#!/usr/bin/env bash
# Export Apple signing + notarization env vars so `tauri build`
# picks them up. Tauri handles keychain import, codesign, and
# notarytool submission internally when these are set.
#
# Env vars (set by the workflow from secrets):
#   APPLE_CERTIFICATE           base64 .p12
#   APPLE_CERTIFICATE_PASSWORD  .p12 passphrase (optional)
#   APPLE_SIGNING_IDENTITY      e.g. "Developer ID Application: ..."
#   APPLE_ID                    Apple ID email (notarization)
#   APPLE_PASSWORD              app-specific password (notarization)
#   APPLE_TEAM_ID               team ID (notarization)
#   SIGNING_ONLY                "true" to skip notarization vars
#
# Writes to $GITHUB_ENV (CI) or exports to current shell (local).

set -euo pipefail

emit() {
  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "$1" >> "${GITHUB_ENV}"
  else
    echo "$1"
  fi
}

emit "APPLE_CERTIFICATE=${APPLE_CERTIFICATE:-}"
emit "APPLE_CERTIFICATE_PASSWORD=${APPLE_CERTIFICATE_PASSWORD:-}"
emit "APPLE_SIGNING_IDENTITY=${APPLE_SIGNING_IDENTITY:-}"

if [ "${SIGNING_ONLY:-false}" != "true" ]; then
  emit "APPLE_ID=${APPLE_ID:-}"
  emit "APPLE_PASSWORD=${APPLE_PASSWORD:-}"
  emit "APPLE_TEAM_ID=${APPLE_TEAM_ID:-}"
fi
