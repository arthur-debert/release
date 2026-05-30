#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Consumer CLAUDE.md orientation block (release#348)
#
# release-sync injects a marker-delimited managed block at the TOP of the
# consumer's CLAUDE.md — a one-line `@.release/ORIENTATION.md` import of the
# "Welcome — managed by release" note. It owns ONLY the block; the consumer's
# own content lives below it, untouched. The file is created if absent.
# ---------------------------------------------------------------------

BEGIN_MARK='<!-- BEGIN release-managed orientation'
END_MARK='<!-- END release-managed orientation -->'

@test "ORIENTATION.md materializes into .release/ but is NOT mirrored to root" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ -f .release/ORIENTATION.md ]
  # release-internal: no ./ORIENTATION.md file or symlink scattered out.
  [ ! -e ORIENTATION.md ]
  [ ! -L ORIENTATION.md ]
}

@test "sync creates CLAUDE.md with the orientation block when absent" {
  [ ! -e CLAUDE.md ]
  "$BIN/release-sync" >/dev/null
  [ -f CLAUDE.md ]
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  grep -qF "$END_MARK" CLAUDE.md
}

@test "sync injects the block at the top, preserving the consumer's content" {
  printf '# My Project\n\nMy own instructions.\n' > CLAUDE.md
  "$BIN/release-sync" >/dev/null
  # Block is first; the consumer's content survives below it.
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF '# My Project' CLAUDE.md
  grep -qF 'My own instructions.' CLAUDE.md
}

@test "the orientation block is idempotent — a second sync changes nothing" {
  "$BIN/release-sync" >/dev/null
  cp CLAUDE.md CLAUDE.expected
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  # The second run must not report a CLAUDE.md action, and the file is identical.
  [[ "$output" != *"CLAUDE.md orientation"* ]]
  cmp -s CLAUDE.md CLAUDE.expected
}

@test "a stale block is refreshed in place (not duplicated)" {
  "$BIN/release-sync" >/dev/null
  # Corrupt the managed import line, as if an old block drifted.
  sed -i.bak 's#@.release/ORIENTATION.md#@.release/STALE.md#' CLAUDE.md && rm -f CLAUDE.md.bak
  "$BIN/release-sync" >/dev/null
  grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  ! grep -qF '@.release/STALE.md' CLAUDE.md
  # Exactly one managed block — refresh replaced, didn't append.
  [ "$(grep -cF "$BEGIN_MARK" CLAUDE.md)" -eq 1 ]
}

@test "the escalation tool gh-release-issue ships to consumers (#348)" {
  # ORIENTATION.md's escalation contract tells agents to run gh-release-issue,
  # so it must actually be on a consumer's PATH after sync (not maintainer-only).
  "$BIN/release-sync" >/dev/null
  [ -L bin/gh-release-issue ]
  [ -f .release/bin/gh-release-issue ]
  run ./bin/gh-release-issue --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"File a bug against arthur-debert/release"* ]]
}

@test "--check reports drift when the orientation block is missing" {
  # Full sync first so the ONLY drift we introduce is the CLAUDE.md block.
  "$BIN/release-sync" >/dev/null
  run "$BIN/release-sync" --check
  [ "$status" -eq 0 ]
  # Remove the managed block → --check must see it as non-clean.
  rm -f CLAUDE.md
  run "$BIN/release-sync" --check
  [ "$status" -eq 1 ]
}
