#!/usr/bin/env bats
# release-core shim — bin/release-core, the local-checkout CLI entry.
#
# Regression suite for release#497: release_core imports `click` (a real
# third-party dep since #457), and #487 moved click into an ISOLATED venv
# (no longer the --user site), so a bare `python3` running this shim can't
# import it → the CLI was dead with a ModuleNotFoundError. The shim now
# re-execs under the isolated venv's python (which carries click), keeping
# PYTHONPATH pinned at the checkout so it still runs the IN-CHECKOUT code; if no
# venv exists it prints an actionable hint instead of a raw traceback.
#
# Hermetic + offline: the "no click" interpreter is the real python3 run with
# `-S` (skips site-packages, so third-party click is unimportable regardless of
# the host), and the isolated venv is a recording bash STUB at
# $RELEASE_CORE_HOME/venv/bin/python — so we assert the re-exec argv/PYTHONPATH
# without needing a second real interpreter.

SHIM="$BATS_TEST_DIRNAME/../../bin/release-core"
LIB_REL="templates/commons/lib/release_core"

# A python3 guaranteed to lack click: -S skips site-packages entirely.
NOCLICK=(python3 -S)

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/rcshim.XXXXXX")"

  # Isolate the venv the shim borrows click from, under WORK (teardown removes).
  export RELEASE_CORE_HOME="$WORK/relcore"
  export REEXEC_LOG="$WORK/reexec.log"
  : > "$REEXEC_LOG"

  # A fake `click` package for the "click is importable here" case.
  mkdir -p "$WORK/fakeclick/click"
  : > "$WORK/fakeclick/click/__init__.py"

  # Don't let an ambient install/override leak in.
  unset XDG_DATA_HOME _RELEASE_CORE_REEXEC PYTHONPATH
}

teardown() {
  rm -rf "$WORK"
}

# Install a recording stub as the isolated venv's python: it logs the argv it
# was re-exec'd with (and the PYTHONPATH / one-shot guard env) and exits 0, so a
# re-exec is observable without a real second interpreter.
_install_venv_stub() {
  mkdir -p "$RELEASE_CORE_HOME/venv/bin"
  cat > "$RELEASE_CORE_HOME/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
{
  echo "ARGV: $*"
  echo "PYTHONPATH: ${PYTHONPATH:-<unset>}"
  echo "REEXEC: ${_RELEASE_CORE_REEXEC:-<unset>}"
} >> "$REEXEC_LOG"
exit 0
STUB
  chmod +x "$RELEASE_CORE_HOME/venv/bin/python"
}

@test "click missing + venv present → re-execs under the venv python" {
  _install_venv_stub
  run "${NOCLICK[@]}" "$SHIM" admin --help
  [ "$status" -eq 0 ]                       # the stub exits 0
  # The stub recorded the re-exec: it was handed the shim path + the user args.
  grep -q "ARGV: .*bin/release-core admin --help" "$REEXEC_LOG"
  # The one-shot loop guard was set for the child.
  grep -q "REEXEC: 1" "$REEXEC_LOG"
}

@test "re-exec pins the checkout lib first on PYTHONPATH (runs in-checkout code, not the wheel)" {
  _install_venv_stub
  run "${NOCLICK[@]}" "$SHIM" --version
  [ "$status" -eq 0 ]
  # PYTHONPATH's FIRST colon-delimited segment is the checkout's release_core
  # lib dir — `[^:]*` forbids any earlier segment, so a regressed ordering (lib
  # appended after a pre-existing entry) fails the test instead of passing on a
  # bare "appears somewhere" match.
  grep -q "PYTHONPATH: [^:]*${LIB_REL}" "$REEXEC_LOG"
}

@test "click missing + NO venv → actionable hint, exit 1 (no raw traceback)" {
  # RELEASE_CORE_HOME has no venv/bin/python → nothing to re-exec into.
  run "${NOCLICK[@]}" "$SHIM" --help
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing dependency 'click'"* ]]
  [[ "$output" == *"install-release-core"* ]]
  # It is the hint, not a Python traceback.
  [[ "$output" != *"Traceback"* ]]
}

@test "click importable here → does NOT re-exec (the fast/unchanged path)" {
  _install_venv_stub
  # Make `import click` succeed in the first interpreter via a fake click pkg.
  run env PYTHONPATH="$WORK/fakeclick" "${NOCLICK[@]}" "$SHIM" --version
  # We only care that NO re-exec happened — click was already importable, so the
  # shim never touched the venv. (cli_entry's real-click usage on the stub pkg
  # may fail downstream; that is irrelevant to the re-exec decision.)
  [ ! -s "$REEXEC_LOG" ]
}

@test "loop guard: already re-exec'd + click still missing → no second re-exec" {
  _install_venv_stub
  run env _RELEASE_CORE_REEXEC=1 "${NOCLICK[@]}" "$SHIM" --help
  # The guard short-circuits before re-exec; the venv stub is never invoked.
  [ ! -s "$REEXEC_LOG" ]
  # Falls through to the actionable hint.
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing dependency 'click'"* ]]
}
