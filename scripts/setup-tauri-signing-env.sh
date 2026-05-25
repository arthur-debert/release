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
# CI: writes to $GITHUB_ENV (heredoc form, multiline-safe).
# Local: prints KEY=VALUE to stdout (pipe to `source /dev/stdin`
# or eval to import).

set -euo pipefail

emit() {
  local key="$1"
  local val="$2"
  if [ -n "${GITHUB_ENV:-}" ]; then
    {
      echo "${key}<<__GHENV_EOF__"
      echo "${val}"
      echo "__GHENV_EOF__"
    } >> "${GITHUB_ENV}"
  else
    echo "${key}=${val}"
  fi
}

emit APPLE_CERTIFICATE "${APPLE_CERTIFICATE:-}"
emit APPLE_CERTIFICATE_PASSWORD "${APPLE_CERTIFICATE_PASSWORD:-}"
emit APPLE_SIGNING_IDENTITY "${APPLE_SIGNING_IDENTITY:-}"

if [ "${SIGNING_ONLY:-false}" != "true" ]; then
  emit APPLE_ID "${APPLE_ID:-}"
  emit APPLE_PASSWORD "${APPLE_PASSWORD:-}"
  emit APPLE_TEAM_ID "${APPLE_TEAM_ID:-}"
fi
