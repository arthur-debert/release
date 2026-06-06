#!/usr/bin/env bats

# shim-deref — locks the action-download dereference regression.
#
# The variant-(a) shims in templates/commons/bin/ are distributed to consumers
# and to the v2 action download. In a normal checkout bin/<name> is a SYMLINK
# into templates/commons/bin/, so realpath redirects and the shim's relative
# package path (../lib/<pkg>) resolves. But GitHub DEREFERENCES the symlink when
# it packages a composite action: inside _actions/arthur-debert/release/v2/bin/
# the shim is a REAL FILE, realpath no longer redirects, ../lib/ points at a
# nonexistent top-level lib/ → ModuleNotFoundError. This killed lex's
# rust-cli.yml@v2 prepare step (roll-changelog.sh → bin/changelog).
#
# For EACH of the variant-(a) shims this suite reconstructs that exact layout — a real-file
# copy at <tmp>/bin/<name> (NOT a symlink), the package tree at
# <tmp>/templates/commons/lib/<pkg>, and NO top-level lib/ — then runs the shim
# and asserts it does NOT die with ModuleNotFoundError. The fix's second
# candidate path (../templates/commons/lib/<pkg>) is what keeps this green.

REPO_ROOT="$BATS_TEST_DIRNAME/../.."

# name|pkg for each variant-(a) shim. All resolve to release_core. (gh-task-status
# and gh-release-issue were retired as synced shims in #476 — they ship purely as
# pip console-scripts now — so they are no longer in this set. The changelog
# family is still distributed as sys.path shims because the release composite
# actions / gh-action.yml execute them by file-path from the action checkout,
# where the wheel is not pip-installed; see #476's blocker note.)
SHIMS=(
  "changelog|release_core"
  "changelog-add|release_core"
  "changelog-cut|release_core"
  "changelog-render|release_core"
)

setup() {
  TMP="$(mktemp -d)"
}

teardown() {
  [ -n "$TMP" ] && rm -rf "$TMP"
}

# Build the action-download layout for one shim and run it with --help.
# Sets: $run_output (combined stdout+stderr), $run_status.
_run_dereffed() {
  local name="$1" pkg="$2"
  local root="$TMP/$name"
  mkdir -p "$root/bin" "$root/templates/commons/lib"

  # Real-file copy — cp dereferences, so this is NOT a symlink even though
  # bin/<name> is a symlink in the live repo. This is the action-download state.
  cp "$REPO_ROOT/templates/commons/bin/$name" "$root/bin/$name"
  [ ! -L "$root/bin/$name" ] # guard: must be a real file, not a symlink

  cp -R "$REPO_ROOT/templates/commons/lib/$pkg" "$root/templates/commons/lib/$pkg"

  # No top-level lib/ exists — the single-candidate ../lib/<pkg> would miss.
  [ ! -e "$root/lib" ]

  run "$root/bin/$name" --help
  run_output="$output"
  run_status="$status"
}

@test "changelog resolves its package when the bin symlink is dereferenced" {
  _run_dereffed changelog release_core
  [[ "$run_output" != *"ModuleNotFoundError"* ]]
  [[ "$run_output" != *"No module named"* ]]
}

@test "changelog-add resolves its package when the bin symlink is dereferenced" {
  _run_dereffed changelog-add release_core
  [[ "$run_output" != *"ModuleNotFoundError"* ]]
  [[ "$run_output" != *"No module named"* ]]
}

@test "changelog-cut resolves its package when the bin symlink is dereferenced" {
  _run_dereffed changelog-cut release_core
  [[ "$run_output" != *"ModuleNotFoundError"* ]]
  [[ "$run_output" != *"No module named"* ]]
}

@test "changelog-render resolves its package when the bin symlink is dereferenced" {
  _run_dereffed changelog-render release_core
  [[ "$run_output" != *"ModuleNotFoundError"* ]]
  [[ "$run_output" != *"No module named"* ]]
}
