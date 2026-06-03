#!/usr/bin/env bash
#
# native-e2e-scope.sh — decide the native-E2E scope (thin | full) for the
# managed tauri-e2e reusable workflow, writing `full=<bool>` to $GITHUB_OUTPUT.
#
# The native lane ALWAYS runs (native ships on desktop, so it gates every PR),
# but narrows its SCOPE to keep frontend-only PRs fast:
#   - full  — every native-capable scenario. Non-PR events (nightly schedule /
#             manual dispatch) and PRs that touch the transport seam / native
#             shell.
#   - thin  — the default for a frontend-only PR.
#
# Path-gating is done with plain `git diff` (no third-party Marketplace action —
# the consumer org's enterprise Actions policy blocks dorny/paths-filter). The
# consumer supplies the "what counts as full" file set as an ERE regex via
# FULL_SCOPE_PATHS; doc-only changes (*.md/*.lex) are dropped first so a docs PR
# never forces full.
#
# Env (set by the reusable workflow):
#   EVENT             github.event_name
#   BASE_SHA          github.event.pull_request.base.sha (PR events only)
#   HEAD_SHA          github.event.pull_request.head.sha (PR events only)
#   FULL_SCOPE_PATHS  ERE regex; any changed non-doc file matching it ⇒ full
#   GITHUB_OUTPUT     standard Actions output file
set -euo pipefail

# Default the env so `set -u` can't crash on an unset var and the script stays
# runnable locally (GITHUB_OUTPUT → stdout for inspection).
EVENT="${EVENT:-}"
FULL_SCOPE_PATHS="${FULL_SCOPE_PATHS:-}"
BASE_SHA="${BASE_SHA:-}"
HEAD_SHA="${HEAD_SHA:-}"
GITHUB_OUTPUT="${GITHUB_OUTPUT:-/dev/stdout}"

if [ -z "${FULL_SCOPE_PATHS}" ]; then
	echo "native-e2e-scope: FULL_SCOPE_PATHS is required (the consumer's full-scope regex)." >&2
	exit 1
fi

# Non-PR events (schedule / workflow_dispatch) always sweep the full suite.
if [ "${EVENT}" != "pull_request" ]; then
	echo "Non-PR event (${EVENT:-<unset>}) → full native scope."
	echo "full=true" >>"${GITHUB_OUTPUT}"
	exit 0
fi

if [ -z "${BASE_SHA}" ] || [ -z "${HEAD_SHA}" ]; then
	echo "native-e2e-scope: BASE_SHA and HEAD_SHA are required for pull_request events." >&2
	exit 1
fi

changed="$(git diff --name-only "${BASE_SHA}" "${HEAD_SHA}")"
echo "Changed files:"
echo "${changed}"

# Drop doc-only files BEFORE the transport grep: a doc-only PR falls to thin,
# while a mixed PR keeps its non-doc files and can still trigger full.
changed="$(printf '%s\n' "${changed}" | grep -vE '\.(md|lex)$' || true)"

# No non-doc files left → nothing can touch the transport seam → thin. Also
# guards grep against an empty input, where an unanchored consumer regex could
# false-match a lone blank line.
if [ -z "${changed//[[:space:]]/}" ]; then
	echo "→ no non-doc changes: THIN native scope."
	echo "full=false" >>"${GITHUB_OUTPUT}"
	exit 0
fi

if printf '%s\n' "${changed}" | grep -Eq "${FULL_SCOPE_PATHS}"; then
	echo "→ transport-touching change: FULL native scope."
	echo "full=true" >>"${GITHUB_OUTPUT}"
else
	echo "→ frontend-only change: THIN native scope."
	echo "full=false" >>"${GITHUB_OUTPUT}"
fi
