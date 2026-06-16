#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Infra skill distribution (release#348 → trimmed in WS2 release#523, then to
# ONE in WS7 release#528)
#
# WS7 cut PUSH_ALL_SKILLS to the ONE skill the harness needs a file on disk for:
# gh-pr-review-loop (the `/`-triggered PR-loop driver). release-issue-relay was
# dropped — the escalation contract is binary-carried (CLAUDE.md stub +
# `release-core how-to` + the `release-core issue file` console path). The dev
# cycle + general-dev guidance likewise lives in `release-core how-to`, not
# synced skill files. `release-core init` distributes the kept set
# into .claude/skills/ (the path Claude Code auto-discovers from), sourced from
# skills/, whole-directory. A second upgrade-only set (REPLACE_IF_PRESENT_SKILLS)
# syncs only when the consumer already carries the skill. A stale REAL local copy
# at a managed dest is replaced by the managed symlink; a previously-distributed
# skill that's been dropped has its now-dangling symlink swept on re-sync.
# ---------------------------------------------------------------------

@test "sync installs the PR-loop skill into .release/.claude/skills/" {
  run release_sync
  [ "$status" -eq 0 ]
  [ -f .release/.claude/skills/gh-pr-review-loop/SKILL.md ]
}

@test "the skill is mirrored out as a symlink Claude Code can discover" {
  release_sync >/dev/null
  [ -L .claude/skills/gh-pr-review-loop/SKILL.md ]
  [ "$(readlink .claude/skills/gh-pr-review-loop/SKILL.md)" = \
    "../../../.release/.claude/skills/gh-pr-review-loop/SKILL.md" ]
  # The symlink resolves to real content (dereferenceable → discoverable).
  [ -f .claude/skills/gh-pr-review-loop/SKILL.md ]
}

@test "the synced skill is consumer-facing (leads with release-core pr status, no auto-merge)" {
  release_sync >/dev/null
  desc=$(sed -n '/^description:/p' .claude/skills/gh-pr-review-loop/SKILL.md)
  [[ "$desc" == *"release-core pr status"* ]]
  [[ "$desc" == *"ready-for-human-merge"* ]]
}

@test "init distributes the trimmed PUSH_ALL set (gh-pr-review-loop only, WS7)" {
  release_sync >/dev/null
  for s in gh-pr-review-loop; do
    [ -f ".release/.claude/skills/$s/SKILL.md" ] \
      || { echo "missing installed $s"; false; }
    [ -L ".claude/skills/$s/SKILL.md" ] \
      || { echo "missing symlink $s"; false; }
  done
}

@test "the dev-cycle + pr-review-respond + relay skills are NO LONGER distributed (WS2/WS7)" {
  release_sync >/dev/null
  # WS2 dropped the dev-cycle set, WS7 dropped release-issue-relay — the dev
  # cycle + escalation contract live in `release-core how-to`.
  for s in pr-review-respond release-issue-relay diagnose tdd review triage to-issues handoff qa \
           grill-me grill-with-docs improve-codebase-architecture \
           request-refactor-plan ubiquitous-language zoom-out teach padz-for-agents; do
    [ ! -e ".release/.claude/skills/$s" ] || { echo "leaked installed $s"; false; }
    [ ! -e ".claude/skills/$s" ]          || { echo "leaked symlink $s"; false; }
  done
}

@test "a dropped skill's previously-committed symlink is swept on re-sync (auto de-vendor)" {
  # Simulate a pre-WS2 consumer that carried a now-dropped skill as a managed
  # symlink into .release/. On the next init the target is no longer composed, so
  # the dangling managed symlink must be swept (WS4 broken-symlink cleanup).
  release_sync >/dev/null
  mkdir -p .claude/skills/tdd
  ln -s ../../../.release/.claude/skills/tdd/SKILL.md .claude/skills/tdd/SKILL.md
  release_sync >/dev/null
  [ ! -e .claude/skills/tdd/SKILL.md ]
  [ ! -L .claude/skills/tdd/SKILL.md ]
}

@test "release-only skills are NEVER distributed" {
  release_sync >/dev/null
  for s in release-fleet-ops release-fleet-triage gh-repo-setup \
           setup-matt-pocock-skills; do
    [ ! -e ".release/.claude/skills/$s" ] || { echo "leaked installed $s"; false; }
    [ ! -e ".claude/skills/$s" ]          || { echo "leaked symlink $s"; false; }
  done
}

@test "REPLACE_IF_PRESENT skill is synced only when the consumer already has it" {
  # Not present → not distributed.
  release_sync >/dev/null
  [ ! -e .claude/skills/lex-primer/SKILL.md ]

  # Pre-seed a (stale) real lex-primer, then re-sync → it gets upgraded to a symlink.
  mkdir -p .claude/skills/lex-primer
  printf '# stale lex-primer\n' > .claude/skills/lex-primer/SKILL.md
  release_sync >/dev/null
  [ -L .claude/skills/lex-primer/SKILL.md ]
  [ -f .release/.claude/skills/lex-primer/SKILL.md ]
}

@test "a stale REAL skill copy is replaced by the managed symlink (lex regression)" {
  # Simulate a hand-copied real gh-pr-review-loop/SKILL.md (a KEPT skill).
  mkdir -p .claude/skills/gh-pr-review-loop
  printf '# stale 157-line hand-copy\n' > .claude/skills/gh-pr-review-loop/SKILL.md
  [ ! -L .claude/skills/gh-pr-review-loop/SKILL.md ]  # real file now

  release_sync >/dev/null
  # After sync it is a symlink into .release/ (the official copy), no --migrate.
  [ -L .claude/skills/gh-pr-review-loop/SKILL.md ]
  [ "$(readlink .claude/skills/gh-pr-review-loop/SKILL.md)" = \
    "../../../.release/.claude/skills/gh-pr-review-loop/SKILL.md" ]
  # The stale text is gone AND the resolved content is release's official copy
  # (its frontmatter name) — proving a real swap, not a weak "some line differs".
  ! grep -qF 'stale 157-line hand-copy' .claude/skills/gh-pr-review-loop/SKILL.md
  grep -qF 'name: gh-pr-review-loop' .claude/skills/gh-pr-review-loop/SKILL.md
}
