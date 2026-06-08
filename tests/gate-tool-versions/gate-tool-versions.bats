#!/usr/bin/env bats
# gate-tool-versions.sh is the SINGLE source of truth for the pinned gate-toolset
# versions, shared by the CI provisioner (bin-internal/provision-gate-toolset.sh)
# and the SessionStart provisioner (templates/commons/bin/setup-dev-env.sh)
# (release#498 follow-up). These tests lock that contract:
#   * sourcing the file yields the pins,
#   * both provisioners actually source it (no re-pinned literal),
#   * setup-dev-env's resilience fallback (kept so a missing file can't abort the
#     SessionStart hook before install-release-core can self-heal) cannot DRIFT
#     from the shared file.

ROOT="$BATS_TEST_DIRNAME/../.."
VERSIONS="$ROOT/templates/commons/bin/gate-tool-versions.sh"
SETUP="$ROOT/templates/commons/bin/setup-dev-env.sh"
PROVISION="$ROOT/bin-internal/provision-gate-toolset.sh"

@test "sourcing gate-tool-versions.sh sets both pins" {
  run bash -c ". '$VERSIONS' && echo \"\$RUFF_VERSION|\$ACTIONLINT_VERSION\""
  [ "$status" -eq 0 ]
  [[ "$output" == *.*"|"*.* ]]   # both non-empty, dotted versions
}

@test "env overrides win over the file pins" {
  run env RUFF_VERSION=9.9.9 ACTIONLINT_VERSION=8.8.8 \
    bash -c ". '$VERSIONS'; echo \"\$RUFF_VERSION|\$ACTIONLINT_VERSION\""
  [ "$status" -eq 0 ]
  [ "$output" = "9.9.9|8.8.8" ]
}

@test "both provisioners source the shared file (no independent re-pin)" {
  grep -q 'gate-tool-versions.sh' "$SETUP"
  grep -q 'gate-tool-versions.sh' "$PROVISION"
  # provision-gate-toolset.sh must NOT carry its own RUFF/ACTIONLINT literal pin
  # — match an assignment with double, single, or no quotes around the version.
  run grep -E "^(RUFF|ACTIONLINT)_VERSION=['\"]?[0-9]" "$PROVISION"
  [ "$status" -ne 0 ]
}

@test "setup-dev-env resilience fallback matches the shared file (cannot drift)" {
  # Authoritative values from the shared file.
  src="$(bash -c ". '$VERSIONS' && echo \"\$RUFF_VERSION \$ACTIONLINT_VERSION\"")"
  want_ruff="${src%% *}"; want_actionlint="${src##* }"
  # The fallback values from setup-dev-env's `: "${VAR:=x}"` lines.
  fb_ruff="$(sed -n 's/.*RUFF_VERSION:=\([0-9.]*\).*/\1/p' "$SETUP" | head -1)"
  fb_actionlint="$(sed -n 's/.*ACTIONLINT_VERSION:=\([0-9.]*\).*/\1/p' "$SETUP" | head -1)"
  [ -n "$fb_ruff" ] && [ -n "$fb_actionlint" ]
  [ "$fb_ruff" = "$want_ruff" ]
  [ "$fb_actionlint" = "$want_actionlint" ]
}
