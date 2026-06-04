#!/usr/bin/env bats

# ---------------------------------------------------------------------
# release-advance-major: shim CLI contract (shell→Python migration).
# The bash original was replaced by a
# thin Python shim over release_core.verbs.release_advance_major. These
# tests pin the byte-for-byte CLI edge — help text, usage exit codes (64),
# and the wrong-origin refusal — that the bash version had. The ff-merge
# decision / push logic is covered offline by pytest
# (test_core_release_advance_major.py); here we only need the deterministic
# CLI surface, so no real remote is touched.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/ram.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

@test "--help prints usage and exits 0" {
  run "$BIN/release-core" admin release advance-major --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"floating major branch"* ]]
  [[ "$output" == *"Usage:"* ]]
}

@test "-h is an alias for --help" {
  run "$BIN/release-core" admin release advance-major -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"floating major branch"* ]]
}

@test "unknown flag exits 64" {
  run "$BIN/release-core" admin release advance-major --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg"* ]]
}

@test "refuses a non-release origin" {
  git init -q
  git remote add origin git@github.com:someone/else.git
  RELEASE_HOME="$WORK" run "$BIN/release-core" admin release advance-major
  [ "$status" -eq 1 ]
  [[ "$output" == *"refusing"* ]]
}

@test "errors when not in release repo and RELEASE_HOME points nowhere" {
  RELEASE_HOME="$WORK/does-not-exist" run "$BIN/release-core" admin release advance-major
  [ "$status" -eq 1 ]
  [[ "$output" == *"not inside the release repo"* ]]
}
