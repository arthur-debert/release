#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Broken-symlink sweep must not drop a link whose target is installed
# THIS sync (regression: nvim/tree-sitter-lex/supage shipped with
# bin/check-shell + bin/gh-release-issue dropped during the #348 fleet
# propagation).
#
# The trigger: a consumer committed a bin/ symlink (e.g. bin/check-shell)
# but never committed its .release/ target — a dangling link. `release-core init`
# installs the target this run, but the sweep checked only the OLD
# .release/, saw a dangling link, and removed it; the create loop didn't
# re-add it (the link already pointed correctly). Net: the tool vanished.
# ---------------------------------------------------------------------

@test "a dangling link whose target is installed this sync survives + resolves" {
  # The botched-prior-sync state: committed symlink, target absent.
  mkdir -p bin
  ln -s ../.release/bin/check-shell bin/check-shell
  [ ! -e bin/check-shell ]   # dangling before sync

  run release_sync
  [ "$status" -eq 0 ]

  # check-shell is a commons tool, so this sync installs the target —
  # the link must be kept and now resolve, not swept.
  [ -e bin/check-shell ]
  [ "$(readlink bin/check-shell)" = "../.release/bin/check-shell" ]
}

@test "a genuinely stale link (target absent from the new tree too) is still removed" {
  mkdir -p bin
  ln -s ../.release/bin/does-not-exist bin/stale-tool
  run release_sync
  [ "$status" -eq 0 ]
  [ ! -L bin/stale-tool ]   # nothing installs it → correctly swept
}

# ---------------------------------------------------------------------
# #476 (lex dogfood): a symlink whose target is REMOVED this sync —
# present in the still-live OLD .release/, absent from the NEW tree —
# must be swept. The old sweep required the link to be already-broken in
# the live tree, but the .release/ swap happens AFTER the sweep, so a link
# that still resolves now (its old target is present) yet points at a
# now-retired tool was left dangling afterward — the 7 committed dangling
# bin/ links lex was left with.
# ---------------------------------------------------------------------
@test "a link to a tool retired this sync is swept (not left dangling)" {
  # Pre-seed a live .release/ carrying a tool the source no longer ships,
  # and a committed bin/ symlink to it — so the link RESOLVES right now.
  mkdir -p .release/bin bin
  printf '#!/bin/sh\n' > .release/bin/retired-tool
  ln -s ../.release/bin/retired-tool bin/retired-tool
  [ -e bin/retired-tool ]   # NOT broken-live (the old false precondition)

  run release_sync
  [ "$status" -eq 0 ]

  # retired-tool is not a managed tool → absent from the new `.release/` temp dir → the
  # link must be gone entirely, not a dangling symlink.
  [ ! -L bin/retired-tool ]
  [ ! -e bin/retired-tool ]
}
