#!/usr/bin/env bats
# provision-gate-toolset.sh — the gate-toolset provisioning unit called by the
# arm-gate composite. Exercised fully OFFLINE: stub package managers (npm/pip/
# curl), a transparent `sudo`, and the curated real utils the script + the shared
# gate_version_matches helper need (id/bash/tr/grep/head). Every install logs its
# argv so we assert the WHAT (pinned, reconciled, OS-identical) without touching
# the network or the system.
#
# Contract under test (release#531): EVERY tool is pinned and RECONCILED to the
# pin — a present-but-wrong-version binary is reinstalled, not skipped — and the
# install path is IDENTICAL on macOS and Linux (shellcheck via the pip
# shellcheck-py wheel; actionlint via its pinned downloader; no apt/brew branch).

SCRIPT="$BATS_TEST_DIRNAME/../../bin-internal/provision-gate-toolset.sh"

setup() {
  WORK="$(mktemp -d)"
  cd "$WORK"
  mkdir -p stub realbin
  export LOG="$WORK/calls.log"
  : > "$LOG"

  # realbin/: symlinks to ONLY the genuine utilities the script + the shared
  # gate_version_matches helper invoke. The script runs under PATH=stub:realbin
  # via `env -i`, so a real gate tool preinstalled on the runner is NOT visible
  # to the script's checks — without this, "clean machine" assertions pass
  # locally but fail in CI. grep/head are what gate_version_matches uses to
  # extract a present tool's reported version. The test's OWN PATH stays normal.
  for u in id bash grep head; do ln -sf "$(command -v "$u")" "realbin/$u"; done

  # Logging stubs for the package managers + curl. Each records its argv.
  for tool in npm pip curl; do
    {
      echo '#!/usr/bin/env bash'
      echo "printf '${tool} %s\\n' \"\$*\" >> \"\$LOG\""
      # curl must emit nothing so the `| bash` consumer no-ops cleanly.
      echo 'exit 0'
    } > "stub/$tool"
    chmod +x "stub/$tool"
  done

  # Transparent sudo: log + exec the rest, so the wrapped command still runs.
  cat > stub/sudo <<'STUB'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "$LOG"
exec "$@"
STUB
  chmod +x stub/sudo
}

# The isolated PATH the SCRIPT runs under (stubs + the curated real utils only).
ISO_PATH() { printf '%s' "$WORK/stub:$WORK/realbin"; }

teardown() {
  cd /
  rm -rf "$WORK"
}

# Mark a gate tool present AT a reported version (so gate_version_matches can
# compare). A bare exit-0 stub reports NO version → always a miss → reconcile.
_present_at() {  # <toolname> <version-to-report>
  printf '#!/usr/bin/env bash\necho "%s %s"\n' "$1" "$2" > "stub/$1"
  chmod +x "stub/$1"
}

# Run the script under a clean, isolated env so only stubs + curated utils are
# visible (env -i drops the inherited PATH that would otherwise leak runner tools).
run_script() {
  run env -i \
    PATH="$(ISO_PATH)" LOG="$LOG" \
    RUFF_VERSION="${RUFF_VERSION:-}" ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-}" \
    bash "$SCRIPT"
}

# --------------------------------------------------------------------------
# clean machine — same on every OS (no apt/brew branch any more)
# --------------------------------------------------------------------------

@test "clean: npm batches all three at their pins (markdownlint -> markdownlint-cli)" {
  run_script
  [ "$status" -eq 0 ]
  grep -qE '^npm install -g lefthook@2\.1\.9 prettier@3\.8\.4 markdownlint-cli@0\.48\.0$' "$LOG"
}

@test "clean: pip installs pinned ruff + yamllint + shellcheck-py in one call" {
  run_script
  [ "$status" -eq 0 ]
  grep -qE '^pip install ruff==0\.15\.12 yamllint==1\.38\.0 shellcheck-py==0\.11\.0\.1$' "$LOG"
}

