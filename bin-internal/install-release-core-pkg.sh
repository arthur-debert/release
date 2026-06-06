#!/usr/bin/env bash
# install-release-core-pkg.sh — pip-install the bundled release_core package
# so its console-scripts (changelog, changelog-render, changelog-add,
# changelog-cut, semver, …) land on PATH for the rest of the CI job.
#
# This is the single, DRY install mechanism for every release-CI call-site
# that used to exec the synced bin/changelog / bin/semver shims by file path
# (the prepare-release composite actions + roll-changelog.sh /
# check-changelog-fragments.sh / prepare-{nvim,tauri}-release.sh).
#
# It runs INSIDE a composite action's checkout, so the package source is at
# <action_path>/../../../templates/commons/lib/release_core — resolved here
# relative to this script's own location (bin-internal/ sits beside
# templates/). The caller invokes it via:
#   bash "${{ github.action_path }}/../../../bin-internal/install-release-core-pkg.sh"
#
# Python must already be available (the composite actions set up Python for
# the gate); this script asserts it and installs into that interpreter.
#
# After this runs, call the console-scripts by NAME (changelog new-version …,
# semver validate …) — never bin/changelog / bin/semver paths.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg="$script_dir/../templates/commons/lib/release_core"

if [ ! -f "$pkg/pyproject.toml" ]; then
  echo "::error::release_core source not found at $pkg" >&2
  exit 1
fi

# Prefer python3, fall back to python. The composite actions run
# actions/setup-python before this, so one of them is on PATH.
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "::error::no python interpreter on PATH — set up Python before install-release-core-pkg.sh" >&2
  exit 1
fi

"$PY" -m pip install "$pkg"

# Assert the console-scripts are actually resolvable on PATH (the installing
# python's scripts dir is on PATH under actions/setup-python).
missing=()
for s in changelog changelog-render changelog-add changelog-cut semver; do
  command -v "$s" >/dev/null 2>&1 || missing+=("$s")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "::error::console-scripts not on PATH after install: ${missing[*]}" >&2
  exit 1
fi

echo "release_core console-scripts on PATH: changelog changelog-render changelog-add changelog-cut semver"
