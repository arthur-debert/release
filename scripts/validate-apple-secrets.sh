#!/usr/bin/env bash
# Validate that required Apple signing and notarization secrets are
# present before a Tauri release begins. Runs in the preflight job
# so failures surface in seconds, not after a 10-minute build.
#
# Env vars (set by the workflow from secret-presence checks):
#   BUILD_MAC             "true" | "false"
#   NOTARIZE              "true" | "false"
#   APPLE_CERT_PRESENT    "yes" | "no"
#   APPLE_ID_PRESENT      "yes" | "no"  (signing identity)
#   NOTARY_ID_PRESENT     "yes" | "no"  (Apple ID for notarization)
#   NOTARY_PASS_PRESENT   "yes" | "no"
#   NOTARY_TEAM_PRESENT   "yes" | "no"
#
# APPLE_CERTIFICATE_PASSWORD is intentionally NOT required —
# passwordless .p12s are valid per PKCS12 spec.

set -euo pipefail

fail=0

: "${APPLE_CERT_PRESENT:=no}"
: "${APPLE_ID_PRESENT:=no}"
: "${NOTARY_ID_PRESENT:=no}"
: "${NOTARY_PASS_PRESENT:=no}"
: "${NOTARY_TEAM_PRESENT:=no}"

if [ "${BUILD_MAC:-false}" = "true" ]; then
  for v in APPLE_CERT_PRESENT APPLE_ID_PRESENT; do
    if [ "${!v}" = "no" ]; then
      case "${v}" in
        APPLE_CERT_PRESENT) name="APPLE_CERTIFICATE" ;;
        APPLE_ID_PRESENT)   name="APPLE_SIGNING_IDENTITY" ;;
      esac
      echo "::error::build-mac=true but ${name} secret is missing"
      fail=1
    fi
  done
fi

if [ "${BUILD_MAC:-false}" = "true" ] && [ "${NOTARIZE:-false}" = "true" ]; then
  for v in NOTARY_ID_PRESENT NOTARY_PASS_PRESENT NOTARY_TEAM_PRESENT; do
    if [ "${!v}" = "no" ]; then
      case "${v}" in
        NOTARY_ID_PRESENT)   name="APPLE_ID" ;;
        NOTARY_PASS_PRESENT) name="APPLE_PASSWORD" ;;
        NOTARY_TEAM_PRESENT) name="APPLE_TEAM_ID" ;;
      esac
      echo "::error::notarize=true but ${name} secret is missing"
      fail=1
    fi
  done
fi

exit "${fail}"
