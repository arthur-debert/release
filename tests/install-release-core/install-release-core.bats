#!/usr/bin/env bats
# install-release-core — the pull-model boot resolver (bin/install-release-core).
#
# Exercises the resolution + install contract fully OFFLINE. WS8 (#765) made the
# install INDEX-ONLY (the transitional GitHub-release-asset fallback was removed
# once the fleet converged):
#   - The default install runs `pip install --extra-index-url <index> '<constraint>'`.
#     A stub `python3` builds a fake venv whose `python` records every pip
#     invocation to PIP_LOG; with $PIP_FAIL_INDEX set it fails the index install so
#     the FAIL-LOUD path is exercised.
#   - --from-source installs the LOCAL tree (release CI), unchanged.
#   - The major line comes from .release.major.txt (the committed single source);
#     no @vN-derive fallback (the whole fleet carries the file now).

BIN="$BATS_TEST_DIRNAME/../../bin/install-release-core"

setup() {
  WORK="$(mktemp -d)"
  cd "$WORK"
  mkdir -p stub

  # --- stub python3: the interpreter used to BUILD the isolated tool venv.
  # On `-m venv <dir>` it scaffolds a fake venv whose `python` records pip args
  # (PIP_LOG) and whose console-scripts record their invocation (INIT_LOG, honors
  # $RELEASE_CORE_RC for a simulated init failure) — so the install model + the
  # folded-in init are assertable without a real venv. Other calls go to PY_LOG.
  cat > stub/python3 <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PY_LOG"
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  d="$3"; mkdir -p "$d/bin"
  cat > "$d/bin/python" <<'INNER'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIP_LOG"
# Simulate a failing index install when $PIP_FAIL_INDEX is set (the FAIL-LOUD path).
if [ -n "${PIP_FAIL_INDEX:-}" ]; then
  case "$*" in *--extra-index-url*) exit 1 ;; esac
fi
exit 0
INNER
  chmod +x "$d/bin/python"
  for s in release-core changelog changelog-render changelog-add changelog-cut semver; do
    cat > "$d/bin/$s" <<'INNER'
#!/usr/bin/env bash
printf '%s\n' "$(basename "$0") $*" >> "$INIT_LOG"
exit ${RELEASE_CORE_RC:-0}
INNER
    chmod +x "$d/bin/$s"
  done
fi
exit 0
STUB
  chmod +x stub/python3
  export PY_LOG="$WORK/py.log";   : > "$PY_LOG"
  export PIP_LOG="$WORK/pip.log"; : > "$PIP_LOG"
  export INIT_LOG="$WORK/init.log"; : > "$INIT_LOG"

  # Isolate the tool venv + the script-symlink dir under WORK (teardown removes).
  export RELEASE_CORE_HOME="$WORK/relcore"
  export XDG_BIN_HOME="$WORK/binhome"

  export PATH="$WORK/stub:$PATH"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# --------------------------------------------------------------------------
# --print-url: the index constraint (resolve-only)
# --------------------------------------------------------------------------

@test "print-url: no major source → the bare 'release-core' constraint" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "release-core" ]
}

@test "print-url: .release.major.txt becomes the version constraint" {
  echo 3 > .release.major.txt
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "release-core>=3,<4" ]
}

@test "print-url: --major overrides the file" {
  echo 3 > .release.major.txt
  run "$BIN" --major v2 --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "release-core>=2,<3" ]
}

@test "print-url: does NOT install (no venv, no pip call)" {
  echo 3 > .release.major.txt
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ ! -s "$PIP_LOG" ]
  [ ! -e "$WORK/relcore/venv" ]
}

# --------------------------------------------------------------------------
# install model: INDEX-ONLY (WS8 #765)
# --------------------------------------------------------------------------

@test "install: INDEX path — pip installs the bare constraint from the extra index (no --force-reinstall)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [[ "$line" == *"--extra-index-url https://pypi.magik.works/simple/ release-core"* ]]
  # The version-comparing index path does NOT force-reinstall.
  [[ "$line" != *"--force-reinstall"* ]]
}

@test "install: INDEX path — .release.major.txt becomes the version constraint" {
  echo 3 > .release.major.txt
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [[ "$line" == *"release-core>=3,<4"* ]]
  [[ "$output" == *"pinned to major line v3 (from .release.major.txt)"* ]]
}

@test "install: a blank .release.major.txt → no constraint (no derive fallback)" {
  printf '   \n' > .release.major.txt
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [[ "$line" == *"--extra-index-url https://pypi.magik.works/simple/ release-core"* ]]
  [[ "$line" != *">="* ]]
}

@test "install: builds a DEDICATED venv (never the user pip / site)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PY_LOG")" == *"-m venv $WORK/relcore/venv"* ]]
  [ -x "$WORK/relcore/venv/bin/release-core" ]
}

