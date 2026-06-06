#!/usr/bin/env bats

load helper

SCRIPT="$BATS_TEST_DIRNAME/../../bin-internal/roll-changelog.sh"

@test "extract: legacy single-file (no CHANGELOG/ dir) exits 1 with migration guidance" {
  echo "## [Unreleased]" > CHANGELOG.md
  run bash "$SCRIPT" extract CHANGELOG.md notes.md
  [ "$status" -eq 1 ]
  [[ "$output" == *"Legacy single-file changelog mode has been retired"* ]]
  [[ "$output" == *"CHANGELOG/legacy.md"* ]]
}

@test "roll: legacy single-file (no CHANGELOG/ dir) exits 1 with migration guidance" {
  echo "## [Unreleased]" > CHANGELOG.md
  run bash "$SCRIPT" roll CHANGELOG.md 1.0.0 notes.md
  [ "$status" -eq 1 ]
  [[ "$output" == *"Legacy single-file changelog mode has been retired"* ]]
}

@test "extract: fragment-dir with fragments succeeds" {
  mkdir CHANGELOG
  echo "- fix: something" > CHANGELOG/unreleased-fix.md
  echo "## [Unreleased]" > CHANGELOG.md
  # extract doesn't call the `changelog` console-script (only roll does),
  # it just cats the fragments.
  run bash "$SCRIPT" extract CHANGELOG.md notes.md
  [ "$status" -eq 0 ]
  [ -f notes.md ]
  [[ "$(cat notes.md)" == *"fix: something"* ]]
}
