#!/usr/bin/env bats
# provision-gate-toolset.sh — the gate-toolset provisioning unit called by the
# arm-gate composite. Exercised fully OFFLINE: stub package managers (npm/pip/
# curl), a transparent `sudo`, and the curated real utils the script + the shared
# gate_version_matches helper need (id/bash/grep/head). Every install logs its
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
  mkdir -p stub realbin localbin sysbin
  export LOG="$WORK/calls.log"
  : > "$LOG"
  # localbin = the yq install dir (YQ_INSTALL_DIR), models /usr/local/bin.
  # sysbin   = LAST on PATH, models /usr/bin where a python-yq stub squats — so an
  # install into localbin genuinely SHADOWS it, exactly as /usr/local/bin precedes
  # /usr/bin in production. Neither ever touches the real system path.

  # realbin/: symlinks to ONLY the genuine utilities the script + the shared
  # gate_version_matches helper invoke. The script runs under PATH=stub:realbin
  # via `env -i`, so a real gate tool preinstalled on the runner is NOT visible
  # to the script's checks — without this, "clean machine" assertions pass
  # locally but fail in CI. grep/head are what gate_version_matches uses to
  # extract a present tool's reported version. The test's OWN PATH stays normal.
  # mktemp/rm/chmod/mv back the yq install (download to a temp file, install only
  # if non-empty); under the empty-output curl stub the temp stays empty so the
  # chmod/mv never fire, but the utils must still resolve on the isolated PATH.
  for u in id bash grep head mktemp rm chmod mv uname; do ln -sf "$(command -v "$u")" "realbin/$u"; done

  # Logging stubs for the package managers. Each records its argv.
  for tool in npm pip; do
    {
      echo '#!/usr/bin/env bash'
      echo "printf '${tool} %s\\n' \"\$*\" >> \"\$LOG\""
      echo 'exit 0'
    } > "stub/$tool"
    chmod +x "stub/$tool"
  done

  # curl stub: logs argv, and for the yq release asset (a `-o <file>` download)
  # writes a working fake mikefarah yq into the target so the install path
  # (download → chmod → mv → re-check) completes hermetically. For the actionlint
  # downloader (`curl | bash`, no -o) it emits nothing so the `| bash` no-ops.
  # YQ_DL_EMPTY=1 simulates a failed/empty download (writes nothing) so the
  # hard-gate fail-fast can be exercised.
  cat > stub/curl <<'STUB'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$LOG"
_out=""; _prev=""
for a in "$@"; do [ "$_prev" = "-o" ] && _out="$a"; _prev="$a"; done
if [ -n "$_out" ] && printf '%s\n' "$*" | grep -q 'mikefarah/yq' && [ -z "${YQ_DL_EMPTY:-}" ]; then
  printf '#!/usr/bin/env bash\necho "yq (https://github.com/mikefarah/yq/) version v%s"\n' "${YQ_FAKE_VERSION:-4.44.3}" > "$_out"
fi
exit 0
STUB
  chmod +x stub/curl

  # Transparent sudo: log + exec the rest, so the wrapped command still runs.
  cat > stub/sudo <<'STUB'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "$LOG"
exec "$@"
STUB
  chmod +x stub/sudo
}

# The isolated PATH the SCRIPT runs under (stubs + curated real utils + the yq
# install dir + the squatter dir last). localbin precedes sysbin so a freshly
# installed yq shadows a python-yq in sysbin, mirroring /usr/local/bin > /usr/bin.
ISO_PATH() { printf '%s' "$WORK/stub:$WORK/realbin:$WORK/localbin:$WORK/sysbin"; }

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
    PATH="$(ISO_PATH)" LOG="$LOG" YQ_INSTALL_DIR="$WORK/localbin" \
    YQ_DL_EMPTY="${YQ_DL_EMPTY:-}" \
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
  _present_at yq 4.44.3
  run_script
  [ "$status" -eq 0 ]
  ! grep -q '^npm' "$LOG"
  ! grep -q '^pip' "$LOG"
  ! grep -q 'curl' "$LOG"
}

@test "yq: pinned mikefarah binary downloaded to /usr/local/bin (reconciled)" {
  # No yq present (and the bare exit-0 default stub reports no version) → miss →
  # download at the pin. The OS/arch-resolved release asset URL is what's logged.
  run_script
  [ "$status" -eq 0 ]
  grep -qE 'curl .*github.com/mikefarah/yq/releases/download/v4\.44\.3/yq_(linux|darwin)_(amd64|arm64)' "$LOG"
}

@test "yq: drifted python-yq stub (3.x) is reconciled to the mikefarah pin" {
  # kislyuk python-yq squatting /usr/bin (sysbin = last on PATH). The pinned
  # mikefarah install into localbin (before sysbin) must shadow it.
  printf '#!/usr/bin/env bash\necho "yq 3.1.0"\n' > "$WORK/sysbin/yq"; chmod +x "$WORK/sysbin/yq"
  run_script
  [ "$status" -eq 0 ]
  grep -qE 'github.com/mikefarah/yq/releases/download/v4\.44\.3/' "$LOG"
  # post-install the install-dir yq reports mikefarah at the pin → re-check passes
  "$WORK/localbin/yq" --version | grep -q 'mikefarah'
}

@test "yq: empty/failed download fails fast (hard gate, not silent success)" {
  # curl logs but writes nothing → temp stays empty → no install → the post-install
  # re-check finds yq still missing and the provisioner exits non-zero.
  YQ_DL_EMPTY=1 run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"still not at 4.44.3"* ]]
  [ ! -e "$WORK/localbin/yq" ]
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
  _present_at yq 4.44.3      # yq also needs /usr/local/bin → same escalation guard
  run_script
  [ "$status" -eq 0 ]        # no actionlint/yq install needed → guard not triggered
}

@test "non-root + no sudo + yq needed: errors up front (same as actionlint)" {
  rm -f stub/sudo
  _present_at actionlint 1.7.7   # actionlint satisfied so the guard fires for yq
  run_script
  [ "$status" -eq 1 ]
  [[ "$output" == *"non-root without sudo"* ]]
}
