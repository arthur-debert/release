#!/usr/bin/env bash
# Roll a Keep-a-Changelog `## [Unreleased]` section into a versioned section.
#
# Usage:
#   roll-changelog.sh extract <changelog> <out-notes>
#       Read content under `## [Unreleased]` and write to <out-notes>.
#       Errors out if the section is missing or empty.
#
#   roll-changelog.sh roll <changelog> <version> <out-notes>
#       Same as `extract`, then rewrite <changelog> to insert
#       `## [<version>] - <today>` below the (now empty) `## [Unreleased]`.
#
# CHANGELOG.md is expected to follow Keep-a-Changelog with an `## [Unreleased]`
# section near the top. The "[Unreleased] must be non-empty" rule is a
# feature: cutting a patch immediately after a final release fails clearly,
# forcing a brief Unreleased entry that documents the fix.

set -euo pipefail

mode="${1:?mode required: extract|roll}"

extract_unreleased() {
  local file=$1 out=$2
  awk '
    /^## \[Unreleased\]/ { in_unreleased=1; next }
    in_unreleased && /^## \[/ { exit }
    in_unreleased { print }
  ' "$file" | sed -E '/^[[:space:]]*$/{ N; /^\n[[:space:]]*$/D; }' > "$out"

  if ! grep -qE '\S' "$out"; then
    echo "::error::CHANGELOG section [Unreleased] is missing or empty in $file" >&2
    echo "::error::Add a brief entry under ## [Unreleased] describing this release before re-running." >&2
    exit 1
  fi
}

case "$mode" in
  extract)
    file="${2:?changelog path required}"
    out="${3:?out-notes path required}"
    extract_unreleased "$file" "$out"
    ;;
  roll)
    file="${2:?changelog path required}"
    version="${3:?version required}"
    out="${4:?out-notes path required}"
    extract_unreleased "$file" "$out"

    today=$(date -u +%Y-%m-%d)
    body=$(cat "$out")
    section=$(mktemp)
    {
      printf '## [Unreleased]\n\n'
      printf '## [%s] - %s\n\n' "$version" "$today"
      printf '%s\n' "$body"
    } > "$section"

    awk -v section_file="$section" '
      !inserted && /^## \[Unreleased\]/ {
          while ((getline line < section_file) > 0) print line
          close(section_file)
          inserted=1
          # Skip past the original `## [Unreleased]` block — the new
          # section already includes its own [Unreleased] header followed
          # by the versioned section.
          skip=1
          next
      }
      skip && /^## \[/ { skip=0 }
      skip { next }
      { print }
    ' "$file" > "$file.tmp"
    mv "$file.tmp" "$file"
    rm -f "$section"
    ;;
  *)
    echo "unknown mode: $mode (expected: extract|roll)" >&2
    exit 64
    ;;
esac
