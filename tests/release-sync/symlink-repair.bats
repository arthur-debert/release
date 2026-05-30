#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Broken-symlink sweep must not drop a link whose target is materialized
# THIS sync (regression: nvim/tree-sitter-lex/supage shipped with
# bin/check-shell + bin/gh-release-issue dropped during the #348 fleet
# propagation).
#
# The trigger: a consumer committed a bin/ symlink (e.g. bin/check-shell)
# but never committed its .release/ target — a dangling link. release-sync
# materializes the target this run, but the sweep checked only the OLD
# .release/, saw a dangling link, and removed it; the create loop didn't
# re-add it (the link already pointed correctly). Net: the tool vanished.
# ---------------------------------------------------------------------

@test "a dangling link whose target is materialized this sync survives + resolves" {
  # The botched-prior-sync state: committed symlink, target absent.
  mkdir -p bin
  ln -s ../.release/bin/check-shell bin/check-shell
  [ ! -e bin/check-shell ]   # dangling before sync

  run "$BIN/release-sync"
  [ "$status" -eq 0 ]

  # check-shell is a commons tool, so this sync materializes the target —
  # the link must be kept and now resolve, not swept.
  [ -e bin/check-shell ]
  [ "$(readlink bin/check-shell)" = "../.release/bin/check-shell" ]
}

@test "a genuinely stale link (target absent from the new tree too) is still removed" {
  mkdir -p bin
  ln -s ../.release/bin/does-not-exist bin/stale-tool
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ ! -L bin/stale-tool ]   # nothing materializes it → correctly swept
}
