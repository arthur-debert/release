#!/usr/bin/env bats

load helper

@test "fails if CHANGELOG/ directory is absent" {
  run "$BIN/changelog-render"
  [ "$status" -ne 0 ]
  [[ "$output" == *"CHANGELOG/ directory not found"* ]]
}

@test "empty CHANGELOG/ produces prelude + empty Unreleased only" {
  mkdir CHANGELOG
  run "$BIN/changelog-render"
  [ "$status" -eq 0 ]
  [ -f CHANGELOG.md ]
  run grep -c '^## ' CHANGELOG.md
  [ "$output" = "1" ]   # only the "## Unreleased" header
}

@test "prelude is a single comment + # Changelog heading" {
  mkdir CHANGELOG
  "$BIN/changelog-render"
  # awk -- print specific line numbers so we don't depend on bats' lines[]
  # behavior (older versions collapse blank lines in the array).
  [ "$(awk 'NR==1' CHANGELOG.md)" = "<!-- generated - do not edit. See CHANGELOG/README.txt -->" ]
  [ "$(awk 'NR==2' CHANGELOG.md)" = "" ]
  [ "$(awk 'NR==3' CHANGELOG.md)" = "# Changelog" ]
}

@test "unreleased fragments populate the Unreleased section" {
  "$BIN/changelog-add" a "- a-bullet"
  "$BIN/changelog-add" b "- b-bullet"
  "$BIN/changelog-render"
  run cat CHANGELOG.md
  [[ "$output" == *"## Unreleased"*"- a-bullet"*"- b-bullet"* ]]
}

@test "version sections appear in descending semver order" {
  mkdir CHANGELOG
  printf '## 1.0.0 - 2026-06-01\n\n- v100\n' > CHANGELOG/1.0.0.md
  printf '## 0.4.2 - 2026-04-20\n\n- v042\n' > CHANGELOG/0.4.2.md
  printf '## 0.5.0 - 2026-05-20\n\n- v050\n' > CHANGELOG/0.5.0.md
  "$BIN/changelog-render"
  run grep -E '^## [0-9]' CHANGELOG.md
  [ "${lines[0]}" = "## 1.0.0 - 2026-06-01" ]
  [ "${lines[1]}" = "## 0.5.0 - 2026-05-20" ]
  [ "${lines[2]}" = "## 0.4.2 - 2026-04-20" ]
}

@test "bare release sorts ABOVE its pre-releases (semver §11, descending)" {
  mkdir CHANGELOG
  printf '## 0.5.0 - 2026-05-20\n\n- ga\n' > CHANGELOG/0.5.0.md
  printf '## 0.5.0-rc.1 - 2026-05-15\n\n- rc1\n' > CHANGELOG/0.5.0-rc.1.md
  printf '## 0.5.0-alpha.10 - 2026-05-10\n\n- a10\n' > CHANGELOG/0.5.0-alpha.10.md
  printf '## 0.5.0-alpha.2 - 2026-05-05\n\n- a2\n' > CHANGELOG/0.5.0-alpha.2.md
  printf '## 0.5.0-alpha.1 - 2026-05-01\n\n- a1\n' > CHANGELOG/0.5.0-alpha.1.md
  "$BIN/changelog-render"
  run grep -E '^## [0-9]' CHANGELOG.md
  [ "${lines[0]}" = "## 0.5.0 - 2026-05-20" ]
  [ "${lines[1]}" = "## 0.5.0-rc.1 - 2026-05-15" ]
  [ "${lines[2]}" = "## 0.5.0-alpha.10 - 2026-05-10" ]
  [ "${lines[3]}" = "## 0.5.0-alpha.2 - 2026-05-05" ]
  [ "${lines[4]}" = "## 0.5.0-alpha.1 - 2026-05-01" ]
}

