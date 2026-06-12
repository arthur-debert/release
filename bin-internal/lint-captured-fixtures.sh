#!/usr/bin/env bash
# lint-captured-fixtures.sh — the external-surface fixture provenance lint (#623).
#
# Sweeps the registered external-surface seams (release_core's prstate gh-API
# payload dir + the classify / apply_ruleset inline-constant test files) for any
# fixture lacking the `captured-from:` provenance marker. A NEW unmarked fixture
# fails; today's offenders are grandfathered by the shrink-only baseline
# (templates/commons/lib/release_core/tests/captured-fixture-lint-baseline.yaml);
# a STALE baseline entry (a fixture that since got a marker) fails too. The
# baseline only ever shrinks — capture the fixture for real, never grow the
# baseline. See docs/dev/captured-fixture-provenance.md.
#
# Runs the WORKING-TREE release_core via PYTHONPATH — NEVER the installed wheel
# (the PATH `release-core` is the latest release, so a PR adding a fixture would
# otherwise lint against the OLD seam registry). Stdlib + yq only on this path
# (no click), so the gate needs no extra provisioning.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
PYTHONPATH="templates/commons/lib/release_core${PYTHONPATH:+:$PYTHONPATH}" \
  exec python3 -m release_core.verbs.captured_fixtures lint "$@"
