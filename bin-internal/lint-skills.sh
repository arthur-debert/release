#!/usr/bin/env bash
# Lint self-authored skill docs; skip vendored ones via .upstream markers.
#
# Skill docs are excluded from the fleet-wide markdownlint gate (see
# templates/commons/.markdownlintignore) because vendored / third-party
# skills aren't ours to clean. But OUR OWN skills should still be held
# to standard. This script splits the two by provenance:
#
#   - A skill directory (one containing SKILL.md) that has an `.upstream`
#     marker is VENDORED — skipped. The marker records its source repo +
#     pinned commit (see the skill backfill in sync.py / release#321).
#   - A skill directory with NO `.upstream` is SELF-AUTHORED — linted
#     with markdownlint, using the repo's .markdownlint.json.
#
# This is a repo-internal dev gate (release/'s own skills/ tree), invoked
# by this repo's lefthook pre-commit AND by CI (.github/workflows/ci.yml).
# It is NOT synced to consumers.
#
# Usage:
#   bin-internal/lint-skills.sh [skills-root]   # default root: skills/
#
# Exit codes:
#   0  — all self-authored skills pass (or none found)
#   1  — a self-authored skill has markdownlint errors
#   2  — markdownlint not installed

set -euo pipefail

root=${1:-skills}

if ! command -v markdownlint >/dev/null 2>&1; then
  echo "lint-skills: markdownlint not installed" >&2
  echo "  install: npm i -g markdownlint-cli   (or: brew install markdownlint-cli)" >&2
  exit 2
fi

if [ ! -d "$root" ]; then
  echo "lint-skills: no '$root' directory — nothing to lint."
  exit 0
fi

# Collect markdown files from every self-authored skill dir (a dir that
# contains SKILL.md and has no .upstream provenance marker).
declare -a targets=()
declare -a skipped=()
while IFS= read -r skillmd; do
  # Parameter expansion instead of $(dirname ...) — no subshell per
  # iteration. find always yields a path with a slash, but guard the
  # no-slash case for dirname-equivalent semantics.
  dir="${skillmd%/*}"
  [ "$dir" = "$skillmd" ] && dir="."
  if [ -e "$dir/.upstream" ]; then
    skipped+=("$dir")
    continue
  fi
  while IFS= read -r md; do
    targets+=("$md")
  done < <(find "$dir" -name '*.md')
done < <(find "$root" -name SKILL.md | sort)

if [ "${#skipped[@]}" -gt 0 ]; then
  echo "lint-skills: skipping ${#skipped[@]} vendored skill(s) (have .upstream):"
  printf '  - %s\n' "${skipped[@]}"
fi

if [ "${#targets[@]}" -eq 0 ]; then
  echo "lint-skills: no self-authored skill markdown to lint."
  exit 0
fi

echo "lint-skills: linting ${#targets[@]} markdown file(s) across self-authored skills."
markdownlint "${targets[@]}"
echo "lint-skills: OK."