@test "numeric pre-release components sort numerically (alpha.10 > alpha.2)" {
  mkdir CHANGELOG
  printf '## 1.0.0-alpha.10\n\n- a10\n' > CHANGELOG/1.0.0-alpha.10.md
  printf '## 1.0.0-alpha.2\n\n- a2\n' > CHANGELOG/1.0.0-alpha.2.md
  "$BIN/changelog-render"
  run grep -E '^## [0-9]' CHANGELOG.md
  [ "${lines[0]}" = "## 1.0.0-alpha.10" ]
  [ "${lines[1]}" = "## 1.0.0-alpha.2" ]
}

@test "legacy.md is appended verbatim at the very end" {
  mkdir CHANGELOG
  printf '## 1.0.0 - 2026-06-01\n\n- v100\n' > CHANGELOG/1.0.0.md
  printf 'old changelog stuff\n\n## 0.0.1 (legacy era)\n- ancient fix\n' > CHANGELOG/legacy.md
  "$BIN/changelog-render"
  # Last 4 lines of the rendered file must be the legacy file verbatim.
  # awk indexing avoids bats lines[]'s blank-collapse footgun.
  # BSD wc -l emits leading whitespace; force arithmetic to normalise.
  total=$(($(wc -l < CHANGELOG.md)))
  [ "$(awk -v n=$((total - 3)) 'NR==n' CHANGELOG.md)" = "old changelog stuff" ]
  [ "$(awk -v n=$((total - 2)) 'NR==n' CHANGELOG.md)" = "" ]
  [ "$(awk -v n=$((total - 1)) 'NR==n' CHANGELOG.md)" = "## 0.0.1 (legacy era)" ]
  [ "$(awk -v n=$((total))     'NR==n' CHANGELOG.md)" = "- ancient fix" ]
}

@test "render is idempotent" {
  "$BIN/changelog-add" 1 "- bullet"
  mkdir -p CHANGELOG
  printf '## 1.0.0\n\n- v100\n' > CHANGELOG/1.0.0.md
  "$BIN/changelog-render"
  # Keep the snapshot inside the per-test sandbox; teardown cleans it.
  cp CHANGELOG.md "$TMPDIR_TEST/render_first.md"
  "$BIN/changelog-render"
  run diff "$TMPDIR_TEST/render_first.md" CHANGELOG.md
  [ "$status" -eq 0 ]
}

@test "unparseable version filename fails loud" {
  mkdir CHANGELOG
  touch CHANGELOG/not-a-version.md
  run "$BIN/changelog-render"
  [ "$status" -ne 0 ]
  [[ "$output" == *"unparseable version filename"* ]]
  [[ "$output" == *"not-a-version.md"* ]]
  # And CHANGELOG.md must NOT have been written (or at least not overwritten
  # with garbage). The mktemp+mv atomicity in render leaves no .tmp turds.
  run bash -c 'ls CHANGELOG.md.tmp.* 2>/dev/null | wc -l | tr -d " "'
  [ "$output" = "0" ]
}

@test "README.md and legacy.md are not treated as versions" {
  mkdir CHANGELOG
  printf 'README placeholder content\n' > CHANGELOG/README.md
  printf '## 1.0.0\n\n- v100\n' > CHANGELOG/1.0.0.md
  printf 'legacy content\n' > CHANGELOG/legacy.md
  run "$BIN/changelog-render"
  [ "$status" -eq 0 ]
  # README.md's content must NOT bleed into the rendered file.
  # (The prelude does reference CHANGELOG/README.txt, so grep for the
  # README *content* string, not the literal word "README".)
  run grep -c "README placeholder content" CHANGELOG.md
  [ "$output" = "0" ]
}

@test "unreleased section header always present, even with no fragments" {
  mkdir CHANGELOG
  printf '## 1.0.0\n\n- v100\n' > CHANGELOG/1.0.0.md
  "$BIN/changelog-render"
  run grep -c '^## Unreleased$' CHANGELOG.md
  [ "$output" = "1" ]
}
