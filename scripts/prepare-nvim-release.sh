#!/usr/bin/env bash
# Prepare a Neovim plugin release: validate semver, sanity-check
# plugin layout, detect resume, run optional prep-script, bump
# M.version in a Lua file, roll changelog, run pre-commit gate,
# commit + tag + push.
#
# Env vars:
#   NEW_VERSION   semver version to release (required)
#   PLUGIN_ROOT   path to plugin root (default: ".")
#   CHANGELOG     path to CHANGELOG.md (empty to skip)
#   VERSION_FILE  Lua file with M.version to bump (empty to skip)
#   PREP_SCRIPT   optional consumer prep-script path
#   GITHUB_OUTPUT for CI output; falls back to stdout

set -euo pipefail

: "${NEW_VERSION:?NEW_VERSION is required}"
PLUGIN_ROOT="${PLUGIN_ROOT:-.}"
CHANGELOG="${CHANGELOG:-}"
VERSION_FILE="${VERSION_FILE:-}"
PREP_SCRIPT="${PREP_SCRIPT:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

emit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  else
    echo "$1"
  fi
}

# ── Semver validation ──────────────────────────────────────────────
if ! [[ "${NEW_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$ ]]; then
  echo "::error::version must be MAJOR.MINOR.PATCH[-PRERELEASE] (got: ${NEW_VERSION})"
  exit 1
fi

IS_PRERELEASE=false
if [[ "${NEW_VERSION}" == *-* ]]; then
  IS_PRERELEASE=true
fi

# ── Plugin layout sanity check ────────────────────────────────────
if [ ! -d "${PLUGIN_ROOT}/lua" ] && [ ! -d "${PLUGIN_ROOT}/plugin" ]; then
  echo "::error::plugin root ${PLUGIN_ROOT} has no lua/ or plugin/ directory. Is this a Neovim plugin?"
  exit 1
fi

TAG="v${NEW_VERSION}"
git fetch --tags origin >/dev/null 2>&1 || true

# ── Resume case ───────────────────────────────────────────────────
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  TAG_SHA=$(git rev-list -n 1 "${TAG}")
  echo "tag ${TAG} already exists at ${TAG_SHA} — resuming"

  if [ -z "${CHANGELOG}" ]; then
    echo "Release ${TAG}" > release-notes.md
  else
    bash "${script_dir}/roll-changelog.sh" \
      extract "${CHANGELOG}" release-notes.md "${NEW_VERSION}"
    [ -s release-notes.md ] || echo "Release ${TAG}" > release-notes.md
  fi

  emit "version=${NEW_VERSION}"
  emit "prerelease=${IS_PRERELEASE}"
  emit "release-sha=${TAG_SHA}"
  exit 0
fi

# ── Fresh release path ────────────────────────────────────────────
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Optional consumer prep-script
if [ -n "${PREP_SCRIPT}" ]; then
  if [ ! -f "${PREP_SCRIPT}" ]; then
    echo "::error::prep-script not found: ${PREP_SCRIPT}"
    exit 1
  fi
  echo "→ running consumer prep-script: ${PREP_SCRIPT}"
  bash "${PREP_SCRIPT}" "${NEW_VERSION}"
  if ! git diff --quiet; then
    echo "::error::prep-script mutated files but didn't \`git add\` them."
    git diff --stat
    exit 1
  fi
fi

# ── M.version Lua bump ────────────────────────────────────────────
if [ -n "${VERSION_FILE}" ]; then
  if [ ! -f "${VERSION_FILE}" ]; then
    echo "::error::version-file not found: ${VERSION_FILE}"
    exit 1
  fi
  if ! perl -0777 -ne 'exit 0 if /M\.version\s*=\s*[\042\047][^\042\047]+[\042\047]/; exit 1' "${VERSION_FILE}"; then
    echo "::error::version-file ${VERSION_FILE} has no M.version declaration to bump"
    exit 1
  fi
  perl -s -i -pe 's/(M\.version\s*=\s*[\042\047])[^\042\047]+([\042\047])/${1}${v}${2}/' -- -v="${NEW_VERSION}" "${VERSION_FILE}"
  if ! perl -0777 -s -ne 'exit 0 if /M\.version\s*=\s*[\042\047]\Q${v}\E[\042\047]/; exit 1' -- -v="${NEW_VERSION}" "${VERSION_FILE}"; then
    echo "::error::M.version bump in ${VERSION_FILE} did not produce expected value"
    exit 1
  fi
  git add "${VERSION_FILE}"
fi

# ── Roll changelog ────────────────────────────────────────────────
CHANGELOG_DIR="$(dirname "${CHANGELOG:-.}")/CHANGELOG"
if [ -z "${CHANGELOG}" ]; then
  echo "Release ${TAG}" > release-notes.md
elif [ -d "${CHANGELOG_DIR}" ] || [ -f "${CHANGELOG}" ]; then
  if [ "${IS_PRERELEASE}" = "true" ]; then
    bash "${script_dir}/roll-changelog.sh" \
      extract "${CHANGELOG}" release-notes.md
  else
    bash "${script_dir}/roll-changelog.sh" \
      roll "${CHANGELOG}" "${NEW_VERSION}" release-notes.md
    git add "${CHANGELOG}"
    if [ -d "${CHANGELOG_DIR}" ]; then
      git add "${CHANGELOG_DIR}/"
    fi
  fi
else
  echo "Release ${TAG}" > release-notes.md
fi
[ -s release-notes.md ] || echo "Release ${TAG}" > release-notes.md

# ── Pre-commit gate ───────────────────────────────────────────────
bash "${script_dir}/run-precommit-gate.sh"

# ── Commit + tag + push ──────────────────────────────────────────
# Bare-tag support: if nothing is staged (no version-file, no
# changelog, no prep-script mutations), skip the commit and just
# tag HEAD directly.
if ! git diff --cached --quiet; then
  git commit -m "chore: Release ${TAG}"
fi
if [ -s release-notes.md ]; then
  git tag -a "${TAG}" -F release-notes.md
else
  git tag -a "${TAG}" -m "Release ${TAG}"
fi
git push origin HEAD "${TAG}"

emit "version=${NEW_VERSION}"
emit "prerelease=${IS_PRERELEASE}"
emit "release-sha=$(git rev-parse HEAD)"
