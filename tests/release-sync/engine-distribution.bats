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
  # And no lib/release_gh exists outside .release/ (a broken symlink would
  # pass -e but not -L, so check both).
  [ ! -e lib/release_gh ] && [ ! -L lib/release_gh ]
}

@test "is_release_internal is scoped to lib/release_gh — other lib/ files still mirror" {
  # The bats Capability ships a consumer-facing lib/bats-harness.bash; shielding
  # all of lib/ would wrongly stop it mirroring. Only lib/release_gh/ is internal.
  printf 'capabilities:\n  - bats\n' > .release-sync.yaml
  "$BIN/release-sync" >/dev/null
  [ -L lib/bats-harness.bash ]                       # consumer-facing lib/ mirrors
  [ ! -e lib/release_gh ] && [ ! -L lib/release_gh ]  # engine package stays internal
  [ -f .release/lib/release_gh/release_gh/state.py ]
}

@test "the commons shellcheck gate excludes the gh-task-status shim (#348)" {
  # The shim is an extensionless Python symlink under bin/. On a consumer
  # commit it's staged and shellcheck would SC1071 on it, breaking the gate,
  # unless the commons shellcheck step excludes it path-anchored. A leading-*
  # glob can't cross '/', so the exclude must be **/ -anchored.
  "$BIN/release-sync" >/dev/null
  grep -q '\*\*/gh-task-status' .release/lefthook.yml
}

@test "the synced gh-task-status runs — the shim resolves its package" {
  command -v python3 >/dev/null || skip "python3 not available"
  "$BIN/release-sync" >/dev/null
  run ./bin/gh-task-status --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"where does this PR stand"* ]]
}