@test "install: console-scripts are symlinked onto PATH (BIN_DIR)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  for s in release-core changelog changelog-render changelog-add changelog-cut semver; do
    [ -L "$WORK/binhome/$s" ] || { echo "missing symlink: $s"; false; }
    [ "$(readlink "$WORK/binhome/$s")" = "$WORK/relcore/venv/bin/$s" ]
  done
}

@test "install: persists BIN_DIR to \$GITHUB_PATH under GitHub Actions (no YAML scripting needed)" {
  export GITHUB_PATH="$WORK/gh_path"; : > "$GITHUB_PATH"
  run "$BIN" --no-init
  [ "$status" -eq 0 ]
  [ "$(cat "$GITHUB_PATH")" = "$WORK/binhome" ]
}

@test "install: leaves \$GITHUB_PATH alone when not in GitHub Actions" {
  run "$BIN" --no-init
  [ "$status" -eq 0 ]
  [ ! -e "$WORK/gh_path" ]
}

@test "install: pip is NOT invoked with --no-deps (deps must resolve)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--no-deps"* ]]
}

@test "install: no stray pip flags (clean isolated invocation)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--user"* ]]
  [[ "$(cat "$PIP_LOG")" != *"--break-system-packages"* ]]
}

@test "install: honors \$PYTHON override as the venv builder" {
  cat > stub/my-python <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PY_LOG"
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  d="$3"; mkdir -p "$d/bin"
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$PIP_LOG"\n' > "$d/bin/python"
  printf '#!/usr/bin/env bash\nprintf "release-core %%s\\n" "$*" >> "$INIT_LOG"\n' > "$d/bin/release-core"
  chmod +x "$d/bin/python" "$d/bin/release-core"
fi
exit 0
STUB
  chmod +x stub/my-python
  PYTHON="$WORK/stub/my-python" run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PY_LOG")" == *"-m venv $WORK/relcore/venv"* ]]
}

# --------------------------------------------------------------------------
# WS6/F (#763): fail-loud boot — a failing index pull HALTS LOUDLY after a
# bounded transient-retry; a transient blip retries then proceeds.
# --------------------------------------------------------------------------

@test "fail-loud: an unresolvable index install HALTS non-zero with a clear message" {
  PIP_FAIL_INDEX=1 RETRY_SLEEP=0 run "$BIN" --major v2
  [ "$status" -ne 0 ]
  [[ "$output" == *"FATAL"* ]]
  [[ "$output" == *"no wheel means no gate"* ]]
  # init never ran — there was no wheel to install.
  [ ! -s "$INIT_LOG" ]
}

@test "fail-loud: the index pull is RETRIED (bounded) before halting" {
  PIP_FAIL_INDEX=1 RETRY_ATTEMPTS=3 RETRY_SLEEP=0 run "$BIN" --major v2
  [ "$status" -ne 0 ]
  [[ "$output" == *"attempt 1/3 failed"* ]]
  [[ "$output" == *"attempt 2/3 failed"* ]]
  [[ "$output" == *"FATAL"* ]]
}

@test "fail-loud: a transient index blip (fail once, then succeed) RETRIES and proceeds" {
  # A python stub whose pip --extra-index-url fails ONLY on the first attempt
  # (tracked via a counter file), succeeding on the retry — proves the bounded
  # retry recovers a 1-second hiccup instead of red-alerting the session.
  cat > stub/python3 <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PY_LOG"
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  d="$3"; mkdir -p "$d/bin"
  cat > "$d/bin/python" <<INNER
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$PIP_LOG"
case "\$*" in
  *--extra-index-url*)
    _c="${PIP_LOG}.idx_attempts"
    _n=\$(( \$(cat "\$_c" 2>/dev/null || echo 0) + 1 ))
    echo "\$_n" > "\$_c"
    [ "\$_n" -lt 2 ] && exit 1   # fail the FIRST attempt only
    ;;
esac
exit 0
INNER
  chmod +x "$d/bin/python"
  for s in release-core changelog changelog-render changelog-add changelog-cut semver; do
    cat > "$d/bin/$s" <<INNER
#!/usr/bin/env bash
printf '%s\n' "\$(basename "\$0") \$*" >> "$INIT_LOG"
exit \${RELEASE_CORE_RC:-0}
INNER
    chmod +x "$d/bin/$s"
  done
fi
exit 0
STUB
  chmod +x stub/python3
  RETRY_ATTEMPTS=3 RETRY_SLEEP=0 run "$BIN" --major v2
  [ "$status" -eq 0 ]
  [[ "$output" == *"attempt 1/3 failed"* ]]
}

# --------------------------------------------------------------------------
# --from-source: install the local checkout's package (release CI), SAME
# isolated-venv machinery — no second install script.
# --------------------------------------------------------------------------

@test "from-source: installs the local PATH into the venv, NO release resolution" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  run "$BIN" --from-source "$WORK/src" --no-init
  [ "$status" -eq 0 ]
  [ "$(cat "$PIP_LOG")" = "-m pip install --disable-pip-version-check --force-reinstall $WORK/src" ]
}

