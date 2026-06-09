#!/usr/bin/env bats

load helper

# The unified shell gate: one runner,
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

@test "check-shell skips a .sh whose shebang is zsh (shebang vetoes the extension)" {
  # release#374: a zsh script named *.sh was forced into shellcheck → SC1071.
  # The shebang is authoritative — a non-shellcheck interpreter wins over the
  # .sh/.bash extension shortcut.
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  printf '#!/usr/bin/env zsh\nprint hi\n' > generate_icons.sh
  run "$CHECK_SHELL" generate_icons.sh
  [ "$status" -eq 0 ]
}

@test "check-shell still lints a .sh whose shebang is bash" {
  command -v shellcheck >/dev/null || skip "shellcheck not installed"
  printf '#!/usr/bin/env bash\nrm $UNQUOTED\n' > tool.sh
  run "$CHECK_SHELL" tool.sh
  [ "$status" -eq 1 ]
  [[ "$output" == *"SC2086"* ]]
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
  release_sync >/dev/null
  # The synced consumer's bin/ holds the distributed tools (install-release-core,
  # setup-dev-env.sh, vendored share/, ...). The unified gate must pass over
  # them — non-shell falls out by content.
  run bash -c 'shopt -s nullglob; "'"$CHECK_SHELL"'" bin/* .release/bin/*'
  [ "$status" -eq 0 ]
}

# --- WS3 (release#524): the gate lives ONLY in .release/, not at the root ----

@test "the gate config + tool configs land in .release/ but NOT at the consumer root" {
  release_sync >/dev/null
  # Materialized into the ephemeral build dir...
  [ -f .release/lefthook.yml ]
  for c in .markdownlint.json .markdownlintignore .yamllint .shellcheckrc .prettierignore; do
    [ -f ".release/$c" ] || { echo "missing .release/$c"; false; }
  done
  # ...and NOT mirrored out to the consumer root (no tracked gate files).
  [ ! -e lefthook.yml ] && [ ! -L lefthook.yml ]
  for c in .markdownlint.json .markdownlintignore .yamllint .shellcheckrc .prettierignore; do
    { [ ! -e "$c" ] && [ ! -L "$c" ]; } || { echo "leaked root $c"; false; }
  done
}

@test ".editorconfig stays mirrored to the consumer root (editor-facing, not a gate flag)" {
  release_sync >/dev/null
  [ -f .release/.editorconfig ]
  [ -L .editorconfig ]
  [ "$(readlink .editorconfig)" = ".release/.editorconfig" ]
}

@test "a pre-WS3 consumer's tracked root gate symlinks are swept on re-sync (migration)" {
  # Seed a pre-WS3 layout: a live .release/ carrying the gate files, plus
  # committed root symlinks into it (so they RESOLVE now — a presence test would
  # leave them behind; the mirrored-dest sweep removes them).
  mkdir -p .release
  for f in lefthook.yml .markdownlint.json .yamllint; do
    printf 'seed\n' > ".release/$f"
    ln -s ".release/$f" "$f"
    [ -e "$f" ]   # resolves before the sync
  done
  run release_sync
  [ "$status" -eq 0 ]
  # The stale root symlinks are gone; the managed copies remain in .release/.
  for f in lefthook.yml .markdownlint.json .yamllint; do
    [ ! -L "$f" ] || { echo "stale root $f not swept"; false; }
    [ -f ".release/$f" ] || { echo "missing .release/$f"; false; }
  done
}

@test "release-core gate --install-hook wires .git/hooks/pre-commit through the binary" {
  release_sync >/dev/null
  run release-core gate --install-hook
  [ "$status" -eq 0 ]
  hook="$(git rev-parse --git-path hooks)/pre-commit"
  [ -f "$hook" ]
  [ -x "$hook" ]
  grep -qF 'release-core gate --hook' "$hook"
}
