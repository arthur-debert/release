#!/usr/bin/env bash
# install-release-core-pkg.sh — install release_core from THIS checkout's source
# (not a published wheel), for release's own CI: the prepare-release composite
# actions + changelog-check must test the code in the current PR.
#
# It is a thin delegation to the ONE install definition — `install-release-core
# --from-source <local release_core>` — so the isolated-venv install, the
# console-script PATH exposure, and the $GITHUB_PATH persistence are NOT
# re-implemented here (that was the fork). The only thing this script owns is
# locating the in-repo source + resolver relative to its own path.
#
# Called from a composite action's checkout via:
#   bash "${{ github.action_path }}/../../../bin-internal/install-release-core-pkg.sh"
#
# After it runs, call the console-scripts by NAME (changelog …, semver …) in
# LATER steps — they reach PATH via $GITHUB_PATH (this is a standalone step).

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
pkg="$repo_root/templates/commons/lib/release_core"
resolver="$repo_root/bin/install-release-core"

exec bash "$resolver" --from-source "$pkg" --no-init
