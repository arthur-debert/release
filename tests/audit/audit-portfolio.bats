#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# audit-portfolio: fleet aggregation over audit-repo. Shim CLI contract
# + --json stream (one object per repo) + table, offline via stub `gh`.
# ---------------------------------------------------------------------

@test "--help exits 0 and prints usage" {
  run "$BIN/audit-portfolio" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"audit-portfolio"* ]]
}

@test "an unknown flag is a usage error (exit 64)" {
  run "$BIN/audit-portfolio" --nope
  [ "$status" -eq 64 ]
}

@test "--repos with no value is a usage error (exit 64)" {
  run "$BIN/audit-portfolio" --repos
  [ "$status" -eq 64 ]
}

@test "--json with --repos emits one JSON object per repo" {
  _install_stub_gh
  run "$BIN/audit-portfolio" --repos o/a,o/b --json
  [ "$status" -eq 0 ]
  # two non-empty JSON lines, each a well-formed audit-repo object.
  local lines
  lines=$(printf '%s\n' "$output" | grep -c '^{')
  [ "$lines" -eq 2 ]
  [ "$(printf '%s\n' "$output" | sed -n '1p' | jq -r '.repo')" = "o/a" ]
  [ "$(printf '%s\n' "$output" | sed -n '2p' | jq -r '.repo')" = "o/b" ]
}

@test "explicit --repos prints a summary table and a summary line" {
  _install_stub_gh
  run "$BIN/audit-portfolio" --repos o/a
  [[ "$output" == *"REPO"* ]]
  [[ "$output" == *"STATUS"* ]]
  [[ "$output" == *"summary:"* ]]
}
