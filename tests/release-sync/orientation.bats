#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Consumer CLAUDE.md @import (release#348 → WS2 release#523 → WS4 release#761)
#
# `release-core init` no longer SPLICES a marker-delimited block into the
# consumer's CLAUDE.md. Instead it emits a managed TARGET file
# (`.claude/IMPORTANT-RELEASE.md`) carrying the orientation body, and ensures
# CLAUDE.md @imports it with a single line `@.claude/IMPORTANT-RELEASE.md`.
# `release-core how-to` remains the single source of orientation; the target
# just points at it. After insertion CLAUDE.md is 100% consumer-owned. The
# pre-WS4 spliced BEGIN..END block, if present, is stripped and replaced by the
# one-line @import on the next init.
# ---------------------------------------------------------------------

BEGIN_MARK='<!-- BEGIN release-managed orientation'
IMPORT_LINE='@.claude/IMPORTANT-RELEASE.md'
TARGET='.claude/IMPORTANT-RELEASE.md'

@test "ORIENTATION.md is retired — not composed, not mirrored (WS2)" {
  run release_sync
  [ "$status" -eq 0 ]
  [ ! -e .release/ORIENTATION.md ]
  [ ! -e ORIENTATION.md ]
  [ ! -L ORIENTATION.md ]
}

@test "init creates CLAUDE.md as the one-line @import + the managed target when absent" {
  [ ! -e CLAUDE.md ]
  release_sync >/dev/null
  # CLAUDE.md is exactly the one-line pointer.
  [ -f CLAUDE.md ]
  grep -qF "$IMPORT_LINE" CLAUDE.md
  ! grep -qF "$BEGIN_MARK" CLAUDE.md
  # The orientation body lives in the managed target, not in CLAUDE.md.
  [ -f "$TARGET" ]
  grep -qF 'release-core how-to' "$TARGET"
  ! grep -qF 'release-core how-to' CLAUDE.md
  ! grep -qF '@.release/ORIENTATION.md' "$TARGET"
}

@test "init inserts the @import line at the top, preserving the consumer's content" {
  printf '# My Project\n\nMy own instructions.\n' > CLAUDE.md
  release_sync >/dev/null
  # The @import line is first; the consumer's content survives below it.
  head -1 CLAUDE.md | grep -qF "$IMPORT_LINE"
  grep -qF '# My Project' CLAUDE.md
  grep -qF 'My own instructions.' CLAUDE.md
  [ -f "$TARGET" ]
}

@test "the @import is idempotent — a second init changes nothing" {
  release_sync >/dev/null
  cp CLAUDE.md CLAUDE.expected
  cp "$TARGET" TARGET.expected
  run release_sync
  [ "$status" -eq 0 ]
  cmp -s CLAUDE.md CLAUDE.expected
  cmp -s "$TARGET" TARGET.expected
}

@test "a pre-WS4 spliced ORIENTATION-import block is migrated in place to the @import (WS4)" {
  # Seed a pre-WS4 consumer's spliced block (imported @.release/ORIENTATION.md).
  # init must STRIP the whole BEGIN..END block, prepend the one-line @import, write
  # the target, and preserve the consumer's own content — exactly one @import line,
  # no managed block, ORIENTATION import gone.
  printf '%s\n@.release/ORIENTATION.md\n%s\n\n# Proj\n\nmine\n' \
    "<!-- BEGIN release-managed orientation — managed by release-sync; do not edit -->" \
    "<!-- END release-managed orientation -->" > CLAUDE.md
  release_sync >/dev/null
  head -1 CLAUDE.md | grep -qF "$IMPORT_LINE"
  ! grep -qF "$BEGIN_MARK" CLAUDE.md
  ! grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  grep -qF '# Proj' CLAUDE.md
  grep -qF 'mine' CLAUDE.md
  # Exactly one @import line — migration replaced, didn't append a second.
  [ "$(grep -cF "$IMPORT_LINE" CLAUDE.md)" -eq 1 ]
  # The body moved to the managed target.
  [ -f "$TARGET" ]
  grep -qF 'release-core how-to' "$TARGET"
}

@test "the gh-release-issue synced retired file is gone — not installed (#476)" {
  # The escalation tool reaches a consumer's PATH via the pip console-script
  # (install-release-core at SessionStart), NOT a synced bin/ file.
  release_sync >/dev/null
  [ ! -e bin/gh-release-issue ]
  [ ! -e .release/bin/gh-release-issue ]
}

@test "init re-creates the @import + target after CLAUDE.md is removed" {
  release_sync >/dev/null
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" == *"already current"* ]]
  rm -f CLAUDE.md
  run release_sync
  [ "$status" -eq 0 ]
  head -1 CLAUDE.md | grep -qF "$IMPORT_LINE"
  [ -f "$TARGET" ]
}
