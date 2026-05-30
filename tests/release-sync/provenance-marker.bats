#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Provenance marker (ADR-0002) + release-drift-check
# ---------------------------------------------------------------------

@test "sync writes a provenance marker holding the source SHA" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ -f .release/.release-sync-source ]
  expected=$(git -C "$RELEASE_HOME" rev-parse HEAD)
  grep -qx "$expected" .release/.release-sync-source
}

@test "the marker lives only in .release/ — never mirrored out as a symlink" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  # No symlink/copy of the marker leaks into the consumer tree root.
  [ ! -e ./.release-sync-source ]
  # And nothing symlinks to it.
  run bash -c 'find . -type l -lname "*release-sync-source*" | head -1'
  [ -z "$output" ]
}

@test "the marker round-trips: a freshly synced repo has zero drift" {
  "$BIN/release-sync" >/dev/null
  run "$BIN/release-drift-check"
  [ "$status" -eq 0 ]
  [[ "$output" == *"clean"* ]]
}

@test "drift-check fails when a managed symlink is replaced by a real file" {
  "$BIN/release-sync" >/dev/null
  # lefthook.yml is a symlink into .release/ on every synced consumer.
  [ -L lefthook.yml ]
  rm lefthook.yml
  printf 'pre-commit:\n  commands:\n    nope: { run: "true" }\n' > lefthook.yml
  run "$BIN/release-drift-check"
  [ "$status" -eq 1 ]
  [[ "$output" == *"DRIFT DETECTED"* ]]
  [[ "$output" == *"UPSTREAM"* ]]
}

@test "drift-check flags .release-sync.yaml keys beyond capabilities" {
  "$BIN/release-sync" >/dev/null
  cat > .release-sync.yaml <<'EOF'
capabilities: []
extra-knob: true
EOF
  run "$BIN/release-drift-check"
  [ "$status" -eq 1 ]
  [[ "$output" == *"extra-knob"* ]]
  [[ "$output" == *"beyond"* ]]
}

@test "drift-check tolerates an empty/capabilities-only .release-sync.yaml" {
  "$BIN/release-sync" >/dev/null
  printf 'capabilities: []\n' > .release-sync.yaml
  run "$BIN/release-drift-check"
  [ "$status" -eq 0 ]
}

@test "drift-check hard-fails (non-zero) on malformed .release-sync.yaml" {
  "$BIN/release-sync" >/dev/null
  printf 'this: [unclosed\n' > .release-sync.yaml
  run "$BIN/release-drift-check"
  [ "$status" -ne 0 ]
}

@test "drift-check is a no-op (exit 0) when there is no marker" {
  # A repo not on the marker-aware sync must not fail the gate.
  mkdir -p .release
  run "$BIN/release-drift-check"
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping drift gate"* ]]
}
