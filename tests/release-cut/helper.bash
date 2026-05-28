# Shared test setup for tests/release-cut/*.bats.
#
# Each @test runs in a fresh mktemp -d working directory initialized
# as a git repo, because release-cut resolves repo root via
# `git rev-parse --show-toplevel`.

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"
TEMPLATES_BIN="$BATS_TEST_DIRNAME/../../templates/commons/bin"

setup() {
  harness_create_workspace_notrap
  TMPDIR_TEST="$HARNESS_WORKSPACE"
  cd "$HARNESS_WORKSPACE"
  git init -q
}

teardown() {
  cd /
  harness_cleanup
}
