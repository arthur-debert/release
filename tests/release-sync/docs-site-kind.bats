#!/usr/bin/env bats

# ---------------------------------------------------------------------
# docs-site Kind (mkdocs) — release#353 §A (onboarding lex-fmt/comms)
#
# A pure-content/docs repo (an mkdocs site) has no compiled-artifact
# signal, so detect-kind used to fail and release-sync aborted with
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

setup() {
  harness_create_workspace_notrap
  cd "$HARNESS_WORKSPACE"
  git init -q
  # docs-site fixture: a root mkdocs.yml is the Kind signal. Declare the
  # mkdocs Capability so check-docs is materialized too.
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
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

@test "a root mkdocs.yml wins over a legacy docs/_config.yml" {
  # Mid-migration repos can carry both; mkdocs.yml is the discriminator.
  mkdir -p docs
  touch docs/_config.yml _config.yml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

@test "release-sync succeeds on a docs-site repo (no 'could not detect kind')" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [[ "$output" == *"kind:         docs-site"* ]]
  [[ "$output" != *"could not detect kind"* ]]
}

@test "the docs-site Kind ships bin/check (mkdocs build --strict)" {
  "$BIN/release-sync" >/dev/null
  [ -f .release/bin/check ]
  [ -L bin/check ]
  [ "$(readlink bin/check)" = "../.release/bin/check" ]
  grep -qF 'mkdocs build --strict' .release/bin/check
}

@test "the mkdocs Capability ships check-docs on top of the Kind" {
  "$BIN/release-sync" >/dev/null
  [ -f .release/bin/check-docs ]
  [ -L bin/check-docs ]
}

@test "the commons gate + orientation block land on a docs-site repo" {
  "$BIN/release-sync" >/dev/null
  [ -f .release/lefthook.yml ]
  [ -f CLAUDE.md ]
  grep -qF '@.release/ORIENTATION.md' CLAUDE.md
}

@test "a second sync is idempotent — --check reports clean" {
  "$BIN/release-sync" >/dev/null
  run "$BIN/release-sync" --check
  [ "$status" -eq 0 ]
}
