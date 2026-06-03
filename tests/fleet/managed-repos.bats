#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# managed-repos: zero-logic fleet accessor
# ---------------------------------------------------------------------

@test "--list prints every repo (owner/name) from the manifest" {
  run "$BIN/managed-repos" --list
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 4 ]
  [[ "$output" == *"lex-fmt/lex"* ]]
  [[ "$output" == *"arthur-debert/dodot"* ]]
}

@test "--paths joins REPOS_ROOT/<path> verbatim — no inference from repo name" {
  run "$BIN/managed-repos" --paths
  [ "$status" -eq 0 ]
  # lex-fmt/lex lives under the org-named dir, exists → found
  [[ "$output" == *"lex-fmt/lex	$REPOS_ROOT/lex-fmt/lex	found"* ]]
  # single-repo project collapses to a bare dir, exists → found
  [[ "$output" == *"arthur-debert/dodot	$REPOS_ROOT/dodot	found"* ]]
}

@test "--paths reports missing for repos whose path has no checkout" {
  run "$BIN/managed-repos" --paths
  [ "$status" -eq 0 ]
  [[ "$output" == *"lex-fmt/comms	$REPOS_ROOT/lex-fmt/comms	missing"* ]]
  [[ "$output" == *"phos-editor/app	$REPOS_ROOT/phos/phos-app	missing"* ]]
}

@test "a trailing owner/name filter restricts every mode" {
  run "$BIN/managed-repos" --list arthur-debert/dodot lex-fmt/comms
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 2 ]
  [[ "$output" == *"arthur-debert/dodot"* ]]
  [[ "$output" != *"lex-fmt/lex"* ]]
}

@test "REPOS_ROOT relocates the whole fleet with no logic change" {
  export REPOS_ROOT=/somewhere/else
  run "$BIN/managed-repos" --paths
  [ "$status" -eq 0 ]
  [[ "$output" == *"arthur-debert/dodot	/somewhere/else/dodot	missing"* ]]
}

@test "an unknown flag is a usage error" {
  run "$BIN/managed-repos" --bogus
  [ "$status" -eq 64 ]
}