@test "clean: actionlint via the pinned downloader — no apt, no brew" {
  run_script
  [ "$status" -eq 0 ]
  grep -q 'raw.githubusercontent.com/rhysd/actionlint' "$LOG"   # the downloader
  grep -qE 'sudo bash -s -- 1\.7\.7 /usr/local/bin' "$LOG"      # pinned version + dir
  ! grep -q 'apt-get' "$LOG"                                    # shellcheck is pip now
  ! grep -q 'brew' "$LOG"                                       # no mac-only path
}

@test "no OS branch: shellcheck never comes from apt or brew" {
  run_script
  [ "$status" -eq 0 ]
  ! grep -qi 'apt-get install -y shellcheck' "$LOG"
  ! grep -qi 'brew install' "$LOG"
}

# --------------------------------------------------------------------------
# idempotency: every tool already AT its pin → nothing installed
# --------------------------------------------------------------------------

@test "all at pin: no npm, no pip, no actionlint download" {
  _present_at lefthook 2.1.9
  _present_at prettier 3.8.4
  _present_at markdownlint 0.48.0
  _present_at ruff 0.15.12
  _present_at yamllint 1.38.0
  _present_at shellcheck 0.11.0
  _present_at actionlint 1.7.7
  run_script
  [ "$status" -eq 0 ]
  ! grep -q '^npm' "$LOG"
  ! grep -q '^pip' "$LOG"
  ! grep -q 'curl' "$LOG"
}

# --------------------------------------------------------------------------
# RECONCILE: present-but-WRONG-version is reinstalled (the core fix)
# --------------------------------------------------------------------------

@test "drifted actionlint (wrong version) is reconciled to the pin" {
  _present_at actionlint 1.7.12   # floating brew version
  run_script
  [ "$status" -eq 0 ]
  grep -qE 'sudo bash -s -- 1\.7\.7 /usr/local/bin' "$LOG"   # reinstalled at pin
}

@test "drifted ruff (wrong version) re-triggers the pinned pip install" {
  _present_at ruff 0.14.0
  # keep the rest at pin so ONLY ruff's drift forces the pip call
  _present_at yamllint 1.38.0
  _present_at shellcheck 0.11.0
  run_script
  [ "$status" -eq 0 ]
  grep -qE '^pip install ruff==0\.15\.12 ' "$LOG"
}

@test "one drifted npm tool re-triggers the npm install (batched with its pin)" {
  _present_at lefthook 2.1.9
  _present_at prettier 3.0.0       # drifted
  _present_at markdownlint 0.48.0
  run_script
  [ "$status" -eq 0 ]
  grep -qE '^npm install -g prettier@3\.8\.4$' "$LOG"
}

# --------------------------------------------------------------------------
# pins are overridable via env
# --------------------------------------------------------------------------

@test "RUFF_VERSION override is honored in the pip install" {
  RUFF_VERSION=9.9.9 run_script
  [ "$status" -eq 0 ]
  grep -qE '^pip install ruff==9\.9\.9 ' "$LOG"
}

@test "empty RUFF_VERSION falls back to the shared-file pin (single source)" {
  RUFF_VERSION='' run_script
  [ "$status" -eq 0 ]
  grep -qE '^pip install ruff==0\.15\.12 ' "$LOG"
}

# --------------------------------------------------------------------------
# non-root + no sudo: actionlint's /usr/local/bin write needs escalation
# --------------------------------------------------------------------------

@test "non-root + no sudo + actionlint needed: errors up front" {
  rm -f stub/sudo            # `command -v sudo` now fails; test user is non-root
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"non-root without sudo"* ]]
}

@test "non-root + no sudo but actionlint already at pin: still succeeds" {
  rm -f stub/sudo
  _present_at actionlint 1.7.7
  run_script
  [ "$status" -eq 0 ]        # no actionlint install needed → guard not triggered
}
