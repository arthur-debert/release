#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# gh-task-status PR state engine distribution (release#348)
#
# The shim + its release_gh package live under templates/commons, so a
# consumer gets them on sync. The package materializes into .release/lib/
# (the shim imports it) but is release-internal — NOT mirrored as symlinks
# into the consumer tree.
# ---------------------------------------------------------------------

@test "sync materializes gh-task-status + the release_gh package into .release/" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ -f .release/bin/gh-task-status ]
  [ -f .release/lib/release_gh/release_gh/state.py ]
  [ -f .release/lib/release_gh/release_gh/cli/task_status.py ]
}

@test "bin/gh-task-status is mirrored out as a symlink into .release/" {
  "$BIN/release-sync" >/dev/null
  [ -L bin/gh-task-status ]
  [ "$(readlink bin/gh-task-status)" = "../.release/bin/gh-task-status" ]
}

@test "the release_gh package is release-internal — no lib/ symlinks leak out" {
  "$BIN/release-sync" >/dev/null
  # Nothing under the consumer tree (outside .release/) links to the package.
  run bash -c 'find . -path ./.release -prune -o -type l -lname "*release_gh*" -print'
  [ -z "$output" ]
  # And no lib/release_gh dir exists outside .release/.
  [ ! -e lib/release_gh ]
}

@test "the synced gh-task-status runs — the shim resolves its package" {
  command -v python3 >/dev/null || skip "python3 not available"
  "$BIN/release-sync" >/dev/null
  run ./bin/gh-task-status --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"where does this PR stand"* ]]
}
