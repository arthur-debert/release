#!/usr/bin/env bats

# ---------------------------------------------------------------------
# docs-site Kind (mkdocs) — release#353 §A (onboarding lex-fmt/comms)
#
# A pure-content/docs repo (an mkdocs site) has no compiled-artifact
# signal, so detect-kind used to fail and the install engine aborted with
# "could not detect kind". The docs-site Kind recognises a root
# mkdocs.yml and ships a manifest-less Kind tree (bin/check). The repo
# opts into the mkdocs Capability via .release-sync.yaml.
#
# This suite does NOT use the shared helper's tree-sitter fixture — it
# builds its own docs-site fixture per test.
# ---------------------------------------------------------------------

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"
RELEASE_HOME_ABS="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

# Install the managed files via the current entry point (`release-core init`); the
# standalone `release-sync` verb was retired in WS4 (release#521). --no-commit:
# the suite asserts filesystem state, never git history. (This suite builds its
# own docs-site fixture and does NOT load the shared helper, so it defines this.)
release_sync() {
  release-core init --no-commit "$@"
}

setup() {
  harness_create_workspace_notrap
  cd "$HARNESS_WORKSPACE"
  git init -q
  # docs-site fixture: a root mkdocs.yml is the Kind signal. Declare the
  # mkdocs Capability so check-docs is installed too.
  touch mkdocs.yml
  printf 'capabilities:\n  - mkdocs\n' > .release-sync.yaml
  export PATH="$BIN:$PATH"
  export RELEASE_HOME="$RELEASE_HOME_ABS"
  export RELEASE_REF=HEAD
}

teardown() {
  cd /
  harness_cleanup
}

@test "detect-kind classifies a root mkdocs.yml as docs-site" {
  run "$BIN/release-core" detect-kind
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

@test "a root mkdocs.yml wins over a legacy docs/_config.yml" {
  # Mid-migration repos can carry both; mkdocs.yml is the discriminator.
  mkdir -p docs
  touch docs/_config.yml _config.yml
  run "$BIN/release-core" detect-kind
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

@test "init succeeds on a docs-site repo (no 'could not detect kind')" {
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" != *"could not detect kind"* ]]
  # The docs-site Kind tree installed (manifest-less Kind detected from mkdocs.yml).
  [ -f .release/bin/check ]
}

@test "the docs-site Kind ships bin/check (mkdocs build --strict)" {
  release_sync >/dev/null
  [ -f .release/bin/check ]
  [ -L bin/check ]
  [ "$(readlink bin/check)" = "../.release/bin/check" ]
  grep -qF 'mkdocs build --strict' .release/bin/check
}

@test "the mkdocs Capability ships check-docs on top of the Kind" {
  release_sync >/dev/null
  [ -f .release/bin/check-docs ]
  [ -L bin/check-docs ]
}

@test "the commons gate + @import land on a docs-site repo" {
  release_sync >/dev/null
  [ -f .release/lefthook.yml ]
  [ -f CLAUDE.md ]
  # WS4 (#761): CLAUDE.md carries a one-line @import of the managed target;
  # the `release-core how-to` orientation body now lives in that target file.
  grep -qF '@.claude/IMPORTANT-RELEASE.md' CLAUDE.md
  [ -f .claude/IMPORTANT-RELEASE.md ]
  grep -qF 'release-core how-to' .claude/IMPORTANT-RELEASE.md
}

@test "a second init is idempotent — reports already current" {
  release_sync >/dev/null
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" == *"already current"* ]]
}
