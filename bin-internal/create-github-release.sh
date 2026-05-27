#!/usr/bin/env bash
# Create or update a GitHub release with optional asset upload.
#
# Env vars:
#   TAG            e.g. "v1.2.3" (required)
#   PRERELEASE     "true" | "false" (default: "false")
#   NOTES_FILE     path to release notes file (default: "release-notes.md")
#   ASSETS_DIR     directory containing release assets (optional)
#   GH_TOKEN       already in env from the workflow

set -euo pipefail

: "${TAG:?TAG is required}"
NOTES_FILE="${NOTES_FILE:-release-notes.md}"

prerelease_flag=""
if [ "${PRERELEASE:-false}" = "true" ]; then
  prerelease_flag="--prerelease"
fi

if [ -n "${ASSETS_DIR:-}" ]; then
  ls -la "${ASSETS_DIR}/" || true
fi

if gh release view "${TAG}" >/dev/null 2>&1; then
  echo "Release ${TAG} exists — updating notes and re-uploading assets"
  gh release edit "${TAG}" --notes-file "${NOTES_FILE}"
  if [ -n "${ASSETS_DIR:-}" ] && [ -d "${ASSETS_DIR}" ]; then
    gh release upload "${TAG}" "${ASSETS_DIR}"/* --clobber
  fi
else
  if [ -n "${ASSETS_DIR:-}" ] && [ -d "${ASSETS_DIR}" ]; then
    gh release create "${TAG}" \
      ${prerelease_flag} \
      --title "${TAG}" \
      --notes-file "${NOTES_FILE}" \
      "${ASSETS_DIR}"/*
  else
    gh release create "${TAG}" \
      ${prerelease_flag} \
      --title "${TAG}" \
      --notes-file "${NOTES_FILE}"
  fi
fi
