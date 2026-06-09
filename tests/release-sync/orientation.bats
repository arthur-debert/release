#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Consumer CLAUDE.md orientation block (release#348)
#
# `release-core init` injects a marker-delimited managed block at the TOP of the
# consumer's CLAUDE.md — a one-line `@.release/ORIENTATION.md` import of the
# "Welcome — managed by release" note. It owns ONLY the block; the consumer's
# own content lives below it, untouched. The file is created if absent.
# ---------------------------------------------------------------------

BEGIN_MARK='<!-- BEGIN release-managed orientation'
END_MARK='<!-- END release-managed orientation -->'

@test "ORIENTATION.md materializes into .release/ but is NOT mirrored to root" {
  run release_sync
  [ "$status" -eq 0 ]
  [ -f .release/ORIENTATION.md ]
  # release-internal: no ./ORIENTATION.md file or symlink scattered out.
  [ ! -e ORIENTATION.md ]
  [ ! -L ORIENTATION.md ]
}

@test "sync creates CLAUDE.md with the orientation block when absent" {
  [ ! -e CLAUDE.md ]
  release_sync >/dev/null
  [ -f CLAUDE.md ]
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  grep -qF "$END_MARK" CLAUDE.md
}

@test "sync injects the block at the top, preserving the consumer's content" {
  printf '# My Project\n\nMy own instructions.\n' > CLAUDE.md
  release_sync >/dev/null
  # Block is first; the consumer's content survives below it.
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF '# My Project' CLAUDE.md
  grep -qF 'My own instructions.' CLAUDE.md
}

@test "the orientation block is idempotent — a second sync changes nothing" {
  release_sync >/dev/null
  cp CLAUDE.md CLAUDE.expected
  run release_sync
  [ "$status" -eq 0 ]
  # The second run must not report a CLAUDE.md action, and the file is identical.
  [[ "$output" != *"CLAUDE.md orientation"* ]]
  cmp -s CLAUDE.md CLAUDE.expected
}

@test "a stale block is refreshed in place (not duplicated)" {
  release_sync >/dev/null
  # Corrupt the managed import line, as if an old block drifted.
  sed -i.bak 's#@.release/ORIENTATION.md#@.release/STALE.md#' CLAUDE.md && rm -f CLAUDE.md.bak
  release_sync >/dev/null
  grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  ! grep -qF '@.release/STALE.md' CLAUDE.md
  # Exactly one managed block — refresh replaced, didn't append.
  [ "$(grep -cF "$BEGIN_MARK" CLAUDE.md)" -eq 1 ]
}

@test "the gh-release-issue synced shim is retired — not materialized (#476)" {
  # The escalation tool reaches a consumer's PATH via the pip console-script
  # (install-release-core at SessionStart), NOT a synced bin/ shim. ORIENTATION's
  # escalation contract uses the `release-core issue file` CLI; the redundant
  # synced shim was retired in #476 so .release/lib/release_core can later be
  # stripped without leaving a dangling sys.path shim.
  release_sync >/dev/null
  [ ! -e bin/gh-release-issue ]
  [ ! -e .release/bin/gh-release-issue ]
  # ORIENTATION points agents at the console-script escalation CLI.
  grep -qF 'release-core issue file' .release/ORIENTATION.md
}

@test "init re-injects the orientation block after it is removed" {
  # WS4 (release#521) retired the --check drift mode; reconciliation is now just
  # idempotent re-init. A clean repo re-inits with no changes; deleting the
  # managed CLAUDE.md makes the next init re-create the orientation block.
  release_sync >/dev/null
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" == *"already current"* ]]
  rm -f CLAUDE.md
  run release_sync
  [ "$status" -eq 0 ]
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
}
