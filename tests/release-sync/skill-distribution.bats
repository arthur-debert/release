#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Infra/dev-cycle skill distribution (release#348)
#
# release/ is the single source of truth for infra + general-dev skills.
# release-sync distributes the OFFICIAL set (PUSH_ALL_SKILLS) into every
# consumer at .claude/skills/ — the path Claude Code auto-discovers project
# skills from — sourced DIRECTLY from skills/, whole-directory, so there is one
# copy and no drift. A second, upgrade-only set (REPLACE_IF_PRESENT_SKILLS) is
# synced only when the consumer already carries the skill. A stale REAL local
# copy at a managed dest is replaced by the managed symlink. .claude/skills/**
# is excluded from the consumer markdownlint gate.
# ---------------------------------------------------------------------

@test "sync materializes the PR-loop skill into .release/.claude/skills/" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ -f .release/.claude/skills/gh-pr-review-loop/SKILL.md ]
}

@test "the skill is mirrored out as a symlink Claude Code can discover" {
  "$BIN/release-sync" >/dev/null
  [ -L .claude/skills/gh-pr-review-loop/SKILL.md ]
  [ "$(readlink .claude/skills/gh-pr-review-loop/SKILL.md)" = \
    "../../../.release/.claude/skills/gh-pr-review-loop/SKILL.md" ]
  # The symlink resolves to real content (dereferenceable → discoverable).
  [ -f .claude/skills/gh-pr-review-loop/SKILL.md ]
}

@test "the synced skill is consumer-facing (leads with release-core pr status, no auto-merge)" {
  "$BIN/release-sync" >/dev/null
  desc=$(sed -n '/^description:/p' .claude/skills/gh-pr-review-loop/SKILL.md)
  [[ "$desc" == *"release-core pr status"* ]]
  [[ "$desc" == *"ready-for-human-merge"* ]]
}

@test "sync distributes the full official PUSH_ALL set as discoverable symlinks" {
  "$BIN/release-sync" >/dev/null
  for s in gh-pr-review-loop pr-review-respond release-issue-relay diagnose \
           tdd review triage to-issues handoff qa grill-me grill-with-docs \
           improve-codebase-architecture request-refactor-plan \
           ubiquitous-language zoom-out teach padz-for-agents; do
    [ -f ".release/.claude/skills/$s/SKILL.md" ] \
      || { echo "missing materialized $s"; false; }
    [ -L ".claude/skills/$s/SKILL.md" ] \
      || { echo "missing symlink $s"; false; }
  done
}

@test "a multi-file skill (tdd) distributes ALL its files, not just SKILL.md" {
  "$BIN/release-sync" >/dev/null
  for f in SKILL.md mocking.md tests.md refactoring.md; do
    [ -f ".release/.claude/skills/tdd/$f" ] || { echo "missing tdd/$f"; false; }
    [ -L ".claude/skills/tdd/$f" ]          || { echo "no symlink tdd/$f"; false; }
  done
}

@test "release-only skills are NEVER distributed" {
  "$BIN/release-sync" >/dev/null
  for s in release-fleet-ops release-fleet-triage gh-repo-setup \
           migrate-consumer-to-build-dir setup-matt-pocock-skills; do
    [ ! -e ".release/.claude/skills/$s" ] || { echo "leaked $s"; false; }
    [ ! -e ".claude/skills/$s" ]          || { echo "leaked symlink $s"; false; }
  done
}

@test "REPLACE_IF_PRESENT skill is synced only when the consumer already has it" {
  # Not present → not distributed.
  "$BIN/release-sync" >/dev/null
  [ ! -e .claude/skills/lex-primer/SKILL.md ]

  # Pre-seed a (stale) real lex-primer, then re-sync → it gets upgraded to a symlink.
  mkdir -p .claude/skills/lex-primer
  printf '# stale lex-primer\n' > .claude/skills/lex-primer/SKILL.md
  "$BIN/release-sync" >/dev/null
  [ -L .claude/skills/lex-primer/SKILL.md ]
  [ -f .release/.claude/skills/lex-primer/SKILL.md ]
}

@test "a stale REAL skill copy is replaced by the managed symlink (lex regression)" {
  # Simulate lex's hand-copied real pr-review-respond/SKILL.md.
  mkdir -p .claude/skills/pr-review-respond
  printf '# stale 157-line hand-copy\n' > .claude/skills/pr-review-respond/SKILL.md
  [ ! -L .claude/skills/pr-review-respond/SKILL.md ]  # real file now

  "$BIN/release-sync" >/dev/null
  # After sync it is a symlink into .release/ (the official copy), no --migrate.
  [ -L .claude/skills/pr-review-respond/SKILL.md ]
  [ "$(readlink .claude/skills/pr-review-respond/SKILL.md)" = \
    "../../../.release/.claude/skills/pr-review-respond/SKILL.md" ]
  # The stale text is gone (no line contains it) AND the resolved content is
  # release's official copy (its frontmatter name) — proving a real swap, not a
  # weak "some line differs" check.
  ! grep -qF 'stale 157-line hand-copy' .claude/skills/pr-review-respond/SKILL.md
  grep -qF 'name: pr-review-respond' .claude/skills/pr-review-respond/SKILL.md
}
