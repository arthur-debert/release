#!/usr/bin/env bats
# gate-tool-versions.sh is the SINGLE source of truth for the pinned gate-toolset
# versions AND the gate_version_matches reconcile helper, shared by the CI
# provisioner (bin-internal/provision-gate-toolset.sh) and the SessionStart
# provisioner (templates/commons/bin/setup-dev-env.sh) (release#498, release#531).
# These tests lock that contract:
#   * sourcing the file yields every pin,
#   * both provisioners actually source it (no re-pinned literal),
#   * setup-dev-env's resilience fallbacks (kept so a missing file can't abort the
#     SessionStart hook before install-release-core self-heals) cannot DRIFT from
#     the shared file,
#   * gate_version_matches reconciles (matches the pin / flags a drifted binary).

ROOT="$BATS_TEST_DIRNAME/../.."
VERSIONS="$ROOT/templates/commons/bin/gate-tool-versions.sh"
SETUP="$ROOT/templates/commons/bin/setup-dev-env.sh"
PROVISION="$ROOT/bin-internal/provision-gate-toolset.sh"
ENV_SETUP="$ROOT/env/setup.sh"

# Every pinned var the shared file MUST set.
PINS="RUFF_VERSION ACTIONLINT_VERSION YAMLLINT_VERSION LEFTHOOK_VERSION \
PRETTIER_VERSION MARKDOWNLINT_CLI_VERSION SHELLCHECK_VERSION SHELLCHECK_PY_VERSION"

@test "sourcing gate-tool-versions.sh sets every pin to a dotted version" {
  for var in $PINS; do
    run bash -c ". '$VERSIONS' && printf '%s' \"\$$var\""
    [ "$status" -eq 0 ]
    [[ "$output" == *.* ]]   # non-empty, dotted
  done
}

@test "env overrides win over the file pins (every pin)" {
  run env \
    RUFF_VERSION=9.9.9 ACTIONLINT_VERSION=8.8.8 YAMLLINT_VERSION=7.7.7 \
    LEFTHOOK_VERSION=6.6.6 PRETTIER_VERSION=5.5.5 MARKDOWNLINT_CLI_VERSION=4.4.4 \
    SHELLCHECK_VERSION=3.3.3 SHELLCHECK_PY_VERSION=2.2.2 \
    bash -c ". '$VERSIONS'; printf '%s|%s|%s|%s|%s|%s|%s|%s' \
      \"\$RUFF_VERSION\" \"\$ACTIONLINT_VERSION\" \"\$YAMLLINT_VERSION\" \
      \"\$LEFTHOOK_VERSION\" \"\$PRETTIER_VERSION\" \"\$MARKDOWNLINT_CLI_VERSION\" \
      \"\$SHELLCHECK_VERSION\" \"\$SHELLCHECK_PY_VERSION\""
  [ "$status" -eq 0 ]
  [ "$output" = "9.9.9|8.8.8|7.7.7|6.6.6|5.5.5|4.4.4|3.3.3|2.2.2" ]
}

@test "both provisioners source the shared file (no independent re-pin)" {
  grep -q 'gate-tool-versions.sh' "$SETUP"
  grep -q 'gate-tool-versions.sh' "$PROVISION"
  # provision-gate-toolset.sh must NOT carry its own literal pin for ANY tool —
  # match a top-of-line assignment with double, single, or no quotes around a
  # version digit.
  run grep -E "^(RUFF|ACTIONLINT|YAMLLINT|LEFTHOOK|PRETTIER|MARKDOWNLINT_CLI|SHELLCHECK|SHELLCHECK_PY)_VERSION=['\"]?[0-9]" "$PROVISION"
  [ "$status" -ne 0 ]
}

@test "setup-dev-env resilience fallbacks match the shared file (cannot drift)" {
  for var in $PINS; do
    want="$(bash -c ". '$VERSIONS' && printf '%s' \"\$$var\"")"
    # The fallback value from setup-dev-env's `: "${VAR:=x}"` line.
    fb="$(sed -n "s/.*${var}:=\([0-9.]*\).*/\1/p" "$SETUP" | head -1)"
    [ -n "$fb" ]
    [ "$fb" = "$want" ]
  done
}

@test "env/setup.sh lefthook fallback matches the shared file (cannot drift)" {
  # The cloud-snapshot builder sources the shared file but keeps a last-resort
  # `: "${LEFTHOOK_VERSION:=x}"` fallback — guard it against the stale-2.1.6
  # drift this PR fixed.
  want="$(bash -c ". '$VERSIONS' && printf '%s' \"\$LEFTHOOK_VERSION\"")"
  fb="$(sed -n 's/.*LEFTHOOK_VERSION:=\([0-9.]*\).*/\1/p' "$ENV_SETUP" | head -1)"
  [ -n "$fb" ]
  [ "$fb" = "$want" ]
}

@test "gate_version_matches: true when the binary reports the pin" {
  tmp="$(mktemp -d)"
  printf '#!/usr/bin/env bash\necho "footool 1.2.3"\n' > "$tmp/footool"
  chmod +x "$tmp/footool"
  run bash -c "PATH=\"$tmp:\$PATH\"; . '$VERSIONS'; gate_version_matches footool 1.2.3"
  [ "$status" -eq 0 ]
  rm -rf "$tmp"
}

@test "gate_version_matches: false when the binary reports a different version" {
  tmp="$(mktemp -d)"
  printf '#!/usr/bin/env bash\necho "footool 1.2.3"\n' > "$tmp/footool"
  chmod +x "$tmp/footool"
  run bash -c "PATH=\"$tmp:\$PATH\"; . '$VERSIONS'; gate_version_matches footool 1.2.4"
  [ "$status" -ne 0 ]
  rm -rf "$tmp"
}

@test "gate_version_matches: false when the tool is absent" {
  run bash -c ". '$VERSIONS'; gate_version_matches definitely-not-a-tool-xyz 1.0.0"
  [ "$status" -ne 0 ]
}

@test "gate_version_matches: extracts the FIRST dotted token (shellcheck banner)" {
  # shellcheck --version prints a banner line with no digits, THEN `version: X`.
  tmp="$(mktemp -d)"
  printf '#!/usr/bin/env bash\nprintf "ShellCheck - shell script analysis tool\\nversion: 0.11.0\\n"\n' > "$tmp/shellcheck"
  chmod +x "$tmp/shellcheck"
  run bash -c "PATH=\"$tmp:\$PATH\"; . '$VERSIONS'; gate_version_matches shellcheck 0.11.0"
  [ "$status" -eq 0 ]
  rm -rf "$tmp"
}
