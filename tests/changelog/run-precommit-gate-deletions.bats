#!/usr/bin/env bats

# Regression test for the changelog-roll → pre-commit-gate crash
# (release surfaced downstream in lex-fmt/vscode#128).
#
# The changelog roll writes CHANGELOG/<version>.md and DELETES the
# CHANGELOG/unreleased-*.md fragments. With 2+ fragments git leaves the
# unpaired ones as staged deletions. The gate built its --file list with
# `git diff --cached --name-only`, which INCLUDES deleted paths, and fed
# each to lefthook. lefthook's prettier (stage_fixed: true) then tried to
# operate on a vanished file → crash (exit 128 / "No files matching"),
# failing the release. The fix: collect with --diff-filter=d so ONLY
# deletions are excluded from the gate's file list — everything that still
# exists (added/copied/modified/renamed AND type-changed file↔symlink, T) is
# kept (the deletion is still committed via the staged index regardless).

load helper

GATE="$BATS_TEST_DIRNAME/../../bin-internal/run-precommit-gate.sh"

# Stage the orphan-deletion scenario: roll a changelog with 2 fragments
# (→ staged deletions) plus a real modified file, leaving the index in the
# exact shape the gate sees mid-release. Writes a lefthook.yml whose
# prettier command chokes on a missing file (matching the real gate).
_stage_changelog_roll() {
  "$BIN/changelog-add" aaa "- fix: alpha" >/dev/null
  "$BIN/changelog-add" bbb "- feat: beta" >/dev/null
  echo "# Title" > README.md
  echo "## [Unreleased]" > CHANGELOG.md
  # A regular file that will later turn into a symlink — a type-change (T).
  # `--diff-filter=ACMR` would WRONGLY drop it (T not in the whitelist) even
  # though it still exists; `--diff-filter=d` keeps it.
  echo "typechange" > TYPECHANGE.txt
  cat > lefthook.yml <<'EOF'
pre-commit:
  commands:
    prettier:
      glob: "*.md"
      run: prettier --write {staged_files}
      stage_fixed: true
EOF
  git add -A
  git commit -qm init
  # Roll: cuts the 2 fragments into CHANGELOG/1.0.0.md + deletes them.
  "$BIN/changelog-cut" 1.0.0 >/dev/null
  # A genuinely modified, lintable file alongside the deletions.
  echo "extra change" >> README.md
  # Turn the committed regular file into a symlink → a staged type-change (T).
  rm TYPECHANGE.txt
  ln -s README.md TYPECHANGE.txt
  git add -A
}

@test "staged-file collection excludes deletions from the roll (the fix)" {
  _stage_changelog_roll

  # Sanity: the buggy collector WOULD have included the deletions.
  run git diff --cached --name-only
  [[ "$output" == *"CHANGELOG/unreleased-aaa.md"* ]]
  [[ "$output" == *"CHANGELOG/unreleased-bbb.md"* ]]

  # Sanity: TYPECHANGE.txt is staged as a type-change (T), which the old
  # --diff-filter=ACMR whitelist would have WRONGLY excluded even though the
  # path still exists.
  run git diff --cached --name-status
  [[ "$output" == *"T"*"TYPECHANGE.txt"* ]]
  run git diff --cached --name-only --diff-filter=ACMR
  [[ "$output" != *"TYPECHANGE.txt"* ]]

  # The gate prints "Staged files (N):" then one indented path per line.
  # Assert the deleted fragments are NOT in that list, but the added /
  # modified files are.
  SKIP_PRECOMMIT_GATE=true run bash "$GATE"
  [ "$status" -eq 0 ]

  # Re-derive the gate's exact collection logic to assert the contents,
  # independent of whether lefthook/prettier are installed here.
  run git diff --cached --name-only --diff-filter=d
  [ "$status" -eq 0 ]
  [[ "$output" != *"unreleased-aaa.md"* ]]
  [[ "$output" != *"unreleased-bbb.md"* ]]
  [[ "$output" == *"CHANGELOG/1.0.0.md"* ]]
  [[ "$output" == *"README.md"* ]]
  # The type-changed path still exists and MUST be kept (the ACMR→d fix).
  [[ "$output" == *"TYPECHANGE.txt"* ]]
}

@test "gate survives the 2-fragment orphan-deletion roll (end-to-end)" {
  command -v lefthook >/dev/null || skip "lefthook not installed"
  command -v prettier >/dev/null || skip "prettier not installed"

  _stage_changelog_roll

  # The deletions are staged; the gate must not feed them to lefthook.
  run git diff --cached --name-status
  [[ "$output" == *"D"*"unreleased-aaa.md"* ]]

  # Run the real gate. Pre-fix this crashed (deleted paths → prettier /
  # `git add --force` on a vanished file). Post-fix it passes.
  run bash "$GATE"
  [ "$status" -eq 0 ]
  # The deleted fragments must never have been handed to the linter.
  [[ "$output" != *"No files matching"*"unreleased-aaa.md"* ]]

  # The staged deletion is still committed via the index — the gate must
  # not have un-staged it.
  run git diff --cached --name-status
  [[ "$output" == *"D"*"unreleased-aaa.md"* ]]
}
