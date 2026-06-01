#!/usr/bin/env bats

# ---------------------------------------------------------------------
# list-repo-pr / list-repo-scripts: CLI contract (shell→Python shims).
#
# These verbs aggregate over the hardcoded managed-repo set, reaching
# the gh API (list-repo-pr) or the local filesystem (list-repo-scripts).
# The data paths are unit-tested in pytest against recorded fixtures;
# here we pin the offline CLI contract the bash versions had: --help
# exits 0 with usage, and an unknown flag exits 64.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

@test "list-repo-pr --help exits 0 and prints usage" {
  run "$BIN/list-repo-pr" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"list-repo-pr"* ]]
}

@test "list-repo-pr unknown flag exits 64" {
  run "$BIN/list-repo-pr" --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg"* ]]
}

@test "list-repo-scripts --help exits 0 and prints usage" {
  run "$BIN/list-repo-scripts" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"list-repo-scripts"* ]]
}

@test "list-repo-scripts unknown flag exits 64" {
  run "$BIN/list-repo-scripts" --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg"* ]]
}

@test "list-repo-scripts --owner with no present repos still prints totals footer" {
  # REPOS_ROOT points the verb's hardcoded $HOME/h paths nowhere relevant;
  # an owner that matches no cloned repo yields just the totals footer.
  run "$BIN/list-repo-scripts" --owner nonesuch-owner
  [ "$status" -eq 0 ]
  [[ "$output" == *"totals across fleet"* ]]
}
