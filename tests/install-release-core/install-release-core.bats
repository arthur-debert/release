#!/usr/bin/env bats
# install-release-core — the pull-model boot resolver (bin/install-release-core).
#
# Exercises the resolution + install contract fully OFFLINE: a stub `gh` on PATH
# applies the script's REAL --jq expression to fixture release JSON (so the
# major-line filter, prerelease/draft exclusion, and exactly-one-wheel guard are
# tested for real, not faked), and a stub `python3` records the pip invocation so
# we can assert the --force-reinstall install model (deps resolved from PyPI; no
# --no-deps — release_core now declares real third-party deps).
#
# Requires `jq` (the stub applies the filter) — installed alongside bats in CI.

BIN="$BATS_TEST_DIRNAME/../../bin/install-release-core"

setup() {
  WORK="$(mktemp -d)"
  cd "$WORK"
  export FIXTURES="$WORK/fixtures"
  mkdir -p "$FIXTURES" stub

  # --- stub gh: route `gh api <path> [--jq EXPR]` to a fixture, applying EXPR
  # via real jq exactly as gh's bundled jq would. ----------------------------
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
[ "$1" = "api" ] || exit 0
shift
path=""; expr=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift 2 ;;
    --jq=*) expr="${1#--jq=}"; shift ;;
    -*) shift ;;
    *) path="$1"; shift ;;
  esac
done
case "$path" in
  *releases/latest) f="$FIXTURES/latest.json" ;;
  *releases*)       f="$FIXTURES/list.json" ;;
  *) echo "stub gh: unmapped path '$path'" >&2; exit 1 ;;
esac
[ -f "$f" ] || { echo "stub gh: missing fixture $f" >&2; exit 1; }
if [ -n "$expr" ]; then jq -r "$expr" < "$f"; else cat "$f"; fi
STUB
  chmod +x stub/gh

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

  _wheel() { echo "https://example.com/dl/$1/release_core-0.0.1-py3-none-any.whl"; }

  # latest.json — a single release object (what releases/latest returns).
  cat > "$FIXTURES/latest.json" <<EOF
{
  "tag_name": "v2.5.0",
  "draft": false,
  "prerelease": false,
  "assets": [
    {"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v2.5.0)"},
    {"name": "something-else.tar.gz", "browser_download_url": "https://example.com/x.tgz"}
  ]
}
EOF

  # list.json — releases array, NEWEST FIRST, spanning majors + a prerelease.
  # v3.0.0 is newest overall (so "latest" != "latest v2"); v2.6.0 is the newest
  # STABLE v2 with a wheel; v2.4.0-rc1 is a prerelease (must be skipped); the v4
  # line's newest is a prerelease so --major v4 must fall back to v4.0.0; v1.7.5
  # carries NO wheel.
  cat > "$FIXTURES/list.json" <<EOF
