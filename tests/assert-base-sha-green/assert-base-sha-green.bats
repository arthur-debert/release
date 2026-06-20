#!/usr/bin/env bats

# Contract suite for bin-internal/assert-base-sha-green.sh — the WS2 (#811)
# base-sha CI-green assertion that replaced the in-prepare re-gate.
#
# Fully offline: a stub `gh` on PATH replays a canned check-runs response from
# $CHECK_RUNS_JSON through the script's `--jq` filter (jq is required on the
# runner, same as production). The stub mimics `gh api … --jq …` by piping the
# canned JSON to jq with the requested filter.

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/assert-base-sha-green.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"
  mkdir -p bin-stub
  # Stub `gh`: for `gh api … --jq <filter>`, feed $CHECK_RUNS_JSON to jq with
  # that filter (matching `gh api --jq`'s behavior). $GH_FAIL=1 makes the API
  # call itself error (network/auth failure) — the script tolerates that and
  # treats it as "no runs".
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
[ "${GH_FAIL:-0}" = "1" ] && exit 1
filter='.'
prev=""
for arg in "$@"; do
  [ "$prev" = "--jq" ] && filter="$arg"
  prev="$arg"
done
printf '%s' "${CHECK_RUNS_JSON:-}" | jq -r "$filter"
EOF
  chmod +x bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"
  export BASE_SHA=deadbeef GITHUB_REPOSITORY=acme/widget
}

teardown() {
  cd / || true
  rm -rf "$TMP"
}

@test "all check-runs success → passes" {
  export CHECK_RUNS_JSON='{"check_runs":[
    {"status":"completed","conclusion":"success"},
    {"status":"completed","conclusion":"success"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"is CI-green"* ]]
}

@test "neutral and skipped conclusions count as passing" {
  export CHECK_RUNS_JSON='{"check_runs":[
    {"status":"completed","conclusion":"success"},
    {"status":"completed","conclusion":"neutral"},
    {"status":"completed","conclusion":"skipped"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "a failing check-run fails fast" {
  export CHECK_RUNS_JSON='{"check_runs":[
    {"status":"completed","conclusion":"success"},
    {"status":"completed","conclusion":"failure"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not CI-green"* ]]
  [[ "$output" == *"conclusion=failure"* ]]
}

@test "a cancelled check-run fails" {
  export CHECK_RUNS_JSON='{"check_runs":[{"status":"completed","conclusion":"cancelled"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
}

@test "a still-running check-run fails (not provably green)" {
  export CHECK_RUNS_JSON='{"check_runs":[
    {"status":"completed","conclusion":"success"},
    {"status":"in_progress","conclusion":null}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not finished"* ]]
}

@test "absent CI (zero check-runs) is refused" {
  export CHECK_RUNS_JSON='{"check_runs":[]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no check-runs found"* ]]
}

@test "gh api failure surfaces the error and refuses (not misread as absent CI)" {
  export GH_FAIL=1
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed to query check-runs"* ]]
  [[ "$output" != *"no check-runs found"* ]]
}

@test "missing BASE_SHA errors" {
  unset BASE_SHA
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"BASE_SHA is required"* ]]
}

@test "missing GITHUB_REPOSITORY errors" {
  unset GITHUB_REPOSITORY
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"GITHUB_REPOSITORY is required"* ]]
}
