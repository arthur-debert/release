#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# release-inbox: triage accessor over consumer-filed issues
# (release#348 Phase C foundation / #368). Fully offline — a stub `gh`
# returns canned issue JSON, so we assert clustering/recurrence/render
# without touching the network.
# ---------------------------------------------------------------------

# Drop a stub `gh` on PATH that returns the JSON in $1 for `gh issue list`.
_stub_gh() {
  mkdir -p stub
  cat > stub/gh <<EOF
#!/usr/bin/env bash
cat "$1"
EOF
  chmod +x stub/gh
  export PATH="$PWD/stub:$PATH"
}

# Three issues: two under copilot-review (3 + 0 comments → recurrence 5),
# one under rust-cli-release (1 comment → recurrence 2).
_three_issue_fixture() {
  cat > fix.json <<'JSON'
[
 {"number":42,"title":"[copilot-review] reviewer empty after success","body":"**Reported from:** arthur-debert/dodot\n","createdAt":"2026-05-25T00:00:00Z","url":"https://x/42","comments":[{},{},{}]},
 {"number":51,"title":"[copilot-review] not attached on fork PR","body":"**Reported from:** arthur-debert/clapfig\n","createdAt":"2026-05-29T00:00:00Z","url":"https://x/51","comments":[]},
 {"number":48,"title":"[rust-cli-release] cargo publish failed at v1.4.2","body":"Reported from: arthur-debert/padz","createdAt":"2026-05-28T00:00:00Z","url":"https://x/48","comments":[{}]}
]
JSON
}

@test "empty inbox renders a clear message and exits 0" {
  echo '[]' > fix.json
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox
  [ "$status" -eq 0 ]
  [[ "$output" == *"no open"* ]]
}

@test "--json on an empty inbox prints an empty array" {
  echo '[]' > fix.json
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox --json
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | jq 'length')" -eq 0 ]
}

@test "--json clusters by component, sorted by recurrence (comment count)" {
  _three_issue_fixture
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox --json
  [ "$status" -eq 0 ]
  # copilot-review (recurrence 5) sorts ahead of rust-cli-release (recurrence 2).
  [ "$(echo "$output" | jq -r '.[0].component')" = "copilot-review" ]
  [ "$(echo "$output" | jq -r '.[0].recurrence')" = "5" ]
  [ "$(echo "$output" | jq -r '.[0].issue_count')" = "2" ]
  [ "$(echo "$output" | jq -r '.[1].component')" = "rust-cli-release" ]
}

@test "--json sorts issues within a cluster by comment count (recurrence) first" {
  _three_issue_fixture
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox --json
  # #42 (3 comments) before #51 (0 comments) within copilot-review.
  [ "$(echo "$output" | jq -r '.[0].issues[0].number')" = "42" ]
  [ "$(echo "$output" | jq -r '.[0].issues[1].number')" = "51" ]
}

@test "human digest shows source repo, comment count, and url" {
  _three_issue_fixture
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox
  [ "$status" -eq 0 ]
  [[ "$output" == *"arthur-debert/dodot"* ]]   # parsed from **Reported from:**
  [[ "$output" == *"arthur-debert/padz"* ]]    # parsed from a bare "Reported from:"
  [[ "$output" == *"3 comment(s)"* ]]
  [[ "$output" == *"https://x/42"* ]]
}

@test "an issue with no [component] prefix lands under 'other'" {
  cat > fix.json <<'JSON'
[ {"number":9,"title":"untagged escalation","body":"","createdAt":"2026-05-29T00:00:00Z","url":"https://x/9","comments":[]} ]
JSON
  _stub_gh "$PWD/fix.json"
  run "$BIN/release-core" admin inbox --json
  [ "$(echo "$output" | jq -r '.[0].component')" = "other" ]
}

@test "an unknown argument exits 64" {
  run "$BIN/release-core" admin inbox --nope
  [ "$status" -eq 64 ]
}

@test "a value-less --label exits 64 (not the bash-default 1)" {
  run "$BIN/release-core" admin inbox --label
  [ "$status" -eq 64 ]
  [[ "$output" == *"--label needs a value"* ]]
}

@test "--help exits 0 and prints usage" {
  run "$BIN/release-core" admin inbox --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}
