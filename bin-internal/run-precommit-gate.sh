#!/usr/bin/env bash
# Run the consumer's own pre-commit gate against staged files.
#
# Detects (in order):
#   1. lefthook  (LEFTHOOK_CONFIG / managed .release/lefthook.yml)
#   2. husky  (.husky/pre-commit)
#   3. release-core managed-gate fallback — when no gate config is
#      present but release-core is on PATH, install the managed
#      files from the wheel bundle and run the gate through the binary
#      (`release-core gate --hook`). This is the post-WS3 path: the
#      gate config lives only in the ephemeral .release/, so there is
#      no root file to detect.
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

run_husky() {
  bash .husky/pre-commit
}

# WS3 (release#524): the gate config lives in the ephemeral .release/ temp dir;
# there is no tracked root lefthook.yml. Point lefthook at the managed config via
# LEFTHOOK_CONFIG. .release/lefthook.yml exists only when the caller wrote
# it (e.g. rust-cli's arm-gate before prepare-release); when it is absent this is a
# no-op and detection falls through to the release-core branch, which installs
# the managed files from the wheel bundle. The pre-WS3 root-config fallback
# (lefthook.yml / .lefthook.yml / lefthook.yaml) was removed in #569 B4 — 0/19
# consumers track a root config, this script never runs against release's own repo
# (it self-releases via gh-action.yml), and an unbuilt managed consumer is
# handled by the release-core branch below.
#
# An explicit caller-provided LEFTHOOK_CONFIG is authoritative (matching the
# release-core gate verb); an empty/whitespace value counts as unset. Only when no
# real config is provided do we point lefthook at the managed .release/ copy.
if [ -n "${LEFTHOOK_CONFIG:-}" ] && [ -z "$(printf '%s' "${LEFTHOOK_CONFIG}" | tr -d '[:space:]')" ]; then
  unset LEFTHOOK_CONFIG
fi
if [ -z "${LEFTHOOK_CONFIG:-}" ] && [ -f .release/lefthook.yml ]; then
  export LEFTHOOK_CONFIG=.release/lefthook.yml
fi

gate_ran=0
if [ -n "${LEFTHOOK_CONFIG:-}" ]; then
  echo "Detected lefthook config — running gate."
  run_lefthook
  gate_ran=1
elif [ -d .husky ] && [ -f .husky/pre-commit ]; then
  echo "Detected husky pre-commit — running gate."
  run_husky
  gate_ran=1
elif command -v release-core >/dev/null 2>&1; then
  # The hollow-green spot (release#531 F1): a post-WS3 managed consumer has NO
  # root gate config (the gate lives in the ephemeral .release/, WS4), and
  # outside rust-cli's arm-gate nothing wrote .release/ — so this branch
  # used to fall through to "skipping" and the bot commit went UNGATED. The
  # release flows install release-core earlier (install-release-core-pkg.sh /
  # install-release-core), so: install the managed files from the wheel
  # bundle (offline — BundleSource, no network/token), then run the real gate
  # through the binary. `release-core gate --hook` resolves lefthook (PATH or
  # node_modules/.bin), points it at .release/lefthook.yml via LEFTHOOK_CONFIG,
  # and forwards the --file args; node consumers get their gate deps installed
  # first, same as the root-config path. Failures here are LOUD (set -e; the
  # gate verb exits 1 on a missing lefthook — it never skips).
  echo "No root gate config but release-core is present — installing the managed gate."
  if [ ! -f .release/lefthook.yml ]; then
    release-core init --no-commit
  fi
  if [ -f pnpm-lock.yaml ]; then
    corepack enable >/dev/null 2>&1 || true
    echo "→ pnpm install (frozen, no scripts) for gate deps"
    pnpm install --frozen-lockfile --ignore-scripts
  elif [ -f package-lock.json ]; then
    echo "→ npm ci (no scripts) for gate deps"
    npm ci --ignore-scripts
  elif [ -f yarn.lock ]; then
    corepack enable >/dev/null 2>&1 || true
    echo "→ yarn install (frozen, no scripts) for gate deps"
    yarn install --frozen-lockfile --ignore-scripts
  fi
  release-core gate --hook "${lefthook_file_args[@]}"
  gate_ran=1
else
  echo "No pre-commit gate detected (no lefthook / husky config, no release-core) — skipping."
fi

if [ "${gate_ran}" = "1" ] && ! git diff --quiet; then
  echo "Gate produced auto-fixes — re-staging modified files."
  git diff --name-only
  git add -u
fi
