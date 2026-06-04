#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# PR-loop skill distribution (release#348 A3 / #367)
#
# The canonical gh-pr-review-loop skill lives in skills/ (the single home,
# also installed to ~/.claude/skills/ by env/setup.sh). release-sync injects
# it into every consumer at .claude/skills/ — the path Claude Code
# auto-discovers project skills from — sourced DIRECTLY from skills/, so there
# is one copy and no drift. .claude/skills/** is excluded from the consumer
# markdownlint gate, so the synced SKILL.md doesn't fight it.
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
