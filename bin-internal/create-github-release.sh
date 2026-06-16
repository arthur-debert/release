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

# Re-assert the prerelease flag on BOTH the create and the edit path. gh
# defaults a created release to isPrerelease=false, and `gh release edit`
# leaves prerelease UNCHANGED unless --prerelease is passed — so on the
# resume/exists path (a re-run, or a release pre-created without the flag) the
# prerelease status would otherwise stay false even for a `-rc`/`-release-rc`
# version. Pass the explicit boolean form (--prerelease=true|false) so the
# release's prerelease status always matches the version's actual SemVer
# pre-release suffix, idempotently (release#726).
prerelease_bool=false
if [ "${PRERELEASE:-false}" = "true" ]; then
  prerelease_bool=true
fi

if [ -n "${ASSETS_DIR:-}" ]; then
  ls -la "${ASSETS_DIR}/" || true
fi

if gh release view "${TAG}" >/dev/null 2>&1; then
  echo "Release ${TAG} exists — updating notes and re-uploading assets"
  gh release edit "${TAG}" --notes-file "${NOTES_FILE}" --prerelease="${prerelease_bool}"
  if [ -n "${ASSETS_DIR:-}" ] && [ -d "${ASSETS_DIR}" ]; then
    gh release upload "${TAG}" "${ASSETS_DIR}"/* --clobber
  fi
else
  if [ -n "${ASSETS_DIR:-}" ] && [ -d "${ASSETS_DIR}" ]; then
    gh release create "${TAG}" \
      --prerelease="${prerelease_bool}" \
      --title "${TAG}" \
      --notes-file "${NOTES_FILE}" \
      "${ASSETS_DIR}"/*
  else
    gh release create "${TAG}" \
      --prerelease="${prerelease_bool}" \
      --title "${TAG}" \
      --notes-file "${NOTES_FILE}"
  fi
fi
