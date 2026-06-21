#!/usr/bin/env bash
# Resolve the latest available consumer `compile-<platform>` artifact and restore
# it into a checkout, so the bundle smoke test (bundle-smoke.sh) can run the real
# `tauri bundle` path against a PRE-BUILT binary — no recompile, no secrets, no
# private source beyond the consumer's own checkout (#841).
#
# This is the on-demand-fixture half of the design (settled with the maintainer):
# there is NO committed fixture. We pull the newest non-expired `compile-<plat>`
# artifact (the binary + frontendDist that stage-tauri-compile.sh uploaded in a
# real release run) from a consumer repo and restore it via the SAME logic the
# package job uses (restore-tauri-compile.sh).
#
# LIMITATION (documented here + in #841): GitHub artifacts retain for ~7 days. If
# the consumer hasn't run a release recently there is NO artifact to pull, and
# this script FAILS LOUD (exit 3) rather than skipping — a silent skip would be
# false confidence. A re-run after the consumer cuts/builds resolves it.
#
# Env vars:
#   CONSUMER_REPO  owner/name to pull from        (default: phos-editor/app)
#   PLATFORM       linux | mac | windows          (default: linux)
#   CHECKOUT_DIR   consumer checkout to restore into; must already contain the
#                  consumer source (tauri.conf.json, package.json). REQUIRED.
#   TAURI_DIR      Tauri project root, repo-root-relative (default: ".")
#   GH_TOKEN       token gh uses (RELEASE_TOKEN in CI; a normal gh login locally)
#
# Requires `gh` authenticated for CONSUMER_REPO. Writes the resolved run id to
# $GITHUB_OUTPUT (run_id=…) when set.

set -euo pipefail

CONSUMER_REPO="${CONSUMER_REPO:-phos-editor/app}"
PLATFORM="${PLATFORM:-linux}"
TAURI_DIR="${TAURI_DIR:-.}"
CHECKOUT_DIR="${CHECKOUT_DIR:?CHECKOUT_DIR required (the consumer checkout to restore the artifact into)}"

artifact="compile-${PLATFORM}"

[ -d "${CHECKOUT_DIR}" ] || { echo "::error::CHECKOUT_DIR not found: ${CHECKOUT_DIR}"; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "::error::gh CLI not found on PATH"; exit 1; }

echo "Resolving latest non-expired '${artifact}' artifact from ${CONSUMER_REPO}…" >&2

# Newest non-expired artifact of this name + the run that produced it. The
# artifacts API carries expiry + the source run id, so we don't scan runs.
# --paginate + --slurp walks EVERY page (a busy consumer can have >100 artifacts,
# so a single page could miss the newest one) and slurps them into one array of
# page responses; the jq flattens .artifacts across pages, then picks the latest
# non-expired match and emits "<run_id> <head_sha>".
# (--slurp can't combine with --jq, so pipe the slurped pages to jq.)
read -r run_id head_sha < <(gh api --paginate --slurp \
  "repos/${CONSUMER_REPO}/actions/artifacts?per_page=100" \
  | jq -r "[.[].artifacts[] | select(.name==\"${artifact}\" and .expired==false)] | sort_by(.created_at) | reverse | .[0] | \"\(.workflow_run.id // \"\") \(.workflow_run.head_sha // \"\")\"")

if [ -z "${run_id}" ]; then
  echo "::error::no non-expired '${artifact}' artifact found in ${CONSUMER_REPO} (artifacts retain ~7d; have a recent release/build run produced one?). #841: on-demand fixture — re-run after the consumer builds." >&2
  exit 3
fi

echo "Found '${artifact}' in ${CONSUMER_REPO} run ${run_id} (head ${head_sha:-unknown}); downloading…" >&2

# Pin the consumer checkout to the artifact's commit when it's a git clone, so
# the SOURCE tree (tauri.conf, frontendDist layout) matches the PRE-BUILT binary.
# Without this, a consumer default-branch advance would mismatch source vs binary
# and fail the bundle for reasons unrelated to the dep-set regression we gate.
# Best-effort: only when CHECKOUT_DIR is a git repo and the sha is fetchable
# (a shallow clone may lack it — then we warn and proceed on the current tree).
if [ -n "${head_sha}" ] && git -C "${CHECKOUT_DIR}" rev-parse --git-dir >/dev/null 2>&1; then
  if git -C "${CHECKOUT_DIR}" cat-file -e "${head_sha}^{commit}" 2>/dev/null \
     || git -C "${CHECKOUT_DIR}" fetch --depth 1 origin "${head_sha}" 2>/dev/null; then
    git -C "${CHECKOUT_DIR}" checkout -q "${head_sha}" \
      && echo "Pinned ${CONSUMER_REPO} checkout to artifact commit ${head_sha}." >&2
  else
    echo "::warning::could not fetch artifact commit ${head_sha} in ${CHECKOUT_DIR} (shallow clone?); proceeding on the current checkout — source may not match the pre-built binary." >&2
  fi
fi

dl="$(mktemp -d)"
trap 'rm -rf "${dl}"' EXIT
gh run download "${run_id}" -R "${CONSUMER_REPO}" -n "${artifact}" -D "${dl}" \
  || { echo "::error::failed to download '${artifact}' from ${CONSUMER_REPO} run ${run_id}"; exit 1; }

# Restore via the package job's own logic (single source of truth) so the smoke
# test exercises exactly what CI does.
ARTIFACT_DIR="${dl}" REPO_ROOT="${CHECKOUT_DIR}" TAURI_DIR="${TAURI_DIR}" \
  bash "$(dirname "$0")/restore-tauri-compile.sh"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "run_id=${run_id}" >> "${GITHUB_OUTPUT}"
  echo "consumer_repo=${CONSUMER_REPO}" >> "${GITHUB_OUTPUT}"
fi
echo "Restored ${artifact} (run ${run_id}) into ${CHECKOUT_DIR}." >&2
