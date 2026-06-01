#!/usr/bin/env bats

# ---------------------------------------------------------------------
# install-release-secrets: CLI contract (shell→Python shim).
#
# This verb mutates LIVE repo secrets (gh secret set), so these tests
# only exercise the offline, side-effect-free surface: --help and the
# usage-error path. The secret-sourcing, discovery, and set-loop logic
# is unit-tested in pytest (test_core_install_release_secrets.py) with
# gh.secret_set mocked — never an actual `gh secret set` here.
#
# Pins the byte-for-byte CLI contract the bash version had: --help
# exits 0 with usage, an unknown flag exits 64.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

@test "install-release-secrets --help exits 0 and prints usage" {
  run "$BIN/install-release-secrets" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"install-release-secrets"* ]]
}

@test "install-release-secrets --help lists the secret sources" {
  run "$BIN/install-release-secrets" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"APPLE_CERTIFICATE_P12_BASE64"* ]]
  [[ "$output" == *"HOMEBREW_TAP_TOKEN"* ]]
}

@test "install-release-secrets unknown flag exits 64" {
  run "$BIN/install-release-secrets" --bogus
  [ "$status" -eq 64 ]
}

@test "install-release-secrets missing auth source exits 1 (before any discovery/set)" {
  # Point at an empty auth dir + clear the required env so sourcing fails fast.
  EMPTY="$(mktemp -d "${BATS_TMPDIR:-/tmp}/irs.XXXXXX")"
  run env -u CRATES_IO_KEY -u HOMEBREW_TAP_TOKEN \
    "$BIN/install-release-secrets" --auth-dir "$EMPTY" --repos owner/repo
  rm -rf "$EMPTY"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing:"* ]]
}
