#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# gh-task-status: command contract (offline — no gh/network)
# ---------------------------------------------------------------------

@test "--help prints usage and exits 0" {
  run "$BIN/gh-task-status" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"gh-task-status"* ]]
  [[ "$output" == *"where does this PR stand"* ]]
}

@test "non-numeric PR number is a usage error (64)" {
  run "$BIN/gh-task-status" abc
  [ "$status" -eq 64 ]
  [[ "$output" == *"numeric"* ]]
}

@test "unknown option is a usage error (64)" {
  run "$BIN/gh-task-status" --nope
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown option"* ]]
}

@test "too many arguments is a usage error (64)" {
  run "$BIN/gh-task-status" 1 2
  [ "$status" -eq 64 ]
  [[ "$output" == *"too many"* ]]
}
