#!/usr/bin/env bash
# lint-consumer-contract.sh — the assumption lint (#584, epic #583 WS-A).
#
# Scans every CI surface (.github/workflows/*.yml, .github/actions/*/action.yml,
# the workflow copies under templates/) for any JOB whose steps reference a
# managed path (per docs/references/consumer-contract.yaml) without a prior
# install step in the same job (arm-gate / `release-core init`). This is
# the mechanical "who still assumes the old shape" sweep that would have caught
# release#579 the day WS7 merged.
#
# Runs the WORKING-TREE release_core via PYTHONPATH — NEVER the installed wheel
# (the PATH `release-core` is the latest release, so a PR changing sync.py
# classification would otherwise lint against the OLD contract). Stdlib + yq
# only on this path (no click), so the gate needs no extra provisioning.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
PYTHONPATH="templates/commons/lib/release_core${PYTHONPATH:+:$PYTHONPATH}" \
  exec python3 -m release_core.verbs.contract lint "$@"
