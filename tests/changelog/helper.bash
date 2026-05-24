# Shared test setup for tests/changelog/*.bats.
#
# Each @test runs in a fresh mktemp -d working directory so the
# scripts (which all touch CHANGELOG/ relative to PWD) are sandboxed
# from each other.

# Resolve the repo's bin/ once so tests can call scripts without PATH
# games and so a broken PATH in a test doesn't leak in.
BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  TMPDIR_TEST="$(mktemp -d)"
  cd "$TMPDIR_TEST"
}

teardown() {
  cd /
  rm -rf "$TMPDIR_TEST"
}
