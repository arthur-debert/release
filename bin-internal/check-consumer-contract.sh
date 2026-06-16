#!/usr/bin/env bash
# check-consumer-contract.sh — manifest freshness check (#584, epic #583 WS-A).
#
# Regenerates docs/references/consumer-contract.yaml in memory from sync.py's
# constants + predicates applied to templates/, and diffs against the committed
# file. Fails (with the diff) when out of sync — a contract-changing PR must run
# `release-core admin contract dump` and commit the result in the SAME PR.
#
# Runs the WORKING-TREE release_core via PYTHONPATH — NEVER the installed wheel
# (the PATH `release-core` is the latest release, so a PR changing sync.py
# classification would otherwise regenerate from the OLD code). Stdlib only on
# this path (no click), so the gate needs no extra provisioning.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
PYTHONPATH="templates/commons/lib/release_core${PYTHONPATH:+:$PYTHONPATH}" \
  exec python3 -m release_core.verbs.contract check "$@"
