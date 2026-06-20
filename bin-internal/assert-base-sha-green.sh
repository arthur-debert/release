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
# Env vars:
#   BASE_SHA            commit SHA to assert green (required)
#   GITHUB_REPOSITORY   owner/repo (provided by Actions; required)
#   GH_TOKEN            token for `gh api` auth (required for private repos)
#
# Success criterion: the sha has at least one check-run AND every check-run is in
# a passing terminal state (success / neutral / skipped). ANY failing, cancelled,
# timed-out, action-required, or still-running check fails fast. ZERO check-runs
# (absent CI) also fails — we never release a sha we can't prove was gated.
set -euo pipefail

: "${BASE_SHA:?BASE_SHA is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

if ! command -v gh >/dev/null 2>&1; then
	echo "::error::gh CLI is not on PATH — cannot assert the base sha is CI-green." >&2
	exit 1
fi

# Fetch every check-run on the sha. --slurp collects all pages into one array of
# response objects (so a sha with >100 runs is fully covered) and lets jq reduce
# across the WHOLE set at once — needed because the API returns EVERY historical
# check-run, including stale ones from earlier re-runs of the same check name. We
# collapse to the LATEST run per check name (max by started_at, completed_at as
# tiebreak) so an earlier failed run that was re-run green doesn't false-negative.
# Capture stderr separately so an auth/permission failure (the Checks API needs
# `checks: read` on GITHUB_TOKEN) is surfaced, not misread as "absent CI".
gh_err="$(mktemp)"
trap 'rm -f "${gh_err}"' EXIT
if ! runs="$(gh api \
	"repos/${GITHUB_REPOSITORY}/commits/${BASE_SHA}/check-runs" \
	--paginate --slurp \
	--jq '[.[].check_runs[]]
	      | sort_by(.name)
	      | group_by(.name)
	      | map(max_by(.started_at // "", .completed_at // ""))
	      | .[] | "\(.status) \(.conclusion // "")"' 2>"${gh_err}")"; then
	echo "::error::failed to query check-runs for ${BASE_SHA} (auth/permission? the Checks API needs \`checks: read\`):" >&2
	sed 's/^/::error::  /' "${gh_err}" >&2
	exit 1
fi

if [ -z "${runs}" ]; then
	echo "::error::release base sha ${BASE_SHA} is not CI-green — no check-runs found on it." >&2
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
