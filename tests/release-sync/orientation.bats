#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Consumer CLAUDE.md orientation STUB (release#348 → WS2 release#523)
#
# `release-core init` injects a marker-delimited managed block at the TOP of the
# consumer's CLAUDE.md. WS2 made the block a tiny STUB pointing at the binary
# (`release-core how-to` is the single source of orientation) — it no longer
# imports @.release/ORIENTATION.md, and ORIENTATION.md is no longer composed. The
# block owns ONLY itself; the consumer's own content lives below it, untouched.
# ---------------------------------------------------------------------

BEGIN_MARK='<!-- BEGIN release-managed orientation'
END_MARK='<!-- END release-managed orientation -->'

@test "ORIENTATION.md is retired — not composed, not mirrored (WS2)" {
  run release_sync
  [ "$status" -eq 0 ]
  [ ! -e .release/ORIENTATION.md ]
  [ ! -e ORIENTATION.md ]
  [ ! -L ORIENTATION.md ]
}

@test "init creates CLAUDE.md with the stub block when absent" {
  [ ! -e CLAUDE.md ]
  release_sync >/dev/null
  [ -f CLAUDE.md ]
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  # The stub points at the binary, NOT an ORIENTATION import.
  grep -qF 'release-core how-to' CLAUDE.md
  ! grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  grep -qF "$END_MARK" CLAUDE.md
}

@test "init injects the block at the top, preserving the consumer's content" {
  printf '# My Project\n\nMy own instructions.\n' > CLAUDE.md
  release_sync >/dev/null
  # Block is first; the consumer's content survives below it.
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF '# My Project' CLAUDE.md
  grep -qF 'My own instructions.' CLAUDE.md
}

@test "the stub block is idempotent — a second init changes nothing" {
  release_sync >/dev/null
  cp CLAUDE.md CLAUDE.expected
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" != *"CLAUDE.md orientation"* ]]
  cmp -s CLAUDE.md CLAUDE.expected
}

@test "an OLD ORIENTATION-import block is refreshed in place to the stub (WS2 migration)" {
  # Seed a pre-WS2 consumer's block (imported @.release/ORIENTATION.md). The
  # markers are byte-identical, so init must STRIP it and refresh to the stub —
  # exactly one block, ORIENTATION import gone.
  printf '%s\n@.release/ORIENTATION.md\n%s\n\n# Proj\n\nmine\n' \
    "<!-- BEGIN release-managed orientation — managed by release-sync; do not edit -->" \
    "$END_MARK" > CLAUDE.md
  release_sync >/dev/null
  grep -qF 'release-core how-to' CLAUDE.md
  ! grep -qF '@.release/ORIENTATION.md' CLAUDE.md
  grep -qF '# Proj' CLAUDE.md
  # Exactly one managed block — refresh replaced, didn't append.
  [ "$(grep -cF "$BEGIN_MARK" CLAUDE.md)" -eq 1 ]
}

@test "the gh-release-issue synced shim is retired — not materialized (#476)" {
  # The escalation tool reaches a consumer's PATH via the pip console-script
  # (install-release-core at SessionStart), NOT a synced bin/ shim.
  release_sync >/dev/null
  [ ! -e bin/gh-release-issue ]
  [ ! -e .release/bin/gh-release-issue ]
}

@test "init re-injects the stub block after CLAUDE.md is removed" {
  release_sync >/dev/null
  run release_sync
  [ "$status" -eq 0 ]
  [[ "$output" == *"already current"* ]]
  rm -f CLAUDE.md
  run release_sync
  [ "$status" -eq 0 ]
  head -1 CLAUDE.md | grep -qF "$BEGIN_MARK"
  grep -qF 'release-core how-to' CLAUDE.md
}
