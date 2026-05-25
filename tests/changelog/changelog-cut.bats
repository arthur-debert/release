#!/usr/bin/env bats

load helper

@test "concatenates fragments into a version file with date header" {
  "$BIN/changelog-add" 1 "- one"
  "$BIN/changelog-add" 2 "- two"
  run "$BIN/changelog-cut" 0.1.0
  [ "$status" -eq 0 ]
  [ -f CHANGELOG/0.1.0.md ]
  # Header line shape: "## <version> - YYYY-MM-DD"
  run head -1 CHANGELOG/0.1.0.md
  [[ "$output" =~ ^\#\#\ 0\.1\.0\ -\ [0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
}

@test "fragments are deleted after cut" {
  "$BIN/changelog-add" 1 "- one"
  "$BIN/changelog-add" 2 "- two"
  "$BIN/changelog-cut" 0.1.0
  run bash -c 'ls CHANGELOG/unreleased-*.md 2>/dev/null | wc -l | tr -d " "'
  [ "$output" = "0" ]
}

@test "concatenation order is stable byte-sort on filename" {
  # Fragment names: a < b < c byte-wise.
  "$BIN/changelog-add" c "- c-bullet"
  "$BIN/changelog-add" a "- a-bullet"
  "$BIN/changelog-add" b "- b-bullet"
  "$BIN/changelog-cut" 0.1.0
  run cat CHANGELOG/0.1.0.md
  # Bullets must appear in a, b, c order regardless of insert order.
  [[ "$output" == *"- a-bullet"*"- b-bullet"*"- c-bullet"* ]]
}

@test "fails when no unreleased fragments exist" {
  mkdir CHANGELOG
  run "$BIN/changelog-cut" 0.1.0
  [ "$status" -ne 0 ]
  [[ "$output" == *"no CHANGELOG/unreleased-*.md fragments"* ]]
}

@test "refuses to overwrite an existing version file" {
  "$BIN/changelog-add" 1 "- one"
  "$BIN/changelog-cut" 0.1.0
  "$BIN/changelog-add" 2 "- two"
  run "$BIN/changelog-cut" 0.1.0
  [ "$status" -ne 0 ]
  [[ "$output" == *"already exists"* ]]
  # Fragment from the second attempt must NOT have been deleted.
  [ -f CHANGELOG/unreleased-pr-2.md ]
}

@test "rejects non-semver version arg" {
  "$BIN/changelog-add" 1 "- one"
  run "$BIN/changelog-cut" "1.0"
  [ "$status" -eq 2 ]
  [[ "$output" == *"must be valid semver"* ]]
}

@test "rejects path-traversal version arg" {
  "$BIN/changelog-add" 1 "- one"
  run "$BIN/changelog-cut" "../evil"
  [ "$status" -eq 2 ]
  # No file outside CHANGELOG/ was created.
  [ ! -e ../evil.md ]
}

@test "accepts pre-release version" {
  "$BIN/changelog-add" 1 "- rc bullet"
  run "$BIN/changelog-cut" "2.0.0-rc.1"
  [ "$status" -eq 0 ]
  [ -f CHANGELOG/2.0.0-rc.1.md ]
}

@test "missing version arg exits with usage" {
  run "$BIN/changelog-cut"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "rejects 'v' prefix on version arg (#220)" {
  "$BIN/changelog-add" 1 "- bullet"
  run "$BIN/changelog-cut" v1.2.3
  [ "$status" -eq 2 ]
  [[ "$output" == *"bare semver"* ]]
  [[ "$output" == *"v prefix"* ]] || [[ "$output" == *"'v' prefix"* ]]
  # Fragment must NOT have been consumed.
  [ -f CHANGELOG/unreleased-pr-1.md ]
  [ ! -f CHANGELOG/v1.2.3.md ]
}

@test "rejects 'V' prefix on version arg (#220)" {
  "$BIN/changelog-add" 1 "- bullet"
  run "$BIN/changelog-cut" V1.2.3
  [ "$status" -eq 2 ]
  [ ! -f CHANGELOG/V1.2.3.md ]
}

@test "fragments without trailing newline get one appended (#224)" {
  mkdir -p CHANGELOG
  # Write two fragments WITHOUT trailing newlines
  printf -- '- first' > CHANGELOG/unreleased-a.md
  printf -- '- second' > CHANGELOG/unreleased-b.md
  run "$BIN/changelog-cut" 0.1.0
  [ "$status" -eq 0 ]
  # Each fragment should be on its own line
  run grep -c '^- ' CHANGELOG/0.1.0.md
  [ "$output" = "2" ]
}

@test "cwd-agnostic: works from a subdir (#219)" {
  "$BIN/changelog-add" 1 "- bullet"
  mkdir -p crates/foo
  cd crates/foo
  run "$BIN/changelog-cut" 0.1.0
  [ "$status" -eq 0 ]
  # Version file lands at repo root, not the subdir.
  [ -f "$TMPDIR_TEST/CHANGELOG/0.1.0.md" ]
  [ ! -f "$TMPDIR_TEST/crates/foo/CHANGELOG/0.1.0.md" ]
}
