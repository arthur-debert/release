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
  mkdir -p stub
  export LOG="$WORK/calls.log"
  : > "$LOG"

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

  # Isolate PATH to the stubs + coreutils ONLY — otherwise the dev machine's real
  # lefthook/shellcheck/actionlint (in /opt/homebrew/bin etc.) would be found by
  # `command -v` and (correctly) treated as already-present, defeating the
  # clean-machine assertions. /usr/bin + /bin give `id` and friends the script
  # needs; the gate tools live nowhere on this PATH unless a stub provides them.
  export PATH="$WORK/stub:/usr/bin:/bin"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# Mark a gate tool as already-present (an executable stub on PATH).
_present() { printf '#!/usr/bin/env bash\nexit 0\n' > "stub/$1"; chmod +x "stub/$1"; }

run_script() { run env UNAME_S="${1:-Linux}" bash "$SCRIPT"; }

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
  RUFF_VERSION=9.9.9 run env UNAME_S=Linux RUFF_VERSION=9.9.9 bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -qE 'ruff==9\.9\.9 yamllint' "$LOG"
}
