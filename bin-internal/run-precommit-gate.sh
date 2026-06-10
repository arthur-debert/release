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

# WS3 (release#524): the gate config lives in the ephemeral .release/ build dir;
# the root lefthook.yml symlink is no longer tracked. Prefer the managed config via
# LEFTHOOK_CONFIG so lefthook finds it without a root file, falling back to a root
# config for a not-yet-migrated consumer (or release's own repo). .release/lefthook.yml
# exists only when the caller materialized it (e.g. rust-cli's arm-gate before
# prepare-release); when it is absent this is a no-op and detection falls through.
if [ -f .release/lefthook.yml ]; then
  export LEFTHOOK_CONFIG=.release/lefthook.yml
fi

gate_ran=0
if [ -n "${LEFTHOOK_CONFIG:-}" ] || [ -f lefthook.yml ] || [ -f .lefthook.yml ] || [ -f lefthook.yaml ]; then
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
elif command -v release-core >/dev/null 2>&1; then
  # The hollow-green spot (release#531 F1): a post-WS3 managed consumer has NO
  # root gate config (the gate lives in the ephemeral .release/, WS4), and
  # outside rust-cli's arm-gate nothing materialized .release/ — so this branch
  # used to fall through to "skipping" and the bot commit went UNGATED. The
  # release flows install release-core earlier (install-release-core-pkg.sh /
  # install-release-core), so: materialize the managed tree from the wheel
  # bundle (offline — BundleSource, no network/token), then run the real gate
  # through the binary. `release-core gate --hook` resolves lefthook (PATH or
  # node_modules/.bin), points it at .release/lefthook.yml via LEFTHOOK_CONFIG,
  # and forwards the --file args; node consumers get their gate deps installed
  # first, same as the root-config path. Failures here are LOUD (set -e; the
  # gate verb exits 1 on a missing lefthook — it never skips).
  echo "No root gate config but release-core is present — materializing the managed gate."
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
  echo "No pre-commit gate detected (no lefthook / pre-commit / husky config, no release-core) — skipping."
fi

if [ "${gate_ran}" = "1" ] && ! git diff --quiet; then
  echo "Gate produced auto-fixes — re-staging modified files."
  git diff --name-only
  git add -u
fi
