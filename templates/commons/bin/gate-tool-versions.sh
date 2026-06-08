# shellcheck shell=sh
# gate-tool-versions.sh — single source of truth for the pinned gate-toolset
# versions (release#498 follow-up). SOURCED, not executed.
#
# Two provisioners install the same gate tools and MUST agree on versions:
#   * bin-internal/provision-gate-toolset.sh   — the CI-side provisioner
#   * templates/commons/bin/setup-dev-env.sh   — the SessionStart provisioner
#                                                (synced into every consumer)
# Rather than duplicate the literals in both (the drift trap this file closes),
# both `.`-source this. It is synced alongside setup-dev-env.sh, so a consumer
# always receives the two together in one managed sync — never a new script
# against a missing file.
#
# Each is overridable via env (the `:-` default) so CI / a dev can pin a
# different version without editing this file.
RUFF_VERSION="${RUFF_VERSION:-0.15.12}"
ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-1.7.7}"
