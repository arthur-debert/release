#!/usr/bin/env bats

# changelog-byte-contract — freezes byte-exact output snapshots of the
# changelog console-scripts.
#
# The changelog-* family ships as release_core console-scripts (the bash
# bin/ shims were retired in release#476). The four behavioral suites
# (changelog-add/cut/render/orchestrator.bats) run those console-scripts
# via $BIN and prove the CLI contract end-to-end; this file freezes a
# couple of byte-exact output snapshots that the whole fleet + CI depend
# on.

load helper

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
