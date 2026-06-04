#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# audit-repo: per-repo conformance audit. Shim CLI contract + --json
# shape + exit-code precedence, exercised offline with a stub `gh`.
# ---------------------------------------------------------------------

@test "--help exits 0 and prints usage" {
  run "$BIN/release-core" audit --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"audit-repo"* ]]
}

@test "an unknown flag is a usage error (exit 64)" {
  run "$BIN/release-core" audit --nope
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg"* ]]
}

@test "--repo with no value is a usage error (exit 64)" {
  run "$BIN/release-core" audit --repo
  [ "$status" -eq 64 ]
}

# A repo where everything is absent: the stub returns 404 for every contents
# call and rulesets. ruleset+release_token+copilot fail → exit 1.
@test "--json emits the {repo,checks:[{name,status,message}]} shape" {
  _install_stub_gh
  # vulnerability-alerts present (204) and rulesets present so we get a mix.
  _gh_api "repos/o/r/rulesets" '[{"id":7,"name":"main-branch-protection"}]'
  run "$BIN/release-core" audit --repo o/r --json
  # exit code is 1 or 2 (there will be fails/warns), never a crash.
  [ "$status" -ne 64 ]
  [ "$(echo "$output" | jq -r '.repo')" = "o/r" ]
  [ "$(echo "$output" | jq -r '.checks[0] | has("name") and has("status") and has("message")')" = "true" ]
  # ruleset PASS surfaces in the checks array.
  [ "$(echo "$output" | jq -r '.checks[] | select(.name=="ruleset") | .status')" = "PASS" ]
}

@test "--quiet hides PASS/SKIP rows in human output" {
  _install_stub_gh
  _gh_api "repos/o/r/rulesets" '[{"id":7,"name":"main-branch-protection"}]'
  run "$BIN/release-core" audit --repo o/r --quiet
  # a FAIL is present (no RELEASE_TOKEN etc.), and PASS rows are suppressed.
  [[ "$output" == *"[FAIL]"* ]]
  [[ "$output" != *"[PASS]"* ]]
}

@test "human output leads with the repo name" {
  _install_stub_gh
  run "$BIN/release-core" audit --repo o/r
  [ "$(echo "$output" | head -1)" = "o/r" ]
}
