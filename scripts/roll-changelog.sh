#!/usr/bin/env bash
# Roll a changelog for a release. Supports two conventions:
#
#  1. **Fragment-directory** (canonical, per docs/proposals/changelog-handling.md
#     and tracker #201). Detected when a `CHANGELOG/` directory exists
#     at the same level as <changelog>. Delegates to `bin/changelog`:
#       - `roll` runs `bin/changelog new-version <version>` (cut + render),
#         then copies the new CHANGELOG/<version>.md body (without the
#         `## <version> - YYYY-MM-DD` header) to <out-notes>.
#       - `extract` concatenates the current CHANGELOG/unreleased-*.md
#         fragments into <out-notes>.
#     Resume case (tag already exists): cut already ran on the tagged
#     commit, so `extract` reads CHANGELOG/<version>.md instead of the
#     unreleased fragments.
#
#  2. **Keep-a-Changelog single-file** (legacy). Used when no
#     `CHANGELOG/` directory is present. Reads/rewrites the
#     `## [Unreleased]` section in <changelog>. The
#     "[Unreleased] must be non-empty" rule forces a brief entry
#     before cutting a patch immediately after a final release.
#
# Usage:
#   roll-changelog.sh extract <changelog> <out-notes> [version]
#       Write release notes to <out-notes>.
#       Fragment-dir: notes come from CHANGELOG/unreleased-*.md, or from
#       CHANGELOG/<version>.md if <version> is given and the cut already
#       happened (resume case).
#       Single-file: notes come from `## [Unreleased]`.
#
#   roll-changelog.sh roll <changelog> <version> <out-notes>
#       Cut the unreleased content into a versioned section, then
#       write the notes for that version to <out-notes>.

set -euo pipefail
# LC_ALL=C ensures the fragment glob below expands in stable byte
# order on both BSD (macOS) and GNU (Linux) regardless of the caller's
# locale — matches the convention in bin/changelog-render.
export LC_ALL=C

mode="${1:?mode required: extract|roll}"

# Detect convention. <changelog> is typically "CHANGELOG.md" at repo root
# and the fragment dir sits beside it as "CHANGELOG/". Take whatever's
# the parent dir of <changelog> plus a "CHANGELOG/" sibling.
detect_dir() {
  local file=$1
  local parent
  # dirname without `--` is POSIX-portable; the `--` form is GNU-only.
  parent=$(dirname "$file")
  echo "$parent/CHANGELOG"
}

# Resolve bin/changelog relative to this script.
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# scripts/roll-changelog.sh → ../templates/commons/bin/changelog (real file).
# When this script is exec'd from a consumer repo via release-sync,
# scripts/ stays under release/ (not synced); the bin/changelog inside
# the consumer is at <consumer>/bin/changelog. Resolve in order:
#   1. <script_dir>/../bin/changelog (when running inside release/ itself)
#   2. <script_dir>/../templates/commons/bin/changelog (same, via the symlink)
#   3. $(pwd)/bin/changelog (consumer running its own bin)
locate_changelog_cli() {
  for candidate in \
      "$script_dir/../bin/changelog" \
      "$script_dir/../templates/commons/bin/changelog" \
      "$(pwd)/bin/changelog"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# Strip the leading "## <version> - <date>" header + blank line from a
# CHANGELOG/<v>.md file, leaving just the body (bullets etc.) suitable
# for tag/release-notes consumption.
fragment_body() {
  local file=$1
  awk 'NR==1 && /^## / { next } NR==2 && /^$/ { next } { print }' "$file"
}

extract_unreleased() {
  local file=$1 out=$2
  awk '
    /^## \[Unreleased\]/ { in_unreleased=1; next }
    in_unreleased && /^## \[/ { exit }
    in_unreleased { print }
  ' "$file" | sed -E '/^[[:space:]]*$/{ N; /^\n[[:space:]]*$/D; }' > "$out"

  if ! grep -q '[^[:space:]]' "$out"; then
    echo "::error::CHANGELOG section [Unreleased] is missing or empty in $file" >&2
    echo "::error::Add a brief entry under ## [Unreleased] describing this release before re-running." >&2
    exit 1
  fi
}

case "$mode" in
  extract)
    file="${2:?changelog path required}"
    out="${3:?out-notes path required}"
    version="${4:-}"
    dir=$(detect_dir "$file")

    if [ -d "$dir" ]; then
      # Fragment-dir mode.
      if [ -n "$version" ] && [ -f "$dir/$version.md" ]; then
        # Resume case: cut already happened; read the versioned file.
        fragment_body "$dir/$version.md" > "$out"
      else
        # Pre-cut: concat the unreleased-*.md fragments.
        shopt -s nullglob
        fragments=("$dir"/unreleased-*.md)
        if [ "${#fragments[@]}" -eq 0 ]; then
          echo "::error::No CHANGELOG/unreleased-*.md fragments to extract in $dir" >&2
          echo "::error::Add an unreleased fragment via 'bin/changelog add <slug> <body>' before re-running." >&2
          exit 1
        fi
        cat "${fragments[@]}" > "$out"
      fi
      if ! grep -q '[^[:space:]]' "$out"; then
        echo "::error::Extracted changelog body is empty (source: $dir)" >&2
        exit 1
      fi
    else
      extract_unreleased "$file" "$out"
    fi
    ;;
  roll)
    file="${2:?changelog path required}"
    version="${3:?version required}"
    out="${4:?out-notes path required}"
    dir=$(detect_dir "$file")

    if [ -d "$dir" ]; then
      # Fragment-dir mode: hand off to bin/changelog and post-process.
      cli=$(locate_changelog_cli) || {
        echo "::error::CHANGELOG/ directory present but bin/changelog not found." >&2
        echo "::error::Run 'release-sync' to pull bin/changelog* and vendor/semver-tool/ into this repo." >&2
        exit 1
      }
      # `new-version` = cut + render. Cut requires unreleased fragments
      # to exist, so this enforces the same "Unreleased must be non-empty"
      # rule as the single-file path.
      #
      # bin/changelog* resolve their root by walking up cwd for
      # CHANGELOG/, so cd next to the user-specified changelog file
      # first — otherwise on monorepos with multiple changelogs
      # (root + per-package) the wrong one gets cut.
      (
        cd "$(dirname "$file")"
        "$cli" new-version "$version"
      )
      # `cli` ran in a subshell, but it wrote files at the path we
      # computed in $dir. Read the freshly-cut version file from there.
      fragment_body "$dir/$version.md" > "$out"
    else
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
    fi
    ;;
  *)
    echo "unknown mode: $mode (expected: extract|roll)" >&2
    exit 64
    ;;
esac
