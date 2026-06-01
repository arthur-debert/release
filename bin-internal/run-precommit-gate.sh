#!/usr/bin/env bash
# Run the consumer's own pre-commit gate against staged files.
#
# Detects (in order):
#   1. lefthook  (lefthook.yml / lefthook.yaml / .lefthook.yml)
#   2. pre-commit framework  (.pre-commit-config.yaml / .yml)
#   3. husky  (.husky/pre-commit)
#
# No-op when none is configured or nothing is staged.
# Re-stages auto-fixes (prettier --write, eslint --fix, etc.).
#
# Env vars:
#   SKIP_PRECOMMIT_GATE   "true" to bypass

set -euo pipefail

if [ "${SKIP_PRECOMMIT_GATE:-false}" = "true" ]; then
  echo "Pre-commit gate skipped (SKIP_PRECOMMIT_GATE)."
  exit 0
fi

# --diff-filter=d excludes ONLY Deleted (lowercase = exclude that status),
# keeping every path that still exists — including type-changes (T, e.g.
# file↔symlink). The changelog roll deletes unreleased-*.md fragments, and a
# deleted path can't be linted/formatted. Feeding one to lefthook's prettier
# (stage_fixed: true) makes it run `git add --force` on a vanished file →
# exit 128. The deletion is still committed via the staged index regardless
# of being in this list.
mapfile -t staged_arr < <(git diff --cached --name-only --diff-filter=d)
if [ "${#staged_arr[@]}" -eq 0 ]; then
  echo "No staged files — pre-commit gate has nothing to check."
  exit 0
fi
printf 'Staged files (%d):\n' "${#staged_arr[@]}"
printf '  %s\n' "${staged_arr[@]}"

lefthook_file_args=()
for f in "${staged_arr[@]}"; do
  lefthook_file_args+=(--file "$f")
done

run_lefthook() {
  if [ -f pnpm-lock.yaml ]; then
    corepack enable >/dev/null 2>&1 || true
    echo "→ pnpm install (frozen, no scripts) for lefthook deps"
    pnpm install --frozen-lockfile --ignore-scripts
    pnpm exec lefthook run pre-commit "${lefthook_file_args[@]}"
  elif [ -f package-lock.json ]; then
    echo "→ npm ci (no scripts) for lefthook deps"
    npm ci --ignore-scripts
    # --yes (not --no-install): npx prefers a locally-installed lefthook
    # but fetches it non-interactively if the consumer doesn't carry it
    # as an npm dep. --no-install fails in CI with "npx canceled … no YES
    # option" for those consumers (release#318).
    npx --yes lefthook run pre-commit "${lefthook_file_args[@]}"
  elif [ -f yarn.lock ]; then
    corepack enable >/dev/null 2>&1 || true
    echo "→ yarn install (frozen, no scripts) for lefthook deps"
    yarn install --frozen-lockfile --ignore-scripts
    yarn exec lefthook run pre-commit "${lefthook_file_args[@]}"
  elif command -v lefthook >/dev/null 2>&1; then
    lefthook run pre-commit "${lefthook_file_args[@]}"
  else
    npx --yes lefthook run pre-commit "${lefthook_file_args[@]}"
  fi
}

run_precommit_framework() {
  if command -v pipx >/dev/null 2>&1; then
    pipx run pre-commit run --files "${staged_arr[@]}"
  else
    python3 -m pip install --user --quiet pre-commit
    "$(python3 -m site --user-base)/bin/pre-commit" run --files "${staged_arr[@]}"
  fi
}

run_husky() {
  bash .husky/pre-commit
}

gate_ran=0
if [ -f lefthook.yml ] || [ -f .lefthook.yml ] || [ -f lefthook.yaml ]; then
  echo "Detected lefthook config — running gate."
  run_lefthook
  gate_ran=1
elif [ -f .pre-commit-config.yaml ] || [ -f .pre-commit-config.yml ]; then
  echo "Detected pre-commit framework — running gate."
  run_precommit_framework
  gate_ran=1
elif [ -d .husky ] && [ -f .husky/pre-commit ]; then
  echo "Detected husky pre-commit — running gate."
  run_husky
  gate_ran=1
else
  echo "No pre-commit gate detected (no lefthook / pre-commit / husky config) — skipping."
fi

if [ "${gate_ran}" = "1" ] && ! git diff --quiet; then
  echo "Gate produced auto-fixes — re-staging modified files."
  git diff --name-only
  git add -u
fi
