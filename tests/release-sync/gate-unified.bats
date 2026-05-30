#!/usr/bin/env bats

load helper

# The unified shell gate (docs/proposals/unified-gate.lex): one runner,
# content-based selection, dogfooded by release over its own distributed output.

CHECK_SHELL="$BATS_TEST_DIRNAME/../../templates/commons/bin/check-shell"

# --- check-shell: selection is by content, not path glob ---------------

@test "check-shell lints a real shell file and flags its issues" {
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  printf '#!/usr/bin/env bash\nrm $UNQUOTED\n' > bad.sh
  run "$CHECK_SHELL" bad.sh
  [ "$status" -eq 1 ]
  [[ "$output" == *"SC2086"* ]]
}

@test "check-shell skips a Python shim, a Dockerfile, and vendored share/" {
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  printf '#!/usr/bin/env python3\nimport sys\n' > shim
  printf 'FROM alpine\n' > Dockerfile.sandbox
  # Vendored trees live under bin/share/ (e.g. the semver-tool).
  mkdir -p bin/share && printf '#!/usr/bin/env bash\nx=$BAD\n' > bin/share/vendored
  run "$CHECK_SHELL" shim Dockerfile.sandbox bin/share/vendored
  [ "$status" -eq 0 ]
}

@test "check-shell follows a symlink and skips a shim pointing at Python" {
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  mkdir -p real && printf '#!/usr/bin/env python3\nimport sys\n' > real/task.py
  ln -s real/task.py gh-task-status
  run "$CHECK_SHELL" gh-task-status
  [ "$status" -eq 0 ]
}

# --- dogfood: distributed tools pass the gate in the synced layout -----

@test "dogfood: synced distributed tools pass the unified gate (shim skipped)" {
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  "$BIN/release-sync" >/dev/null
  # The synced consumer's bin/ holds the distributed tools (changelog,
  # setup-dev-env.sh, the gh-task-status Python shim, vendored share/, ...).
  # The unified gate must pass over them — non-shell falls out by content.
  run bash -c 'shopt -s nullglob; "'"$CHECK_SHELL"'" bin/* .release/bin/*'
  [ "$status" -eq 0 ]
}
