#!/usr/bin/env bash
# Prepare a Tauri release: validate semver, assert the base sha is CI-green
# (WS2 #811 — don't re-gate, the work PR already gated this code), detect resume
# case, run optional prep-script, bump the three version files (package.json,
# Cargo.toml, tauri.conf.json), roll changelog, commit + tag + push.
#
# Env vars:
#   NEW_VERSION   semver version to release (required)
#   TAURI_DIR     path to Tauri project root (default: ".")
#   CHANGELOG     path to CHANGELOG.md (empty to skip changelog)
#   PREP_SCRIPT   optional consumer prep-script path
#   GITHUB_OUTPUT for CI output; falls back to stdout

set -euo pipefail

: "${NEW_VERSION:?NEW_VERSION is required}"
TAURI_DIR="${TAURI_DIR:-.}"
CHANGELOG="${CHANGELOG:-}"
PREP_SCRIPT="${PREP_SCRIPT:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# semver is the release_core console-script; the prepare-release-tauri
# composite action runs install-release-core-pkg first to put it on PATH.
SEMVER="semver"
if ! command -v "$SEMVER" >/dev/null 2>&1; then
  echo "::error::the 'semver' console-script is not on PATH." >&2
  echo "::error::The prepare-release-tauri composite action installs release_core (install-release-core-pkg) before this runs." >&2
  exit 1
fi

emit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  else
    echo "$1"
  fi
}

# ── Semver validation ──────────────────────────────────────────────
if [[ "${NEW_VERSION}" =~ ^[vV] ]] \
    || [[ "$("$SEMVER" validate "${NEW_VERSION}")" != "valid" ]] \
    || [[ -n "$("$SEMVER" get build "${NEW_VERSION}")" ]]; then
  echo "::error::version must be MAJOR.MINOR.PATCH[-PRERELEASE] (got: ${NEW_VERSION})"
  exit 1
fi

IS_PRERELEASE=false
if [[ -n "$("$SEMVER" get prerel "${NEW_VERSION}")" ]]; then
  IS_PRERELEASE=true
fi

# Reserved verification suffix (release#663): a `-release-rc` prerelease is a
# throwaway live-fire cut. It builds + tags + publishes a GitHub pre-release
# like any RC, but the bump commit is NOT pushed to the branch (the tag alone
# carries it), so the consumer's version line / main history stay clean. The
# live-fire harness deletes the tag + GH release on teardown. Normal RCs
# (`-rc.1`) keep real-RC behavior.
IS_VERIFY=false
case "${NEW_VERSION}" in
  *-release-rc|*-release-rc.*) IS_VERIFY=true ;;
esac

# ── Early changelog validation ────────────────────────────────────
if [ -n "${CHANGELOG}" ]; then
  CHANGELOG="${CHANGELOG}" bash "${script_dir}/check-changelog-fragments.sh"
fi

# ── Sanity-check version files ────────────────────────────────────
pkg="${TAURI_DIR}/package.json"
cargo="${TAURI_DIR}/src-tauri/Cargo.toml"
tauri_conf="${TAURI_DIR}/src-tauri/tauri.conf.json"
for f in "${pkg}" "${cargo}" "${tauri_conf}"; do
  if [ ! -f "${f}" ]; then
    echo "::error::expected file not found: ${f}"
    exit 1
  fi
done

TAG="v${NEW_VERSION}"
git fetch --tags origin >/dev/null 2>&1 || true

# ── Assert the release base is CI-green ───────────────────────────
# WS2 (#811): don't re-gate — the work PR that landed this code already ran the
# full gate (lint/compile/tests) on exactly that sha. Assert the RELEASE BASE
# (the pre-bump code being released) is CI-green, in seconds, instead of
# recompiling src-tauri + re-linting here.
#
# The base differs by case:
#   - fresh run: HEAD is the checked-out main tip, before the bump → base = HEAD.
#   - resume case (the tag already exists from a Layer 0 / cascade primitive that
#     bumped + tagged locally): the tag IS the bump commit, which was never CI'd;
#     its PARENT (${TAG}^) is the pre-bump base that CI gated → base = ${TAG}^.
# Skipped only for verification rcs (a `-release-rc` live-fire cut runs off an
# arbitrary working sha that may have no CI round of its own).
assert_base_green() {
  local base_sha="$1"
  if [ "${IS_VERIFY}" = "true" ]; then
    echo "verification rc (${TAG}): skipping base-sha CI-green assertion (release#663)."
    return 0
  fi
  BASE_SHA="${base_sha}" bash "${script_dir}/assert-base-sha-green.sh"
}

