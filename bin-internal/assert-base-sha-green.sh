#!/usr/bin/env bash
#
# assert-base-sha-green.sh — refuse to release un-gated code.
#
# Principle (epic #811, WS2): a release of code whose last change was CI-checked
# is safe. The work PR that landed the release base sha already ran the full gate
# (lint + compile + tests) on exactly this code. So `prepare` no longer re-runs
# that gate — it ASSERTS the base sha is CI-green instead, in seconds, via the
# GitHub check-runs API.
#
# The base sha is the commit being released — main HEAD as checked out, BEFORE
# the version-file bump (the bump only rewrites version strings; it ships no code
# that wasn't already on the base sha and CI-checked there).
#
# SCOPED TO THE CODE GATE. The assertion targets the CODE gate (ci/check, ci/e2e,
# native-e2e/*, request/*, …) — NOT the RELEASE pipeline's own check-runs. The
# release runs ON the base sha, so its own check-runs land on that same sha:
#   - the CURRENT cut's in-progress `release / prepare` (still running when this
#     guard executes) would otherwise read as "not finished" → never green; and
#   - a PRIOR failed release attempt leaves a `release / prepare = failure` on the
#     sha permanently, which would block every future cut of that sha.
# Either makes a real cut un-passable even though the code is gated and green. So
# we EXCLUDE the release pipeline's check-runs and assert only the rest:
#   1. by NAME — drop any check-run whose name starts with "release /" (the
#      conventional caller-job name for the reusable release workflow: "release /
#      preflight", "release / prepare", "release / build (…)", "release / sign /
#      sign", "release / release"). This is a NAMING-CONVENTION assumption.
#   2. by CHECK SUITE (belt-and-suspenders, covers a consumer whose caller job is
#      NOT named "release") — when GITHUB_RUN_ID is set, also drop check-runs in
#      the current run's check_suite. Degrades gracefully: skipped silently when
#      GITHUB_RUN_ID is absent (local invocation) or the lookup fails.
#
# Env vars:
#   BASE_SHA            commit SHA to assert green (required)
#   GITHUB_REPOSITORY   owner/repo (provided by Actions; required)
#   GH_TOKEN            token for `gh api` auth (required for private repos)
#   GITHUB_RUN_ID       current release run id (optional; enables the check_suite
#                       exclusion — the current run's own suite is dropped)
#
# Success criterion: AFTER excluding the release pipeline's runs, the sha has at
# least one (code-gate) check-run AND every one is in a passing terminal state
# (success / neutral / skipped). ANY failing, cancelled, timed-out,
# action-required, or still-running check fails fast. ZERO code-gate check-runs
# (none left after exclusion) also fails — we never release a sha we can't prove
# was gated.
set -euo pipefail

: "${BASE_SHA:?BASE_SHA is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

if ! command -v gh >/dev/null 2>&1; then
	echo "::error::gh CLI is not on PATH — cannot assert the base sha is CI-green." >&2
	exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
	echo "::error::jq is not on PATH — needed to reduce the check-runs response (gh --slurp is piped to jq)." >&2
	exit 1
fi

err_log="$(mktemp)"
trap 'rm -f "${err_log}"' EXIT

# Belt-and-suspenders: resolve THIS run's check_suite so we can exclude the
# current release's own check-runs even when the caller job isn't named
# "release". Optional + best-effort: only when GITHUB_RUN_ID is set (Actions),
# and a lookup failure leaves it empty (the name-based exclusion still applies).
# Empty string means "exclude nothing by suite" in the jq filter below.
exclude_suite=""
if [ -n "${GITHUB_RUN_ID:-}" ]; then
	exclude_suite="$(gh api \
		"repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}" \
		--jq '.check_suite_id // empty' 2>>"${err_log}" || true)"
fi

# Fetch every check-run on the sha. --slurp collects all pages into one array of
# response objects (so a sha with >100 runs is fully covered) and lets jq reduce
# across the WHOLE set at once — needed because the API returns EVERY historical
# check-run, including stale ones from earlier re-runs of the same check name. We
# collapse to the LATEST run per check name (max by started_at, completed_at as
# tiebreak) so an earlier failed run that was re-run green doesn't false-negative.
# Capture stderr separately so an auth/permission failure (the Checks API needs
# `checks: read` on GITHUB_TOKEN) is surfaced, not misread as "absent CI".
#
# --slurp can't combine with --jq, so pipe the slurped pages to external jq. The
# slurped shape is an array of page objects ({total_count, check_runs:[...]}), so
# `[.[].check_runs[]]` flattens the runs across all pages. pipefail (set above)
# makes EITHER stage failing (gh OR jq) trip the `if !`. Capture BOTH stages'
# stderr into the same file (append — they run concurrently in the pipe) so a jq
# failure (missing jq, unexpected JSON shape) surfaces its message too, not an
# empty/misleading report.
#
# EXCLUSION happens FIRST (before group_by), so the release pipeline's runs never
# enter the per-name collapse: drop names starting with "release /", and — when
# $suite is non-empty — drop runs in the current release run's check_suite.
if ! runs="$(gh api \
	"repos/${GITHUB_REPOSITORY}/commits/${BASE_SHA}/check-runs" \
	--paginate --slurp 2>>"${err_log}" \
	| jq -r --arg suite "${exclude_suite}" '
	      [ .[].check_runs[]
	        | select((.name // "") | startswith("release /") | not)
	        | select($suite == "" or ((.check_suite.id | tostring) != $suite)) ]
	      | group_by(.name)
	      | map(max_by(.started_at // "", .completed_at // ""))
	      | .[] | "\(.status) \(.conclusion // "")"' 2>>"${err_log}")"; then
	echo "::error::failed to query check-runs for ${BASE_SHA} (auth/permission? the Checks API needs \`checks: read\`; or a jq/JSON-shape error — see below):" >&2
	sed 's/^/::error::  /' "${err_log}" >&2
	exit 1
fi

if [ -z "${runs}" ]; then
	echo "::error::no non-release check-runs found on ${BASE_SHA} — cannot prove the code was gated." >&2
	echo "::error::(The release pipeline's own check-runs are excluded; only the CODE gate counts.)" >&2
	echo "::error::Releasing un-gated code is refused (epic #811). Push the release base to a branch whose CI ran and is green." >&2
	exit 1
fi

# A passing terminal state is completed + (success | neutral | skipped).
# Anything else — a non-success conclusion, or a run that hasn't completed —
# means the sha is not provably green.
not_green=0
while IFS=' ' read -r status conclusion; do
	[ -z "${status}" ] && continue
	if [ "${status}" != "completed" ]; then
		echo "::error::check-run on ${BASE_SHA} not finished (status=${status})." >&2
		not_green=1
		continue
	fi
	case "${conclusion}" in
		success | neutral | skipped) ;;
		*)
			echo "::error::check-run on ${BASE_SHA} did not pass (conclusion=${conclusion:-none})." >&2
			not_green=1
			;;
	esac
done <<<"${runs}"

if [ "${not_green}" != "0" ]; then
	echo "::error::release base sha ${BASE_SHA} is not CI-green — releasing un-gated code is refused (epic #811)." >&2
	exit 1
fi

echo "release base sha ${BASE_SHA} is CI-green — every check-run passed."
