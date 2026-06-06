#!/usr/bin/env bash
# Roll a changelog for a release using the fragment-directory model
# (per tracker #201).
#
# Requires a `CHANGELOG/` directory at the same level as <changelog>.
# Delegates to the `changelog` console-script (from the pip-installed
# release_core package — the prepare-release composite action runs
# install-release-core-pkg first so it is on PATH):
#   - `roll` runs `changelog new-version <version>` (cut + render),
#     then copies the new CHANGELOG/<version>.md body (without the
#     `## <version> - YYYY-MM-DD` header) to <out-notes>.
#   - `extract` concatenates the current CHANGELOG/unreleased-*.md
#     fragments into <out-notes>.
#   Resume case (tag already exists): cut already ran on the tagged
#   commit, so `extract` reads CHANGELOG/<version>.md instead of the
#   unreleased fragments.
#
# Legacy single-file mode is retired. Invoking this script without
# a CHANGELOG/ directory exits with an error pointing at the
# migration docs.
#
# Usage:
#   roll-changelog.sh extract <changelog> <out-notes> [version]
#       Write release notes to <out-notes>.
#       Notes come from CHANGELOG/unreleased-*.md, or from
#       CHANGELOG/<version>.md if <version> is given and the cut
#       already happened (resume case).
#
#   roll-changelog.sh roll <changelog> <version> <out-notes>
#       Cut the unreleased content into a versioned section, then
#       write the notes for that version to <out-notes>.

set -euo pipefail
# LC_ALL=C ensures the fragment glob below expands in stable byte
# order on both BSD (macOS) and GNU (Linux) regardless of the caller's
# locale — matches the convention in changelog-render.
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

# The `changelog` console-script must be on PATH (the prepare-release
# composite action runs install-release-core-pkg first).

# Strip the leading "## <version> - <date>" header + blank line from a
# CHANGELOG/<v>.md file, leaving just the body (bullets etc.) suitable
# for tag/release-notes consumption.
fragment_body() {
  local file=$1
  awk 'NR==1 && /^## / { next } NR==2 && /^$/ { next } { print }' "$file"
}

legacy_error() {
  echo "::error::Legacy single-file changelog mode has been retired." >&2
  echo "::error::Migration: (1) move existing entries to CHANGELOG/legacy.md," >&2
  echo "::error::(2) run 'release-sync' to set up the CHANGELOG/ structure," >&2
  echo "::error::(3) run 'changelog render' to regenerate CHANGELOG.md." >&2
  echo "::error::See CHANGELOG/README.txt for details." >&2
  exit 1
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
          echo "::error::Add an unreleased fragment via 'changelog add <slug> <body>' before re-running." >&2
          exit 1
        fi
        for frag in "${fragments[@]}"; do
          cat "$frag"
          [[ -s "$frag" && "$(tail -c1 "$frag")" != "" ]] && printf '\n'
        done > "$out"
      fi
      if ! grep -q '[^[:space:]]' "$out"; then
        echo "::error::Extracted changelog body is empty (source: $dir)" >&2
        exit 1
      fi
    else
      legacy_error
    fi
    ;;
  roll)
    file="${2:?changelog path required}"
    version="${3:?version required}"
    out="${4:?out-notes path required}"
    dir=$(detect_dir "$file")

    if [ -d "$dir" ]; then
      # Fragment-dir mode: hand off to the `changelog` console-script.
      if ! command -v changelog >/dev/null 2>&1; then
        echo "::error::CHANGELOG/ directory present but the 'changelog' console-script is not on PATH." >&2
        echo "::error::The prepare-release composite action installs release_core (install-release-core-pkg) before this runs." >&2
        exit 1
      fi
      # changelog resolves its root by walking up cwd for CHANGELOG/,
      # so cd next to the user-specified changelog file first —
      # otherwise on monorepos with multiple changelogs (root +
      # per-package) the wrong one gets cut.
      (
        cd "$(dirname "$file")"
        changelog new-version "$version"
      )
      fragment_body "$dir/$version.md" > "$out"
    else
      legacy_error
    fi
    ;;
  *)
    echo "unknown mode: $mode (expected: extract|roll)" >&2
    exit 64
    ;;
esac
