#!/usr/bin/env bash
# Build a JSON matrix of {os, platform, bundles} entries from
# the build-{mac,linux,windows} toggle inputs.
#
# Env vars:
#   BUILD_MAC, BUILD_LINUX, BUILD_WINDOWS   "true" | "false"
#   BUNDLE_MAC, BUNDLE_LINUX, BUNDLE_WINDOWS  bundle target strings (can be empty)
#
# Writes targets=<json> to $GITHUB_OUTPUT (or stdout when unset).

set -euo pipefail

json='[]'

if [ "${BUILD_MAC:-false}" = "true" ]; then
  json="$(jq -c \
    --arg b "${BUNDLE_MAC:-}" \
    '. + [{os:"macos-latest", platform:"mac", bundles:$b}]' \
    <<<"${json}")"
fi

if [ "${BUILD_LINUX:-false}" = "true" ]; then
  json="$(jq -c \
    --arg b "${BUNDLE_LINUX:-}" \
    '. + [{os:"ubuntu-latest", platform:"linux", bundles:$b}]' \
    <<<"${json}")"
fi

if [ "${BUILD_WINDOWS:-false}" = "true" ]; then
  json="$(jq -c \
    --arg b "${BUNDLE_WINDOWS:-}" \
    '. + [{os:"windows-latest", platform:"windows", bundles:$b}]' \
    <<<"${json}")"
fi

if [ "$(jq 'length' <<<"${json}")" -eq 0 ]; then
  echo "::error::all build-{mac,linux,windows} inputs are false — nothing to build"
  exit 1
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "targets=${json}" >> "${GITHUB_OUTPUT}"
else
  echo "targets=${json}"
fi
