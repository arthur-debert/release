#!/usr/bin/env bats

# ---------------------------------------------------------------------
# release-beta-list: shim CLI contract (shell→Python migration,
# docs/proposals/shell-to-python.md). The bash original was replaced by a
# thin Python shim over release_core.verbs.release_beta_list. These tests
# pin the byte-for-byte CLI edge — help text, usage exit codes (64), the
# not-a-clone error, and the "(none)" empty case — that the bash version
# had. The age/ahead-of-main column rendering is covered offline by pytest
# (test_core_release_beta_list.py).
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/rbl.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

@test "--help prints usage and exits 0" {
  run "$BIN/release-beta-list" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"release/beta"* ]]
  [[ "$output" == *"Usage:"* ]]
}

@test "-h is an alias for --help" {
  run "$BIN/release-beta-list" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "unknown arg exits 64" {
  run "$BIN/release-beta-list" --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg"* ]]
}

@test "RELEASE_HOME that is not a git clone errors" {
  RELEASE_HOME="$WORK/not-a-clone" run "$BIN/release-beta-list"
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not a git clone"* ]]
}

@test "no open betas prints (none)" {
  # A real clone with a fetchable (local) origin but no release/beta/* refs:
  # fetch succeeds offline, for-each-ref over the beta namespace yields nothing.
  # (The bash original fetches without `|| true`, so a fetchable origin is
  # required in either implementation.)
  git init -q --bare "$WORK/origin.git"
  git init -q "$WORK/clone"
  cd "$WORK/clone"
  git config user.email t@t
  git config user.name t
  git remote add origin "$WORK/origin.git"
  git commit -q --allow-empty -m init
  git push -q origin HEAD:refs/heads/main
  RELEASE_HOME="$WORK/clone" run "$BIN/release-beta-list"
  [ "$status" -eq 0 ]
  [ "$output" = "(none)" ]
}
