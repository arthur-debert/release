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
  # Stub `gh`: routes by the API path.
  #  - `…/commits/<sha>/check-runs` (the slurped query): emit the raw
  #    `--paginate --slurp` shape — an ARRAY of page-response objects
  #    ([{total_count, check_runs:[…]}]). The canned $CHECK_RUNS_JSON is a single
  #    page object, so `jq -s '.'` wraps it into that one-element array. The stub
  #    applies NO reduction; the script's own jq pipe does it (production
  #    fidelity).
  #  - `…/actions/runs/<id>` (the optional check_suite lookup, only when
  #    GITHUB_RUN_ID is set): echo $RUN_CHECK_SUITE_ID through the requested
  #    `--jq` filter so the script learns which suite to exclude.
  # $GH_FAIL=1 makes the check-runs call exit non-zero — under the script's
  # `set -o pipefail` that trips the `if !` so the explicit "failed to query
  # check-runs" error fires, distinct from the absent-CI (empty) path.
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
path=""
for arg in "$@"; do
  case "$arg" in repos/*) path="$arg" ;; esac
done
case "$path" in
  */actions/runs/*)
    # the check_suite lookup: `gh api … --jq '.check_suite_id // empty'`
    filter='.'
    prev=""
    for arg in "$@"; do [ "$prev" = "--jq" ] && filter="$arg"; prev="$arg"; done
    printf '{"check_suite_id": %s}' "${RUN_CHECK_SUITE_ID:-null}" | jq -r "$filter"
    ;;
  *)
    [ "${GH_FAIL:-0}" = "1" ] && exit 1
    printf '%s' "${CHECK_RUNS_JSON:-}" | jq -s '.'
    ;;
esac
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
  [[ "$output" == *"no non-release check-runs found"* ]]
}

# --- code-gate scoping: exclude the release pipeline's own check-runs --------
# The release runs ON the base sha, so its own check-runs (the current cut's
# in-progress `release / prepare`, and any PRIOR failed attempt's
# `release / prepare = failure`) land on the sha. The guard must EXCLUDE them by
# name ("release / …") — and, when GITHUB_RUN_ID is set, by check_suite — and
# assert only the CODE gate.

@test "release/* failing+in-progress runs are excluded; the green code gate passes" {
  # The ONLY failing / unfinished runs are release/* (a prior failed attempt +
  # the current in-progress prepare). The code gate (ci/*) is green → passes.
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"release / prepare","status":"completed","conclusion":"failure"},
    {"name":"release / preflight","status":"in_progress","conclusion":null},
    {"name":"ci / check","status":"completed","conclusion":"success"},
    {"name":"ci / e2e","status":"completed","conclusion":"success"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"is CI-green"* ]]
}

@test "zero non-release check-runs → refused (only release/* present)" {
  # If EVERY run on the sha is a release/* run, nothing proves the code was
  # gated — refuse rather than pass an un-gated sha.
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"release / prepare","status":"completed","conclusion":"success"},
    {"name":"release / preflight","status":"completed","conclusion":"success"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no non-release check-runs found"* ]]
}

@test "a red ci/* code-gate run still fails (exclusion doesn't mask real failures)" {
  # release/* is green/excluded, but a CODE-gate check failed → must fail. Proves
  # the exclusion is scoped, not a blanket pass.
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"release / prepare","status":"completed","conclusion":"success"},
    {"name":"ci / check","status":"completed","conclusion":"success"},
    {"name":"ci / e2e","status":"completed","conclusion":"failure"}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"conclusion=failure"* ]]
}

@test "check_suite exclusion: a non-release-named caller's own suite is dropped (GITHUB_RUN_ID set)" {
  # A consumer whose caller job is NOT named "release" — here "publish / prepare"
  # in the CURRENT run's check_suite (id 999), failing. With GITHUB_RUN_ID set,
  # the script resolves suite 999 and excludes it, leaving only the green code
  # gate. Without the suite exclusion this would (wrongly) fail.
  export GITHUB_RUN_ID=12345
  export RUN_CHECK_SUITE_ID=999
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"publish / prepare","status":"in_progress","conclusion":null,"check_suite":{"id":999}},
    {"name":"ci / check","status":"completed","conclusion":"success","check_suite":{"id":111}}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"is CI-green"* ]]
}

@test "check_suite exclusion is skipped when GITHUB_RUN_ID is unset (local invocation)" {
  # No GITHUB_RUN_ID → no suite lookup; only the name-based exclusion applies.
  # The code gate is green and there are no release/* runs → passes.
  unset GITHUB_RUN_ID
  export CHECK_RUNS_JSON='{"check_runs":[
    {"name":"ci / check","status":"completed","conclusion":"success","check_suite":{"id":111}}]}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"is CI-green"* ]]
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
