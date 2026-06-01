#!/usr/bin/env bats

# changelog-shim-contract — pins the shell→Python migration (Phase 1).
#
# The changelog-* family is now a set of thin Python shims over
# release_core.verbs.changelog (docs/proposals/shell-to-python.md). The four
# behavioral suites (changelog-add/cut/render/orchestrator.bats) run the SAME
# shims via $BIN, so they already prove the CLI contract end-to-end. This file
# adds the migration-specific invariants: the entry points are the variant-(a)
# Python shims (not bash), and a couple of byte-exact output snapshots that the
# whole fleet + CI depend on are frozen here.

load helper

@test "shims are python3, not bash" {
  for name in changelog changelog-add changelog-cut changelog-render; do
    run head -1 "$BIN/$name"
    [ "$status" -eq 0 ]
    [ "$output" = "#!/usr/bin/env python3" ]
  done
}

@test "shims dispatch into release_core.verbs.changelog" {
  for name in changelog changelog-add changelog-cut changelog-render; do
    run grep -q "from release_core.verbs import changelog" "$BIN/$name"
    [ "$status" -eq 0 ]
  done
}

@test "render of an empty CHANGELOG/ is byte-exact" {
  mkdir CHANGELOG
  "$BIN/changelog-render"
  expected=$'<!-- generated - do not edit. See CHANGELOG/README.txt -->\n\n# Changelog\n\n## Unreleased\n\n'
  [ "$(cat CHANGELOG.md)" = "$(printf '%s' "$expected")" ]
  # exact byte length (prelude + empty unreleased), no trailing turds
  [ "$(wc -c < CHANGELOG.md | tr -d ' ')" -eq 88 ]
}

@test "cut header + body bytes are exact (single fragment)" {
  mkdir -p CHANGELOG
  printf -- '- only\n' > CHANGELOG/unreleased-x.md
  "$BIN/changelog-cut" 1.2.3
  today="$(date -u +%Y-%m-%d)"
  expected="$(printf '## 1.2.3 - %s\n\n- only\n' "$today")"
  [ "$(cat CHANGELOG/1.2.3.md)" = "$expected" ]
}

@test "add inline body joins args with single space + one newline" {
  "$BIN/changelog-add" frag a b "c  d"
  # printf '%s\n' "$*" → "a b c  d\n"
  [ "$(cat CHANGELOG/unreleased-frag.md)" = "a b c  d" ]
  [ "$(wc -l < CHANGELOG/unreleased-frag.md | tr -d ' ')" -eq 1 ]
}
