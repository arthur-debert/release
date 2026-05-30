# Shared setup for tests/release-sync/*.bats.
#
# Each @test runs in a fresh temp git repo acting as a synthetic consumer.
# release-sync materializes managed files from $RELEASE_HOME (this repo)
# at the recorded ref. We pin RELEASE_REF=HEAD so the sync reads templates
# from the committed tree and never touches the network.

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"
RELEASE_HOME_ABS="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

setup() {
  harness_create_workspace_notrap
  cd "$HARNESS_WORKSPACE"
  git init -q
  # Minimal fixture: grammar.js → Kind=tree-sitter (manifest-less, so the
  # sync is just commons + the tree-sitter subtree — fewest moving parts).
  touch grammar.js
  export PATH="$BIN:$PATH"
  export RELEASE_HOME="$RELEASE_HOME_ABS"
  export RELEASE_REF=HEAD
}

teardown() {
  cd /
  harness_cleanup
}
