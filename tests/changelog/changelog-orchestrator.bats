#!/usr/bin/env bats

load helper

@test "no args prints usage and exits 2" {
  run "$BIN/changelog"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "--help exits 0 and prints usage" {
  run "$BIN/changelog" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage:"* ]]
}

@test "-h is an alias for --help" {
  run "$BIN/changelog" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage:"* ]]
}

@test "unknown command exits 2 with helpful error" {
  run "$BIN/changelog" frobnicate
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command"* ]]
}

@test "add forwards to changelog-add" {
  "$BIN/changelog" add 1 "- bullet"
  [ -f CHANGELOG/unreleased-pr-1.md ]
}

@test "cut forwards to changelog-cut" {
  "$BIN/changelog" add 1 "- bullet"
  "$BIN/changelog" cut 0.1.0
  [ -f CHANGELOG/0.1.0.md ]
  [ ! -f CHANGELOG/unreleased-pr-1.md ]
}

@test "render forwards to changelog-render" {
  mkdir CHANGELOG
  "$BIN/changelog" render
  [ -f CHANGELOG.md ]
}

@test "new-version is cut + render" {
  "$BIN/changelog" add 1 "- bullet 1"
  "$BIN/changelog" add 2 "- bullet 2"
  "$BIN/changelog" new-version 0.1.0
  # Cut happened:
  [ -f CHANGELOG/0.1.0.md ]
  [ ! -f CHANGELOG/unreleased-pr-1.md ]
  [ ! -f CHANGELOG/unreleased-pr-2.md ]
  # Render happened:
  [ -f CHANGELOG.md ]
  run grep -c '^## 0.1.0' CHANGELOG.md
  [ "$output" = "1" ]
}

@test "new-version without version arg exits with usage" {
  run "$BIN/changelog" new-version
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}