@test "from-source: --print-url prints the local path" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  run "$BIN" --from-source "$WORK/src" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "$WORK/src" ]
}

@test "from-source: a checkout ROOT descends to the nested package dir (#516)" {
  # The repo root carries a uv-workspace pyproject.toml pip cannot build; the
  # resolver must descend to templates/commons/lib/release_core automatically.
  mkdir -p "$WORK/checkout/templates/commons/lib/release_core"
  : > "$WORK/checkout/pyproject.toml"  # the unbuildable workspace root
  : > "$WORK/checkout/templates/commons/lib/release_core/pyproject.toml"
  run "$BIN" --from-source "$WORK/checkout" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "$WORK/checkout/templates/commons/lib/release_core" ]
}

@test "from-source: a path without pyproject.toml errors" {
  mkdir -p "$WORK/empty"
  run "$BIN" --from-source "$WORK/empty" --no-init
  [ "$status" -ne 0 ]
  [[ "$output" == *"no pyproject.toml"* ]]
}

@test "from-source: still exposes console-scripts + persists GITHUB_PATH (same machinery)" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  export GITHUB_PATH="$WORK/gh_path"; : > "$GITHUB_PATH"
  run "$BIN" --from-source "$WORK/src" --no-init
  [ "$status" -eq 0 ]
  [ -L "$WORK/binhome/release-core" ]
  [ "$(cat "$GITHUB_PATH")" = "$WORK/binhome" ]
}

# --------------------------------------------------------------------------
# folded-in init: install-release-core runs `release-core init` by default
# --------------------------------------------------------------------------

@test "init: runs a bare release-core init (full install) by default after install" {
  run "$BIN"
  [ "$status" -eq 0 ]
  # #476 cutover: a bare `init` IS the full managed-file install + auto-commit
  # on change. The resolver passes NO flags; auto-commit is the default.
  [ "$(cat "$INIT_LOG")" = "release-core init" ]
}

@test "init: --no-init installs but does NOT run init" {
  run "$BIN" --no-init
  [ "$status" -eq 0 ]
  [ -s "$PIP_LOG" ]        # install still happened
  [ ! -s "$INIT_LOG" ]     # but init did not
  [[ "$output" == *"skipping release-core init"* ]]
}

@test "init: --print-url neither installs nor inits" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ ! -s "$PIP_LOG" ]
  [ ! -s "$INIT_LOG" ]
}

@test "init: failure is best-effort — does NOT fail the resolver" {
  RELEASE_CORE_RC=1 run "$BIN"
  [ "$status" -eq 0 ]                                # install succeeded; init failure tolerated
  [ "$(cat "$INIT_LOG")" = "release-core init" ]     # init was attempted
  [[ "$output" == *"release-core init failed"* ]]
}

# --------------------------------------------------------------------------
# WS5/E (#762): --cloud forwards to `release-core init --cloud` (cloud-only
# provisioning: tag fetch, dep caches, NSS cert import). WS8 #765: also auto-set
# from $CLAUDE_CODE_REMOTE=true (the SessionStart shell that used to detect it is gone).
# --------------------------------------------------------------------------

@test "init: --cloud forwards --cloud to release-core init" {
  run "$BIN" --cloud
  [ "$status" -eq 0 ]
  [ "$(cat "$INIT_LOG")" = "release-core init --cloud" ]
}

@test "init: \$CLAUDE_CODE_REMOTE=true auto-forwards --cloud (no explicit flag)" {
  CLAUDE_CODE_REMOTE=true run "$BIN"
  [ "$status" -eq 0 ]
  [ "$(cat "$INIT_LOG")" = "release-core init --cloud" ]
}

@test "init: without --cloud (local) passes NO cloud flag" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [ "$(cat "$INIT_LOG")" = "release-core init" ]
}

@test "init: --cloud + --no-init still skips init (no cloud provisioning either)" {
  run "$BIN" --cloud --no-init
  [ "$status" -eq 0 ]
  [ ! -s "$INIT_LOG" ]
}

# --------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------

@test "--help: exit 0, prints usage" {
  run "$BIN" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: install-release-core"* ]]
}

@test "unknown flag: usage error (exit 64)" {
  run "$BIN" --nope
  [ "$status" -eq 64 ]
}

@test "--major without a value: usage error (exit 64)" {
  run "$BIN" --major
  [ "$status" -eq 64 ]
}

@test "tolerated no-ops: stale-resolver flags (--repo/--commit/--user/...) do not error" {
  # A not-yet-re-pulled consumer's settings hook or an old CI step may still pass
  # these — accept + ignore rather than exit 64 (which would break the very boot
  # that updates the caller).
  run "$BIN" --repo arthur-debert/release --commit --force --user --break-system-packages --no-init
  [ "$status" -eq 0 ]
}
