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

# ── release#747 — children inherit the checkout lib on PYTHONPATH ─────────────
# Fleet verbs (verify / canary run / cut) shell out to `<interpreter> -m
# release_core` (_SELF_CLI). The dev shim only put the lib on the IN-PROCESS
# sys.path, so a freshly-spawned child died "No module named release_core". The
# shim now EXPORTS PYTHONPATH (lib first), so any child inherits it.

@test "747: the shim exports PYTHONPATH (lib first) into a spawned child's env" {
  # The venv-stub recorder logs the env of the child the shim spawns. The shim
  # exports PYTHONPATH at module load (release#747), so the re-exec child must
  # inherit it leading with the checkout lib — the exact thing a `-m
  # release_core` fleet child needs to import the package.
  _install_venv_stub
  run "${NOCLICK[@]}" "$SHIM" --version
  [ "$status" -eq 0 ]
  grep -q "PYTHONPATH: [^:]*${LIB_REL}" "$REEXEC_LOG"
}

@test "747+749: the shim exports RELEASE_HOME=<checkout> into a spawned child's env" {
  # The recorder also captures RELEASE_HOME: the shim defaults it to the checkout
  # root (release#749) so init has a template source AND fleet children inherit a
  # correct RELEASE_HOME instead of the wrong ~/release default.
  _install_venv_stub
  cat > "$RELEASE_CORE_HOME/venv/bin/python" <<'STUB'
#!/usr/bin/env bash
echo "RELEASE_HOME: ${RELEASE_HOME:-<unset>}" >> "$REEXEC_LOG"
exit 0
STUB
  chmod +x "$RELEASE_CORE_HOME/venv/bin/python"
  run env -u RELEASE_HOME "${NOCLICK[@]}" "$SHIM" --version
  [ "$status" -eq 0 ]
  # The checkout root is bin/.. — the recorded value must end with the repo dir,
  # never <unset>.
  local root
  root="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  grep -q "RELEASE_HOME: $root" "$REEXEC_LOG"
}

@test "747: a child python3 -m release_core fails WITHOUT the lib on PYTHONPATH (the bug)" {
  # Proves the export is load-bearing: strip PYTHONPATH and the bare -m import
  # fails exactly as the fleet verbs did before the fix.
  run env -u PYTHONPATH python3 -S -m release_core --version
  [ "$status" -ne 0 ]
  [[ "$output" == *"No module named release_core"* ]]
}

# ── release#749 — init from the dev shim with empty RELEASE_HOME ──────────────
# The in-checkout release_core has no _bundled_templates (wheel-only), so a
# bundle-less init died "no bundled templates and $RELEASE_HOME='' is not a git
# clone". The shim now defaults RELEASE_HOME to the checkout root (a git clone
# WITH templates/), so init's GitSource has a source to install from.

@test "749: release-core init from the dev shim works with empty RELEASE_HOME" {
  # Build a minimal rust-cli consumer so detect_kind resolves a Kind tree.
  local repo="$WORK/consumer"
  mkdir -p "$repo/src"
  cat > "$repo/Cargo.toml" <<'TOML'
[package]
name = "demo"
version = "0.1.0"
edition = "2021"
[[bin]]
name = "demo"
path = "src/main.rs"
TOML
  echo 'fn main(){}' > "$repo/src/main.rs"
  ( cd "$repo" && git init -q && git config user.email t@t.t && git config user.name t \
      && git add -A && git commit -qm init )

  # Run init via the dev shim with NO RELEASE_HOME (the bug condition). --no-commit
  # keeps it side-effect-light; the shim's default RELEASE_HOME=<checkout> supplies
  # the templates.
  run env -u RELEASE_HOME -C "$repo" "$SHIM" init --no-commit
  [ "$status" -eq 0 ]
  # It installed from the checkout (NOT the missing-bundle error path).
  [[ "$output" == *"managed-file change"* ]]
  [[ "$output" != *"no bundled templates"* ]]
}

@test "749: shim's default RELEASE_HOME does not override an explicit one" {
  # setdefault semantics: an explicit RELEASE_HOME must still win. Point it at a
  # non-clone and init must fail naming THAT path, proving the explicit value was
  # honored rather than silently replaced by the checkout default.
  local repo="$WORK/consumer2" home="$WORK/nothome"
  mkdir -p "$repo/src" "$home"
  cat > "$repo/Cargo.toml" <<'TOML'
[package]
name = "demo"
version = "0.1.0"
edition = "2021"
[[bin]]
name = "demo"
path = "src/main.rs"
TOML
  echo 'fn main(){}' > "$repo/src/main.rs"
  ( cd "$repo" && git init -q && git config user.email t@t.t && git config user.name t \
      && git add -A && git commit -qm init )
  run env -C "$repo" RELEASE_HOME="$home" "$SHIM" init --no-commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"$home"* ]]
}
