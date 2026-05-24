# Shared test setup for tests/changelog/*.bats.
#
# Each @test runs in a fresh mktemp -d working directory so the
# scripts (which all touch CHANGELOG/ relative to PWD) are sandboxed
# from each other. The tmpdir is initialized as a git repo because
# bin/changelog{-add,-cut,-render} resolve repo root via
# `git rev-parse --show-toplevel` (per #219). Tests that want to
# verify the "not in a git repo" error path can skip git init
# explicitly.

# Resolve the repo's bin/ once so tests can call scripts without PATH
# games and so a broken PATH in a test doesn't leak in.
BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  TMPDIR_TEST="$(mktemp -d)"
  cd "$TMPDIR_TEST"
  # `git init -q` so the scripts can resolve repo root. No commits
  # needed; --show-toplevel works on an empty repo.
  git init -q
}

teardown() {
  cd /
  rm -rf "$TMPDIR_TEST"
}
