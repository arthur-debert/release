#!/usr/bin/env bash
# Fail fast when a release has nothing to release: no unreleased
# changelog content. This is the cheap preflight guard — pure bash,
# no checkout of the consumer toolchain, no release_core install — so
# an empty changelog fails in ~5s instead of after prepare's deps +
# gate (the prepare job re-validates via check-changelog-fragments.sh,
# but only after a full checkout + python + tauri-linux-deps setup).
#
# "Unreleased content" spans both supported changelog shapes:
#   - fragment-dir model (#201): one or more CHANGELOG/unreleased-*.md
#     fragments beside the changelog file.
#   - literal Keep-a-Changelog: a `## [Unreleased]` section in the
#     changelog file with at least one non-whitespace line before the
#     next `## ` heading.
# Either one counts; only the absence of BOTH is a failure.
#
# Env vars:
#   CHANGELOG   path to CHANGELOG.md (required)
#
# Exit codes:
#   0   unreleased content present (fragments or a non-empty section)
#   1   nothing to release (with an actionable error message)

set -euo pipefail

: "${CHANGELOG:?CHANGELOG path is required}"

CHANGELOG_DIR="$(dirname "${CHANGELOG}")/CHANGELOG"

# 1. Fragment-dir model: any CHANGELOG/unreleased-*.md fragment is content.
if [ -d "${CHANGELOG_DIR}" ]; then
  shopt -s nullglob
  fragments=("${CHANGELOG_DIR}"/unreleased-*.md)
  if [ "${#fragments[@]}" -gt 0 ]; then
    echo "Found ${#fragments[@]} unreleased fragment(s):"
    printf '  %s\n' "${fragments[@]}"
    exit 0
  fi
fi

# 2. Literal Keep-a-Changelog: a `## [Unreleased]` section with at least
#    one non-whitespace line before the next `## ` heading.
if [ -f "${CHANGELOG}" ] && awk '
    /^## / {
      if (in_unreleased) exit 1   # reached the next section: no content found
      in_unreleased = ($0 ~ /\[?[Uu]nreleased\]?/)
      next
    }
    in_unreleased && /[^[:space:]]/ { found = 1; exit 0 }
    END { exit (found ? 0 : 1) }
  ' "${CHANGELOG}"; then
  echo "Found content under the '## [Unreleased]' section of ${CHANGELOG}."
  exit 0
fi

echo "::error::No unreleased changelog content. Add a CHANGELOG/unreleased-*.md fragment with 'changelog add <slug> <body>' (or fill the '## [Unreleased]' section) before releasing."
exit 1