# ── Resume case ───────────────────────────────────────────────────
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  TAG_SHA=$(git rev-list -n 1 "${TAG}")
  TAG_VERSION="$(git show "${TAG}:${pkg}" 2>/dev/null | jq -r '.version // empty')"
  if [ "${TAG_VERSION}" = "${NEW_VERSION}" ]; then
    echo "tag ${TAG} already exists at ${TAG_SHA} (${pkg} matches) — resuming"
    # The pre-bump base is the tag's parent (the tag commit is the bump itself).
    assert_base_green "$(git rev-parse "${TAG}^")"
    emit "release-sha=${TAG_SHA}"

    if [ -z "${CHANGELOG}" ]; then
      echo "Release ${TAG}" > release-notes.md
    else
      bash "${script_dir}/roll-changelog.sh" \
        extract "${CHANGELOG}" release-notes.md "${NEW_VERSION}"
      [ -s release-notes.md ] || echo "Release ${TAG}" > release-notes.md
    fi

    emit "version=${NEW_VERSION}"
    emit "prerelease=${IS_PRERELEASE}"
    exit 0
  fi
  echo "::error::tag ${TAG} exists but ${pkg} at that commit is '${TAG_VERSION}', not '${NEW_VERSION}'"
  exit 1
fi

# ── Fresh release path ────────────────────────────────────────────
# HEAD is the checked-out main tip, before the bump → it IS the release base.
assert_base_green "$(git rev-parse HEAD)"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Optional consumer prep-script
if [ -n "${PREP_SCRIPT}" ]; then
  if [ ! -f "${PREP_SCRIPT}" ]; then
    echo "::error::prep-script not found: ${PREP_SCRIPT}"
    exit 1
  fi
  echo "→ running consumer prep-script: ${PREP_SCRIPT}"
  bash "${PREP_SCRIPT}"
  if ! git diff --quiet; then
    echo "::error::prep-script mutated files but didn't \`git add\` them. Stage the modifications in the script (use \`git add\` after each mutation), or \`git stash\` them if intentionally out-of-release-scope."
    git diff --stat
    exit 1
  fi
fi

# ── Bump package.json ─────────────────────────────────────────────
# Format-preserving: changes ONLY the version string value.
# Why not jq: jq reformats the entire file (indent, inline arrays),
# breaking consumer prettier/eslint gates.
bump_json_version() {
  local file="$1"
  local new_v="$2"
  awk -v v="${new_v}" '
    !bumped && /^[[:space:]]+"version"[[:space:]]*:[[:space:]]*"/ {
      match($0, /"version"[[:space:]]*:[[:space:]]*"/)
      head_len = RSTART + RLENGTH - 1
      rest = substr($0, head_len + 1)
      closing = index(rest, "\"")
      if (closing > 0) {
        $0 = substr($0, 1, head_len) v substr($0, head_len + closing)
        bumped = 1
      }
    }
    { print }
    END {
      if (!bumped) {
        print "::error::no top-level \"version\" key found in " FILENAME > "/dev/stderr"
        exit 1
      }
    }
  ' "$file" > "${file}.tmp"
  mv "${file}.tmp" "$file"
}

bump_json_version "${pkg}" "${NEW_VERSION}"

