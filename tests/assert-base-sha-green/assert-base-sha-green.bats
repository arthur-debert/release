#!/usr/bin/env bats

# Contract suite for bin-internal/assert-base-sha-green.sh — the WS2 (#811)
# base-sha CI-green assertion that replaced the in-prepare re-gate.
#
# Fully offline: a stub `gh` on PATH replays a canned check-runs response from
# $CHECK_RUNS_JSON. The stub emits ONLY the raw slurped API JSON (an array of
# page objects) — it does NOT apply any jq filter. The script's OWN piped
# `jq -r` is what reduces the runs, so the test exercises the real piped-jq path
# (`gh api --slurp | jq`) rather than a stub that fakes `--jq`. This is what
# caught the `--slurp` + `--jq` incompatibility: a stub that swallowed `--jq`
# would pass even though real gh rejects the combination.

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/assert-base-sha-green.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"
  mkdir -p bin-stub
  # Stub `gh`: emit the raw `--paginate --slurp` shape — an ARRAY of page-response
  # objects ([{total_count, check_runs:[…]}]). The canned $CHECK_RUNS_JSON is a
  # single page object, so `jq -s '.'` wraps it into that one-element array. The
  # stub applies NO reduction; the script's own jq pipe does it (production
  # fidelity). $GH_FAIL=1 makes the API call exit non-zero — under the script's
  # `set -o pipefail` that trips the `if !` so the explicit "failed to query
  # check-runs" error fires, distinct from the absent-CI (empty) path.
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
[ "${GH_FAIL:-0}" = "1" ] && exit 1
printf '%s' "${CHECK_RUNS_JSON:-}" | jq -s '.'
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

@test "a re-run check: latest success wins over an earlier failure (interleaved names)" {
  # The Checks API returns EVERY historical run, interleaved by check name. An
  # earlier 'gate' failure that was re-run green must NOT false-negative — we
  # evaluate only the LATEST run per name. The two 'gate' runs are deliberately
  # non-adjacent (a 'build' run sits between them) to regression-guard the
  # group-by-name collapse against unsorted/interleaved input.
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"gate","status":"completed","conclusion":"failure","started_at":"2026-06-20T10:00:00Z"},
    {"name":"build","status":"completed","conclusion":"success","started_at":"2026-06-20T10:30:00Z"},
    {"name":"gate","status":"completed","conclusion":"success","started_at":"2026-06-20T11:00:00Z"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"is CI-green"* ]]
}

@test "a re-run check: latest failure loses to nothing — still fails (interleaved names)" {
  # Symmetric guard: if the LATEST run for a name failed (earlier was green), the
  # sha is not green — again with the two 'gate' runs non-adjacent.
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"gate","status":"completed","conclusion":"success","started_at":"2026-06-20T10:00:00Z"},
    {"name":"build","status":"completed","conclusion":"success","started_at":"2026-06-20T10:30:00Z"},
    {"name":"gate","status":"completed","conclusion":"failure","started_at":"2026-06-20T11:00:00Z"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"conclusion=failure"* ]]
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

@test "a jq failure (unexpected JSON shape) surfaces jq's stderr, not an empty report" {
  # The script pipes the slurped API JSON to its OWN jq. If that jq fails (here
  # the slurped value is a string, so `[.[].check_runs[]]` can't index it), its
  # stderr must be captured into the same err log and surfaced — not swallowed,
  # leaving a misleading empty "failed to query" report. Regression for the
  # jq-stderr capture (#846 review).
  export CHECK_RUNS_JSON='"not-an-object"'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed to query check-runs"* ]]
  # jq's own diagnostic is present (it indexes a string with check_runs)
  [[ "$output" == *"check_runs"* ]]
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
