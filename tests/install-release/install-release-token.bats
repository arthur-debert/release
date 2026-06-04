#!/usr/bin/env bats

# ---------------------------------------------------------------------
# install-release-token: CLI contract (shell→Python shim).
#
# This verb mutates LIVE repo secrets (gh secret set), so these tests
# only exercise the offline, side-effect-free surface: --help, the
# usage-error path, and the stdin guards (tty / empty token) that fire
# before any network or secret write. The token-validation, discovery,
# and set-then-verify logic is unit-tested in pytest
# (test_core_install_release_token.py) with the gh boundary mocked.
#
# Pins the byte-for-byte CLI contract the bash version had.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

@test "install-release-token --help exits 0 and prints usage" {
  run "$BIN/release-core" admin secrets token --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"install-release-token"* ]]
}

@test "install-release-token unknown flag exits 64" {
  run "$BIN/release-core" admin secrets token --bogus
  [ "$status" -eq 64 ]
}

@test "install-release-token empty stdin exits 64" {
  run bash -c "printf '' | '$BIN/release-core' admin secrets token"
  [ "$status" -eq 64 ]
  [[ "$output" == *"empty token"* ]]
}

@test "install-release-token whitespace-only stdin exits 64" {
  run bash -c "printf '  \n\r ' | '$BIN/release-core' admin secrets token"
  [ "$status" -eq 64 ]
  [[ "$output" == *"empty token"* ]]
}