[
  {"tag_name": "v4.1.0-rc1", "draft": false, "prerelease": true,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v4.1.0-rc1)"}]},
  {"tag_name": "v4.0.0", "draft": false, "prerelease": false,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v4.0.0)"}]},
  {"tag_name": "v3.0.0", "draft": false, "prerelease": false,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v3.0.0)"}]},
  {"tag_name": "v2.6.0", "draft": false, "prerelease": false,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v2.6.0)"}]},
  {"tag_name": "v2.4.0-rc1", "draft": false, "prerelease": true,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v2.4.0-rc1)"}]},
  {"tag_name": "v2.5.0", "draft": false, "prerelease": false,
   "assets": [{"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "$(_wheel v2.5.0)"}]},
  {"tag_name": "v1.7.5", "draft": false, "prerelease": false,
   "assets": [{"name": "other.tar.gz", "browser_download_url": "https://example.com/o.tgz"}]}
]
EOF
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# --------------------------------------------------------------------------
# resolution: latest
# --------------------------------------------------------------------------

@test "latest: resolves the single wheel URL" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

# --------------------------------------------------------------------------
# resolution: major-line filter (the v3-safety guard)
# --------------------------------------------------------------------------

@test "--major v2: picks newest STABLE v2 even though v3 is newer overall" {
  run "$BIN" --major v2 --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v2.6.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "--major v3: resolves the v3 line" {
  run "$BIN" --major v3 --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v3.0.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "--major v4: skips the newer prerelease, falls back to the stable v4.0.0" {
  run "$BIN" --major v4 --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v4.0.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "--major v1: errors — no wheel in that major line" {
  run "$BIN" --major v1 --print-url
  [ "$status" -eq 1 ]
  [[ "$output" == *"no release in major line 'v1'"* ]]
}

@test "derived major: @vN thin callers pin the line — highest major wins (#551)" {
  # A consumer's checkout mixes lines for real (copilot-review stays @v1 while
  # the stack workflows ride @v2): the resolver must derive v2 and resolve the
  # v2 LINE's newest (v2.6.0, via list.json) — falling to releases/latest would
  # resolve the latest.json fixture (v2.5.0) instead, so the URLs distinguish
  # the two paths.
  mkdir -p .github/workflows
  cat > .github/workflows/ci.yml <<'Y'
jobs:
  check:
    uses: arthur-debert/release/.github/workflows/rust-ci.yml@v2
Y
  cat > .github/workflows/copilot-review.yml <<'Y'
jobs:
  request:
    uses: arthur-debert/release/.github/workflows/copilot-review.yml@v1
Y
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [[ "$output" == *"pinned to major line v2"* ]]
  [[ "$output" == *"https://example.com/dl/v2.6.0/release_core-0.0.1-py3-none-any.whl"* ]]
}

@test "derived major: explicit --major overrides the thin-caller derivation" {
  mkdir -p .github/workflows
  printf 'uses: arthur-debert/release/.github/workflows/rust-ci.yml@v2\n' \
    > .github/workflows/ci.yml
  run "$BIN" --major v3 --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v3.0.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "derived major: no thin callers → releases/latest (today's behavior)" {
  mkdir -p .github/workflows
  printf 'jobs: {build: {runs-on: ubuntu-latest}}\n' > .github/workflows/own.yml
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ "$output" = "https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "--major v2 anchoring: does NOT match v20 (no v20 releases)" {
  run "$BIN" --major v20 --print-url
  [ "$status" -eq 1 ]
  [[ "$output" == *"major line 'v20'"* ]]
}

# --------------------------------------------------------------------------
# exactly-one-wheel guard
# --------------------------------------------------------------------------

@test "zero wheel assets: errors" {
  cat > "$FIXTURES/latest.json" <<'EOF'
{"tag_name": "v2.5.0", "draft": false, "prerelease": false,
 "assets": [{"name": "notes.txt", "browser_download_url": "https://example.com/n.txt"}]}
EOF
  run "$BIN" --print-url
  [ "$status" -eq 1 ]
  [[ "$output" == *"no wheel asset"* ]]
}

@test "multiple wheel assets: errors (release packaging bug)" {
  cat > "$FIXTURES/latest.json" <<'EOF'
{"tag_name": "v2.5.0", "draft": false, "prerelease": false,
 "assets": [
   {"name": "release_core-0.0.1-py3-none-any.whl", "browser_download_url": "https://example.com/a.whl"},
   {"name": "release_core-0.0.2-py3-none-any.whl", "browser_download_url": "https://example.com/b.whl"}
 ]}
EOF
  run "$BIN" --print-url
  [ "$status" -eq 1 ]
  [[ "$output" == *"expected exactly 1 wheel asset, found 2"* ]]
}

# --------------------------------------------------------------------------
# install model: --force-reinstall, deps resolved (the static-version fix)
# --------------------------------------------------------------------------

@test "install: the venv's pip is invoked with --force-reinstall (no --no-deps) and the URL" {
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [ "$line" = "-m pip install --disable-pip-version-check --force-reinstall https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "install: builds a DEDICATED venv (never the user pip / site)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  # python3 was asked to build the tool venv, and the wheel was installed by that
  # venv's OWN python — not the host python3 directly (no `--user`, no user site).
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

@test "install: --print-url does NOT install (no venv, no pip call)" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ ! -s "$PIP_LOG" ]
  [ ! -e "$WORK/relcore/venv" ]
}

# --------------------------------------------------------------------------
# --from-source: install the local checkout's package (release CI), SAME
# isolated-venv machinery — no second install script.
# --------------------------------------------------------------------------

@test "from-source: installs the local PATH into the venv, NO release resolution (no gh)" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  # Make gh fail loudly: a passing run proves --from-source never resolves a release.
  printf '#!/usr/bin/env bash\necho "gh must not run in --from-source" >&2; exit 99\n' > stub/gh
  run "$BIN" --from-source "$WORK/src" --no-init
  [ "$status" -eq 0 ]
  [ "$(cat "$PIP_LOG")" = "-m pip install --disable-pip-version-check --force-reinstall $WORK/src" ]
}

@test "from-source: --print-url prints the local path" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  printf '#!/usr/bin/env bash\nexit 99\n' > stub/gh
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
  printf '#!/usr/bin/env bash\nexit 99\n' > stub/gh
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

@test "init: runs a bare release-core init (full materialize) by default after install" {
  run "$BIN"
  [ "$status" -eq 0 ]
  # #476 cutover: a bare `init` IS the full managed-tree materialize + auto-commit
  # on change. The resolver passes NO flags; auto-commit is the default. init is
  # invoked from the tool venv's own bin.
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

@test "install: --user is TOLERATED (accepted + ignored, never reaches pip)" {
  # The deployed pre-isolation SessionStart caller still passes --user; the
  # isolated-venv install must accept it (exit 0) and never forward it to pip.
  run "$BIN" --user
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--user"* ]]
  [ -x "$WORK/relcore/venv/bin/release-core" ]
}

@test "install: --user --break-system-packages both tolerated (ignored)" {
  run "$BIN" --user --break-system-packages
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--user"* ]]
  [[ "$(cat "$PIP_LOG")" != *"--break-system-packages"* ]]
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
