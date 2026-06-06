#!/usr/bin/env bash
# Verify that CHANGELOG/unreleased-*.md fragments exist before a
# release begins. Catches the common case where an author forgets
# `changelog add` — surfaces the error in seconds (preflight)
# instead of after a 10-minute build.
#
# Env vars:
#   CHANGELOG   path to CHANGELOG.md (required)
#
# Exit codes:
#   0   fragments exist
#   1   no fragments found (with actionable error message)

set -euo pipefail

: "${CHANGELOG:?CHANGELOG path is required}"

CHANGELOG_DIR="$(dirname "${CHANGELOG}")/CHANGELOG"

if [ ! -d "${CHANGELOG_DIR}" ]; then
  echo "::error::Fragment directory ${CHANGELOG_DIR} not found. Run 'release-sync' to set up the changelog structure."
  exit 1
fi

shopt -s nullglob
fragments=("${CHANGELOG_DIR}"/unreleased-*.md)

if [ "${#fragments[@]}" -eq 0 ]; then
  echo "::error::No CHANGELOG/unreleased-*.md fragments found. Add one with 'changelog add <slug> <body>' before releasing."
  exit 1
fi

echo "Found ${#fragments[@]} unreleased fragment(s):"
printf '  %s\n' "${fragments[@]}"
