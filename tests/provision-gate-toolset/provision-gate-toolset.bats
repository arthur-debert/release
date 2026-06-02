#!/usr/bin/env bats
# provision-gate-toolset.sh — the gate-toolset provisioning unit called by the
# arm-gate composite. Exercised fully OFFLINE: stub package managers (npm/pip/
# brew/apt-get), a transparent `sudo`, a controllable `uname` (OS branch), and a
# logging `curl` record every install so we can assert the WHAT (batched, OS-
# branched, idempotent, pinned) without touching the network or the system.

SCRIPT="$BATS_TEST_DIRNAME/../../bin-internal/provision-gate-toolset.sh"

setup() {
  WORK="$(mktemp -d)"
  cd "$WORK"
  mkdir -p stub realbin
  export LOG="$WORK/calls.log"
  : > "$LOG"

  # realbin/: symlinks to ONLY the genuine utilities the script itself invokes
  # (id, bash). The script runs under PATH=stub:realbin via `env -i`, so a real
  # gate tool preinstalled on the runner (ubuntu-latest ships shellcheck in
  # /usr/bin!) is NOT visible to the script's `command -v` checks — without this,
  # "clean machine" assertions pass locally but fail in CI. The test's OWN PATH
  # stays normal (grep/rm/teardown unaffected).
  for u in id bash; do ln -sf "$(command -v "$u")" "realbin/$u"; done

  # Logging stubs for the package managers + curl. Each records its argv.
  for tool in npm pip brew apt-get curl; do
    {
      echo '#!/usr/bin/env bash'
      echo "printf '${tool} %s\\n' \"\$*\" >> \"\$LOG\""
      # curl must emit nothing so the `| bash` consumer no-ops cleanly.
      echo 'exit 0'
    } > "stub/$tool"
    chmod +x "stub/$tool"
  done

  # Transparent sudo: log + exec the rest, so the wrapped apt-get stub still runs.
  cat > stub/sudo <<'STUB'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "$LOG"
exec "$@"
STUB
  chmod +x stub/sudo

  # Controllable uname (default Linux); a test overrides UNAME_S.
  cat > stub/uname <<'STUB'
#!/usr/bin/env bash
[ "$1" = "-s" ] && { printf '%s\n' "${UNAME_S:-Linux}"; exit 0; }
printf '%s\n' "${UNAME_S:-Linux}"
STUB
  chmod +x stub/uname

}

# The isolated PATH the SCRIPT runs under (stubs + the curated real utils only).
ISO_PATH() { printf '%s' "$WORK/stub:$WORK/realbin"; }

teardown() {
  cd /
  rm -rf "$WORK"
}

# Mark a gate tool as already-present (an executable stub on the isolated PATH).
_present() { printf '#!/usr/bin/env bash\nexit 0\n' > "stub/$1"; chmod +x "stub/$1"; }

# Run the script under a clean, isolated env so only stubs + curated utils are
# visible (env -i drops the inherited PATH that would otherwise leak runner tools).
run_script() {
  run env -i \
    PATH="$(ISO_PATH)" LOG="$LOG" UNAME_S="${1:-Linux}" \
    RUFF_VERSION="${RUFF_VERSION:-}" ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-}" \
    bash "$SCRIPT"
}

# --------------------------------------------------------------------------
# Linux: clean machine
# --------------------------------------------------------------------------

@test "linux clean: npm batches all three (markdownlint -> markdownlint-cli)" {
  run_script Linux
  [ "$status" -eq 0 ]
  grep -qE '^npm install -g lefthook prettier markdownlint-cli$' "$LOG"
}

@test "linux clean: pip installs pinned ruff + yamllint in one call" {
  run_script Linux
  [ "$status" -eq 0 ]
  grep -qE 'ruff==0\.15\.12 yamllint' "$LOG"
}

@test "linux clean: shellcheck via apt (sudo), actionlint via pinned installer" {
  run_script Linux
  [ "$status" -eq 0 ]
  grep -q 'apt-get install -y shellcheck' "$LOG"     # apt path
  grep -q 'curl' "$LOG"                               # actionlint downloader
  ! grep -q 'brew' "$LOG"                             # NOT the mac path
}

# --------------------------------------------------------------------------
# macOS: brew batches both, no apt / no curl
# --------------------------------------------------------------------------

@test "darwin clean: brew installs shellcheck + actionlint in one call" {
  run_script Darwin
  [ "$status" -eq 0 ]
  grep -qE 'brew install shellcheck actionlint' "$LOG"
  ! grep -q 'apt-get' "$LOG"
  ! grep -q 'curl' "$LOG"          # actionlint comes from brew on mac, not download
}

# --------------------------------------------------------------------------
# idempotency: nothing missing
# --------------------------------------------------------------------------

@test "all present: no npm and no system installs (pip still enforces the pin)" {
  for t in lefthook prettier markdownlint ruff yamllint shellcheck actionlint; do _present "$t"; done
  run_script Linux
  [ "$status" -eq 0 ]
  ! grep -q '^npm' "$LOG"
  ! grep -q 'apt-get' "$LOG"
  ! grep -q 'brew' "$LOG"
  grep -qE 'ruff==0\.15\.12 yamllint' "$LOG"   # pip always runs (pin enforcement)
}

@test "partial: only the missing system tool is installed" {
  # shellcheck present, actionlint missing → apt NOT called, actionlint IS.
  _present shellcheck
  run_script Linux
  [ "$status" -eq 0 ]
  ! grep -q 'apt-get install -y shellcheck' "$LOG"
  grep -q 'curl' "$LOG"
}

# --------------------------------------------------------------------------
# pins are overridable via env
# --------------------------------------------------------------------------

@test "RUFF_VERSION override is honored" {
  RUFF_VERSION=9.9.9 run_script Linux
  [ "$status" -eq 0 ]
  grep -qE 'ruff==9\.9\.9 yamllint' "$LOG"
}

@test "empty RUFF_VERSION falls back to the script's own pin (single source)" {
  # The composite passes empty when not overridden; the script must use its pin.
  RUFF_VERSION='' run_script Linux
  [ "$status" -eq 0 ]
  grep -qE 'ruff==0\.15\.12 yamllint' "$LOG"
}

# --------------------------------------------------------------------------
# non-root + no sudo: fail fast with a clear message (Linux system installs)
# --------------------------------------------------------------------------

@test "linux non-root + no sudo + a missing tool: errors up front" {
  # Drop the sudo stub so `command -v sudo` fails; the test user is non-root.
  rm -f stub/sudo
  run_script Linux
  [ "$status" -eq 1 ]
  [[ "$output" == *"non-root without sudo"* ]]
}

@test "linux non-root + no sudo but nothing missing: still succeeds" {
  rm -f stub/sudo
  for t in shellcheck actionlint; do _present "$t"; done
  run_script Linux
  [ "$status" -eq 0 ]   # no system install needed → guard not triggered
}