# ── Bump src-tauri/Cargo.toml ─────────────────────────────────────
# Scoped to [package] table only (Cargo files have version = under
# [dependencies] too).
awk -v v="${NEW_VERSION}" '
  BEGIN { q = "[\042\047]" }
  {
    sub(/\r$/, "")
    if ($0 ~ /^[[:space:]]*\[/) {
      h = $0
      sub(/#.*/, "", h)
      gsub(/[[:space:]]/, "", h)
      in_pkg = (h == "[package]")
      print
      next
    }
    if (in_pkg && !bumped && $0 ~ ("^[[:space:]]*version[[:space:]]*=[[:space:]]*" q)) {
      leading = $0
      sub(/[^[:space:]].*/, "", leading)
      trailing = ""
      if (match($0, /[[:space:]]+#.*$/)) {
        trailing = substr($0, RSTART, RLENGTH)
      }
      print leading "version = \"" v "\"" trailing
      bumped = 1
      next
    }
    print
  }
  END {
    if (!bumped) {
      print "::error::no [package].version in Cargo.toml" > "/dev/stderr"
      exit 1
    }
  }
' "${cargo}" > "${cargo}.tmp"
mv "${cargo}.tmp" "${cargo}"

# ── Bump src-tauri/tauri.conf.json (when it carries .version) ─────
if [ "$(jq -r '.version // empty' "${tauri_conf}")" != "" ]; then
  bump_json_version "${tauri_conf}" "${NEW_VERSION}"
fi

# ── Roll changelog ────────────────────────────────────────────────
if [ -z "${CHANGELOG}" ]; then
  echo "Release ${TAG}" > release-notes.md
elif [ "${IS_PRERELEASE}" = "true" ]; then
  bash "${script_dir}/roll-changelog.sh" \
    extract "${CHANGELOG}" release-notes.md
else
  bash "${script_dir}/roll-changelog.sh" \
    roll "${CHANGELOG}" "${NEW_VERSION}" release-notes.md
fi
[ -s release-notes.md ] || echo "Release ${TAG}" > release-notes.md

# ── Stage version files + lockfiles ───────────────────────────────
git add "${pkg}" "${cargo}" "${tauri_conf}"
for lock in pnpm-lock.yaml package-lock.json yarn.lock "${TAURI_DIR}/src-tauri/Cargo.lock"; do
  if [ -f "${lock}" ] && ! git diff --quiet -- "${lock}"; then
    git add -- "${lock}"
  fi
done

# ── Stage changelog artifacts ─────────────────────────────────────
# The non-prerelease `roll` above rewrites CHANGELOG.md AND deletes the
# CHANGELOG/unreleased-*.md fragments; stage both so the release commit/tag
# carries them, matching all five sibling prepare flows (e.g.
# prepare-release-npm). The prerelease path uses `extract` (read-only on
# CHANGELOG.md) — nothing to stage there. Guarded on a configured + present
# CHANGELOG. `git add "${CHANGELOG_DIR}/"` stages new fragment-routed files and
# the fragment deletions alike.
if [ "${IS_PRERELEASE}" != "true" ] && [ -n "${CHANGELOG}" ] && [ -f "${CHANGELOG}" ]; then
  git add "${CHANGELOG}"
  CHANGELOG_DIR="$(dirname "${CHANGELOG}")/CHANGELOG"
  if [ -d "${CHANGELOG_DIR}" ]; then
    git add "${CHANGELOG_DIR}/"
  fi
fi

version_files_changed=false
for vf in "${pkg}" "${cargo}" "${tauri_conf}"; do
  if ! git diff --cached --quiet -- "${vf}" 2>/dev/null; then
    version_files_changed=true
    break
  fi
done
if [ "${version_files_changed}" = "false" ]; then
  echo "::error::no version-file changes after bump — was the version already at ${NEW_VERSION}?"
  exit 1
fi

# ── Commit + tag + push ──────────────────────────────────────────
# No pre-commit gate here: WS2 (#811) asserts the base sha is CI-green above
# instead of re-running the gate. The bump commit only rewrites version strings
# (no code), and a malformed bump still fails fast: each bump_json_version /
# Cargo awk hard-errors when its version key is missing, and the
# version_files_changed guard above refuses a no-op bump.
git commit -m "chore: Release ${TAG}"
if [ -s release-notes.md ]; then
  git tag -a "${TAG}" -F release-notes.md
else
  git tag -a "${TAG}" -m "Release ${TAG}"
fi
# Verification rc (release#663): tag-only — the tag carries the bump commit to
# the remote, but the branch ref is NOT advanced, so the consumer's main stays
# clean. Downstream jobs check out release-sha, reachable via the pushed tag.
if [ "${IS_VERIFY}" = "true" ]; then
  echo "verification rc (${TAG}): tag-only — not advancing the branch (release#663)."
else
  git push origin HEAD
fi
git push origin "${TAG}"

emit "version=${NEW_VERSION}"
emit "prerelease=${IS_PRERELEASE}"
emit "release-sha=$(git rev-parse HEAD)"
