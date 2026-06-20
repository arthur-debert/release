#!/usr/bin/env bash
# Fail fast when a release has nothing to release: no unreleased
# changelog content. This is the cheap preflight guard — pure bash,
# no checkout of the consumer toolchain, no release_core install — so
# an empty changelog fails in ~5s instead of after prepare's deps +
# gate (the prepare job re-validates via check-changelog-fragments.sh,
# but only after a full checkout + python + tauri-linux-deps setup).
#
# "Unreleased content" spans both supported changelog shapes, and in
# each case "content" means at least one non-whitespace character — a
# present-but-empty fragment is NOT content. This matches what prepare
# enforces downstream (bin-internal/roll-changelog.sh extracts the
# notes and fails on `grep -q '[^[:space:]]'`), so an empty fragment
# fails here in ~5s instead of after the build.
#   - fragment-dir model (#201): one or more CHANGELOG/unreleased-*.md
#     fragments beside the changelog file with non-whitespace content.
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

# 1. Fragment-dir model: a CHANGELOG/unreleased-*.md fragment with
#    non-whitespace content. An empty fragment doesn't count (prepare
#    would extract empty notes and fail).
if [ -d "${CHANGELOG_DIR}" ]; then
  shopt -s nullglob
  with_content=()
  for frag in "${CHANGELOG_DIR}"/unreleased-*.md; do
    grep -q '[^[:space:]]' "${frag}" && with_content+=("${frag}")
  done
  if [ "${#with_content[@]}" -gt 0 ]; then
    echo "Found ${#with_content[@]} non-empty unreleased fragment(s):"
    printf '  %s\n' "${with_content[@]}"
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
