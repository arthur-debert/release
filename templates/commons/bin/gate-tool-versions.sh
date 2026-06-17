# shellcheck shell=sh
# gate-tool-versions.sh — single source of truth for the pinned gate-toolset
# versions AND the version-reconcile helper. SOURCED, not executed.
#
# Two provisioners install the same gate tools and MUST agree on versions:
#   * bin-internal/provision-gate-toolset.sh   — the CI-side provisioner
#   * templates/commons/bin/setup-dev-env.sh   — the SessionStart provisioner
#                                                (synced into every consumer)
# (env/setup.sh — the cloud-snapshot builder — also sources this for the
# lefthook pin so the baked image matches.)
# Rather than duplicate the literals (the divergence trap this file closes), they
# all `.`-source this. It is synced alongside setup-dev-env.sh, so a consumer
# always receives them together in one managed sync — never a new script
# against a missing file.
#
# WHY EVERY tool is pinned (release#531): "one gate, run everywhere" was true for
# the CONFIG (lefthook.yml) but FALSE for the tool binaries it invokes — only
# ruff + actionlint carried a pin, the other five floated (npm-latest / pip-latest
# / apt-0.9.0 vs brew-0.11). The same gate could pass on one box and fail on
# another from pure version divergence (the WS3 shellcheck --rcfile detour was exactly
# this). Worse, the pin only fired install-IF-MISSING, so a pre-existing floating
# binary silently won — a dev box ran actionlint 1.7.12 while CI ran the pinned
# 1.7.7. The fix: pin ALL of them, and RECONCILE to the pin (gate_version_matches
# below) instead of install-if-missing, so a diverged binary is reinstalled at the
# pin, not accepted.
#
# Each is overridable via env (the `:-` default) so CI / a dev can pin a
# different version without editing this file.
RUFF_VERSION="${RUFF_VERSION:-0.15.12}"
ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-1.7.7}"
YAMLLINT_VERSION="${YAMLLINT_VERSION:-1.38.0}"
LEFTHOOK_VERSION="${LEFTHOOK_VERSION:-2.1.9}"
PRETTIER_VERSION="${PRETTIER_VERSION:-3.8.4}"
MARKDOWNLINT_CLI_VERSION="${MARKDOWNLINT_CLI_VERSION:-0.48.0}"
# Note: shellcheck has no version-pinnable apt/brew package that matches across
# the fleet (apt ships 0.9.0, brew floats), so it rides the SAME pinned-pip path as
# ruff/yamllint via the shellcheck-py wheel (bundles the real binary; mac +
# manylinux x86_64). SHELLCHECK_VERSION is what the BINARY reports (for the
# version-match check); SHELLCHECK_PY_VERSION is the pip package that delivers it.
SHELLCHECK_VERSION="${SHELLCHECK_VERSION:-0.11.0}"
SHELLCHECK_PY_VERSION="${SHELLCHECK_PY_VERSION:-0.11.0.1}"
# yq (mikefarah/Go) — release_core.yamlio's YAML reader (`yq -o=json`) AND the
# `yq eval-all` lefthook-merge seam that `release-core init` runs. It is as
# load-bearing a gate dependency as the rest, but until release#755 it was the
# one UNPINNED + UNPROVISIONED member: GH runners and brew happened to ship
# mikefarah, so no provisioner installed it — until a cloud image's kislyuk
# python-yq (a jq wrapper with no `-o=json`) squatted /usr/bin/yq and hard-failed
# every gate read + `init`. Pin it and provision it like actionlint (a downloaded
# GH-release binary). The FIRST dotted token of `yq --version` is 4.x for
# mikefarah / 3.x for the python stub, so gate_version_matches reconciles a stub
# (or a wrong mikefarah version) to this pin. Numeric here; the download URL tag
# prepends `v`.
YQ_VERSION="${YQ_VERSION:-4.44.3}"

# gate_version_matches <tool> <wanted-version>
# True (exit 0) iff <tool> is on PATH AND its reported version equals
# <wanted-version>. Both provisioners use this to decide whether to (re)install
# at the pin: a pre-existing FLOATING binary (brew/apt/npm-latest) reports a
# different version and gets reconciled to the pin instead of silently winning —
# closing the install-if-missing hole. The FIRST dotted token of `--version` is
# the tool's own version for every gate tool here (ruff/yamllint/prettier/
# markdownlint/lefthook/actionlint/shellcheck), so extract and compare that.
# POSIX sh — no bash arrays / [[ ]] (this file is sourced by sh-mode too).
# grep -Eo prints each match on its own line, so it pulls the version token out
# of `--version` output directly — no need to pre-split on whitespace.
gate_version_matches() {
  command -v "$1" >/dev/null 2>&1 || return 1
  _gvm_have="$("$1" --version 2>/dev/null \
    | grep -Eo '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1)"
  [ "$_gvm_have" = "$2" ]
}
