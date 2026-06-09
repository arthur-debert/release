# Shared setup for tests/release-sync/*.bats.
#
# Each @test runs in a fresh temp git repo acting as a synthetic consumer.
# `release-core init` composes the managed tree from $RELEASE_HOME (this repo)
# at the recorded ref. We pin RELEASE_REF=HEAD so it reads templates from the
# committed tree and never touches the network.
#
# WS4 (release#521) retired the standalone `release-sync` verb; the SAME compose
# engine now runs under `release-core init`. `release_sync` below is a thin alias
# so these suites keep exercising that engine through its current entry point.
# --no-commit: the suites assert filesystem state (.release/ + mirrors), never
# git history, so init must not auto-commit into the synthetic consumer.

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"
RELEASE_HOME_ABS="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

# Compose the managed tree via the current entry point (`release-core init`).
# `release-core` resolves from $BIN (the in-checkout shim) on the exported PATH.
release_sync() {
  release-core init --no-commit "$@"
}

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
