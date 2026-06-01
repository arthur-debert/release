# Shared test setup for tests/changelog/*.bats.
#
# Each @test runs in a fresh mktemp -d working directory so the
# scripts (which all touch CHANGELOG/ relative to PWD) are sandboxed
# from each other. The tmpdir is initialized as a git repo because
# bin/changelog{-add,-cut,-render} resolve repo root via
# `git rev-parse --show-toplevel` (per #219). Tests that want to
# verify the "not in a git repo" error path can skip git init
# explicitly.

# The harness lives in the bats component template during development;
# consumers get it via release-sync.
source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  harness_create_workspace_notrap
  TMPDIR_TEST="$HARNESS_WORKSPACE"
  cd "$HARNESS_WORKSPACE"
  git init -q
  # CI runners carry no ambient git identity, and with auto-detection
  # disabled (user.useConfigOnly) `git commit` aborts with exit 128. Pin a
  # self-contained identity (+ disable signing, set the default branch) in
  # the fixture repo so any test that commits is independent of host git
  # config. Regression: PR #408 (run-precommit-gate-deletions failed 128 in CI).
  git config user.email "tests@release.invalid"
  git config user.name "Release Tests"
  git config commit.gpgsign false
  git config tag.gpgsign false
  git config init.defaultBranch main
}

teardown() {
  cd /
  harness_cleanup
}
