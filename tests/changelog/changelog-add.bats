#!/usr/bin/env bats

load helper

@test "writes fragment from inline args" {
  run "$BIN/changelog-add" pr-142 "- Fix tokenizer crash (#142)"
  [ "$status" -eq 0 ]
  [ -f CHANGELOG/unreleased-pr-142.md ]
  [ "$(cat CHANGELOG/unreleased-pr-142.md)" = "- Fix tokenizer crash (#142)" ]
}

@test "reads body from stdin when no body args given" {
  printf -- '- bullet 1\n- bullet 2\n' | "$BIN/changelog-add" multi
  [ -f CHANGELOG/unreleased-multi.md ]
  # Two bullets, each on its own line.
  run cat CHANGELOG/unreleased-multi.md
  [ "${lines[0]}" = "- bullet 1" ]
  [ "${lines[1]}" = "- bullet 2" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "stdin preserves trailing blank line" {
  printf -- '- one\n- two\n\n' | "$BIN/changelog-add" trailing
  # Trailing blank line ⇒ file ends in two newlines (0x0a 0x0a). Count
  # newlines in the last two bytes — portable across BSD and GNU.
  run bash -c "tail -c 2 CHANGELOG/unreleased-trailing.md | wc -l | tr -d ' '"
  [ "$output" -eq 2 ]
}

@test "numeric slug is prefixed with pr-" {
  "$BIN/changelog-add" 142 "- numeric"
  [ -f CHANGELOG/unreleased-pr-142.md ]
  [ ! -f CHANGELOG/unreleased-142.md ]
}

@test "non-numeric slug is used verbatim" {
  "$BIN/changelog-add" fix-token-leak "- alpha"
  [ -f CHANGELOG/unreleased-fix-token-leak.md ]
}

@test "collision on existing slug fails non-zero" {
  "$BIN/changelog-add" 142 "first"
  run "$BIN/changelog-add" 142 "second"
  [ "$status" -ne 0 ]
  [[ "$output" == *"already exists"* ]]
  # Original content untouched.
  [ "$(cat CHANGELOG/unreleased-pr-142.md)" = "first" ]
}

@test "--force overrides an existing slug" {
  "$BIN/changelog-add" 142 "first"
  run "$BIN/changelog-add" --force 142 "second"
  [ "$status" -eq 0 ]
  [ "$(cat CHANGELOG/unreleased-pr-142.md)" = "second" ]
}

@test "slug with slash is rejected (path traversal)" {
  run "$BIN/changelog-add" '../evil' "x"
  [ "$status" -ne 0 ]
  [[ "$output" == *"slug must match"* ]]
  [ ! -f ../evil ]
  [ ! -f CHANGELOG/unreleased-../evil.md ]
}

@test "slug starting with dot is rejected" {
  run "$BIN/changelog-add" '.hidden' "x"
  [ "$status" -ne 0 ]
}

@test "missing slug exits with usage" {
  run "$BIN/changelog-add"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "creates CHANGELOG/ if it doesn't exist" {
  [ ! -d CHANGELOG ]
  "$BIN/changelog-add" first "- bullet"
  [ -d CHANGELOG ]
}

@test "cwd-agnostic: works from a subdir (#219)" {
  mkdir -p crates/foo
  cd crates/foo
  "$BIN/changelog-add" 142 "- bullet from subdir"
  # The fragment lands in the REPO-ROOT CHANGELOG/, not the subdir.
  [ -f "$TMPDIR_TEST/CHANGELOG/unreleased-pr-142.md" ]
  [ ! -d "$TMPDIR_TEST/crates/foo/CHANGELOG" ]
}

@test "errors when invoked outside a git repo (#219)" {
  # Replace the git-initialized tmpdir with a non-git one for this test.
  rm -rf .git
  run "$BIN/changelog-add" 142 "- bullet"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must be invoked inside a git repository"* ]]
}
