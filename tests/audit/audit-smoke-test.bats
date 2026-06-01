#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# audit-smoke-test: live PR-loop smoke harness. Only the arg-parse /
# usage contract is testable offline (the body clones + opens a real PR
# in GitHub Actions — that is the script's whole point), so we assert
# the flag arity + help here. No PR is ever opened by these tests.
# ---------------------------------------------------------------------

@test "--help exits 0 and prints usage" {
  run "$BIN/audit-smoke-test" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"audit-smoke-test"* ]]
}

@test "no repo positional is a usage error (exit 64)" {
  run "$BIN/audit-smoke-test"
  [ "$status" -eq 64 ]
  [[ "$output" == *"usage:"* ]]
}

@test "an unknown flag is a usage error (exit 64)" {
  run "$BIN/audit-smoke-test" --nope
  [ "$status" -eq 64 ]
}

@test "an extra positional is a usage error (exit 64)" {
  run "$BIN/audit-smoke-test" o/r extra
  [ "$status" -eq 64 ]
  [[ "$output" == *"extra arg"* ]]
}

@test "--base with no value is a usage error (exit 64)" {
  run "$BIN/audit-smoke-test" o/r --base
  [ "$status" -eq 64 ]
}
