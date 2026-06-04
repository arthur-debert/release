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

  # --- stub python3: record the pip args so the install model is assertable. -
  cat > stub/python3 <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIP_LOG"
exit 0
STUB
  chmod +x stub/python3
  export PIP_LOG="$WORK/pip.log"
  : > "$PIP_LOG"

  # --- stub release-core: record `init` so the folded-in init is assertable.
  # Found via `command -v` (step 2 of the resolver's _find_release_core). Honors
  # $RELEASE_CORE_RC so a test can simulate an init failure.
  cat > stub/release-core <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$INIT_LOG"
exit "${RELEASE_CORE_RC:-0}"
STUB
  chmod +x stub/release-core
  export INIT_LOG="$WORK/init.log"
  : > "$INIT_LOG"

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

@test "install: pip is invoked with --force-reinstall (no --no-deps) and the URL" {
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [ "$line" = "-m pip install --force-reinstall https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "install: pip is NOT invoked with --no-deps (deps must resolve)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--no-deps"* ]]
}

@test "install: --print-url does NOT install (no pip call)" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ ! -s "$PIP_LOG" ]
}

# --------------------------------------------------------------------------
# folded-in init: install-release-core runs `release-core init` by default
# --------------------------------------------------------------------------

@test "init: runs release-core init --commit by default after install" {
  run "$BIN"
  [ "$status" -eq 0 ]
  # --commit auto-commits ONLY the generated managed config (pull-model
  # commit-hygiene seam); the resolver opts in at SessionStart.
  [ "$(cat "$INIT_LOG")" = "init --commit" ]
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

@test "init: bare \$PYTHON resolves to its real dir, not a stray ./release-core in cwd" {
  # Regression: dirname of a bare `python3` is `.`, which would (mis)pick an
  # unrelated ./release-core in the repo root. The resolver must resolve $PYTHON
  # to its absolute path first and use the interpreter-adjacent script.
  cat > release-core <<'STUB'
#!/usr/bin/env bash
printf 'WRONG-CWD-BINARY\n' >> "$INIT_LOG"
STUB
  chmod +x release-core
  run "$BIN"
  [ "$status" -eq 0 ]
  [ "$(cat "$INIT_LOG")" = "init --commit" ]  # the stub-dir release-core ran
  [[ "$(cat "$INIT_LOG")" != *"WRONG-CWD-BINARY"* ]]
}

@test "init: failure is best-effort — does NOT fail the resolver" {
  RELEASE_CORE_RC=1 run "$BIN"
  [ "$status" -eq 0 ]                       # install succeeded; init failure tolerated
  [ "$(cat "$INIT_LOG")" = "init --commit" ]  # init was attempted
  [[ "$output" == *"release-core init failed"* ]]
}

@test "install: --user is passed through to pip" {
  run "$BIN" --user
  [ "$status" -eq 0 ]
  [ "$(cat "$PIP_LOG")" = "-m pip install --force-reinstall --user https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "install: --user --break-system-packages both passed through, in order" {
  run "$BIN" --user --break-system-packages
  [ "$status" -eq 0 ]
  [ "$(cat "$PIP_LOG")" = "-m pip install --force-reinstall --user --break-system-packages https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl" ]
}

@test "install: no extra pip flags by default (clean invocation)" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" != *"--user"* ]]
  [[ "$(cat "$PIP_LOG")" != *"--break-system-packages"* ]]
}

@test "install: honors \$PYTHON override" {
  cat > stub/my-python <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIP_LOG"
exit 0
STUB
  chmod +x stub/my-python
  PYTHON="$WORK/stub/my-python" run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" == "-m pip install --force-reinstall "* ]]
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
