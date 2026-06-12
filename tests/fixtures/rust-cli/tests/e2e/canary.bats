#!/usr/bin/env bats
# E2E suite for the canary binary. Run by the managed bin/check-e2e
# (bats Component); CI's e2e job exports CANARY_BIN pointing at the
# artifact built by the check job. See arthur-debert/release#587.

setup() {
  : "${CANARY_BIN:?CANARY_BIN must point at the canary binary}"
}

@test "prints the default liveness line" {
  run "$CANARY_BIN"
  [ "$status" -eq 0 ]
  [ "$output" = "canary is alive" ]
}

@test "honors a custom name argument" {
  run "$CANARY_BIN" tweety
  [ "$status" -eq 0 ]
  [ "$output" = "tweety is alive" ]
}

@test "--version prints the crate version" {
  run "$CANARY_BIN" --version
  [ "$status" -eq 0 ]
  [[ "$output" =~ ^canary\ [0-9]+\.[0-9]+\.[0-9]+ ]]
}
