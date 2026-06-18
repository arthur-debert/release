#!/usr/bin/env bats
# install-release-core — the pull-model boot resolver (bin/install-release-core).
#
# Exercises the resolution + install contract fully OFFLINE. WS3 (#760) made the
# install INDEX-FIRST with an ASSET-PATH FALLBACK:
#   - The default install tries `pip install --extra-index-url <index> '<constraint>'`.
#     A stub `python3` builds a fake venv whose `python` records every pip
#     invocation to PIP_LOG; with $PIP_FAIL_INDEX set it fails the index install so
#     the fallback is exercised.
#   - The ASSET-PATH FALLBACK uses a stub `gh` that applies the script's REAL --jq
#     expression to fixture release JSON (so the major-line filter,
#     prerelease/draft exclusion, and exactly-one-wheel guard are tested for real),
#     installing the resolved wheel URL with --force-reinstall.
#   - --from-source installs the LOCAL tree (release CI), unchanged.
#   - The major line comes from .release.major.txt, else the @vN thin-caller grep.
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
# Simulate "the pip index has no usable wheel yet" (the Phase-1 reality: the index
# is empty until a real tag-stamped wheel ships) when $PIP_FAIL_INDEX is set — the
# INDEX install (the one passing --extra-index-url) fails, exercising the
# asset-path fallback. The asset-path install (a plain URL/path, no
# --extra-index-url) always succeeds.
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
# install model: INDEX-FIRST with an ASSET-PATH FALLBACK (WS3, #760)
# --------------------------------------------------------------------------

@test "install: INDEX path — pip installs the version constraint from the extra index (no --force-reinstall)" {
  # No major source → no constraint; default releases/latest is v2.5.0 but the
  # INDEX path does not resolve a URL — it hands pip 'release-core' + the index.
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [[ "$line" == *"--extra-index-url https://arthur-debert.github.io/release/simple/ release-core"* ]]
  # The version-comparing index path does NOT force-reinstall.
  [[ "$line" != *"--force-reinstall"* ]]
  # gh was never consulted — the index install succeeded.
}

@test "install: INDEX path — .release.major.txt becomes the version constraint" {
  echo 3 > .release.major.txt
  run "$BIN"
  [ "$status" -eq 0 ]
  line="$(cat "$PIP_LOG")"
  [[ "$line" == *"release-core>=3,<4"* ]]
  [[ "$output" == *"pinned to major line v3 (from .release.major.txt)"* ]]
}

@test "install: INDEX path — derives the constraint from @vN callers when .release.major.txt is absent" {
  mkdir -p .github/workflows
  printf 'uses: arthur-debert/release/.github/workflows/rust-ci.yml@v2\n' \
    > .github/workflows/ci.yml
  run "$BIN"
  [ "$status" -eq 0 ]
  [[ "$(cat "$PIP_LOG")" == *"release-core>=2,<3"* ]]
  [[ "$output" == *".release.major.txt absent"* ]]
}

@test "install: ASSET-PATH FALLBACK — when the index has no usable wheel, resolve + install the release asset with --force-reinstall" {
  PIP_FAIL_INDEX=1 run "$BIN"
  [ "$status" -eq 0 ]
  log="$(cat "$PIP_LOG")"
  # The index attempt is logged AND failed; the fallback installs the resolved
  # release-asset URL with --force-reinstall.
  [[ "$log" == *"--extra-index-url"* ]]
  [[ "$log" == *"--force-reinstall https://example.com/dl/v2.5.0/release_core-0.0.1-py3-none-any.whl"* ]]
  [[ "$output" == *"falling back to the GitHub release-asset path"* ]]
}

@test "install: ASSET-PATH FALLBACK — fails loudly when neither index nor asset resolves" {
  # Index fails AND the release has no wheel asset → hard error, nothing installed.
  cat > "$FIXTURES/latest.json" <<'EOF'
{"tag_name": "v2.5.0", "draft": false, "prerelease": false,
 "assets": [{"name": "notes.txt", "browser_download_url": "https://example.com/n.txt"}]}
EOF
  PIP_FAIL_INDEX=1 run "$BIN"
  [ "$status" -eq 1 ]
  [[ "$output" == *"neither the pip index nor the release-asset fallback"* ]]
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

@test "print-url: gh failure (e.g. missing auth) fails loudly, installs nothing" {
  # The arm-gate resolution probe (#587 slice 1) runs `--print-url --major v2`
  # to exercise the gh-authenticated published-wheel path — the exact #535
  # failure surface (gh REQUIRES a token even for a public repo). A gh failure
  # must propagate as a non-zero exit (failing the CI step), never degrade to
  # an empty URL or a silent install skip.
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
echo "gh: To use GitHub CLI in automation, set the GH_TOKEN environment variable." >&2
exit 4
STUB
  run "$BIN" --print-url --major v2
  [ "$status" -ne 0 ]
  [[ "$output" == *"GH_TOKEN"* ]]
  [ ! -s "$PIP_LOG" ]
  [ ! -e "$WORK/relcore/venv" ]
}

# --------------------------------------------------------------------------
# provenance stamp (#580): the resolved release tag lands in the venv so
# `release-core init` can label the managed-files commit with the REAL release
# line (since release#758 the wheel version is tag-stamped too; this explicit
# stamp remains the provenance channel init reads).
# --------------------------------------------------------------------------

@test "stamp: INDEX path stamps 'index <major>' (the version-resolved provenance)" {
  run "$BIN" --major v2
  [ "$status" -eq 0 ]
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "index v2" ]
}

@test "stamp: INDEX path with no major stamps 'index latest'" {
  run "$BIN"
  [ "$status" -eq 0 ]
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "index latest" ]
}

@test "stamp: ASSET-PATH FALLBACK stamps the resolved release tag" {
  PIP_FAIL_INDEX=1 run "$BIN" --major v2
  [ "$status" -eq 0 ]
  # The asset fallback resolved v2.6.0 (newest stable v2) — its tag is stamped.
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "v2.6.0" ]
}

@test "stamp: --print-url writes no stamp (resolve only)" {
  run "$BIN" --print-url
  [ "$status" -eq 0 ]
  [ ! -e "$WORK/relcore/venv/release-source.tag" ]
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

@test "from-source: stamps 'from-source <shortsha>' when the source is a git checkout" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  git -C "$WORK/src" init -q
  git -C "$WORK/src" config user.email t@example.com
  git -C "$WORK/src" config user.name Test
  git -C "$WORK/src" add -A
  git -C "$WORK/src" commit -qm init
  sha="$(git -C "$WORK/src" rev-parse --short HEAD)"
  run "$BIN" --from-source "$WORK/src" --no-init
  [ "$status" -eq 0 ]
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "from-source $sha" ]
}

@test "from-source: stamps bare 'from-source' when the source is not a git tree (never fakes a tag)" {
  mkdir -p "$WORK/src"; : > "$WORK/src/pyproject.toml"
  run "$BIN" --from-source "$WORK/src" --no-init
  [ "$status" -eq 0 ]
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "from-source" ]
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

@test "install: --user is now an UNKNOWN flag (removed in WS3) — usage error" {
  # The pre-isolation no-op flags were removed once the fleet migrated off the
  # old SessionStart caller (WS3, #760).
  run "$BIN" --user
  [ "$status" -eq 64 ]
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

# --------------------------------------------------------------------------
# WS5/E (#762): --cloud forwards to `release-core init --cloud` (cloud-only
# provisioning: tag fetch, dep caches, NSS cert import).
# --------------------------------------------------------------------------

@test "init: --cloud forwards --cloud to release-core init" {
  run "$BIN" --cloud
  [ "$status" -eq 0 ]
  [ "$(cat "$INIT_LOG")" = "release-core init --cloud" ]
}

@test "init: without --cloud passes NO cloud flag (local default)" {
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
# WS6/F (#763): fail-loud boot — the load-bearing wheel pull HALTS LOUDLY after
# a bounded transient-retry; a transient blip retries then proceeds; the verbose
# retry message names the attempt count.
# --------------------------------------------------------------------------

@test "fail-loud: a wholly-unresolvable wheel HALTS non-zero with a clear message" {
  # Index install fails (PIP_FAIL_INDEX), AND the asset-path resolve fails (gh
  # errors) → neither path resolves a wheel → the resolver must exit non-zero with
  # a fail-loud root-cause message, never warn-and-continue into a half-state.
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
echo "gh: network error" >&2
exit 4
STUB
  chmod +x stub/gh  # must be executable or PATH lookup falls through to the real gh
  PIP_FAIL_INDEX=1 RETRY_SLEEP=0 run "$BIN" --major v2
  [ "$status" -ne 0 ]
  [[ "$output" == *"FATAL"* ]]
  [[ "$output" == *"no wheel means no gate"* ]]
  # init never ran — there was no wheel to install.
  [ ! -s "$INIT_LOG" ]
}

@test "fail-loud: the index pull is RETRIED (bounded) before the fallback kicks in" {
  # PIP_FAIL_INDEX makes the index install fail every time; with RETRY_ATTEMPTS=3
  # the resolver retries it 3x (the verbose retry notice fires) THEN falls back to
  # the asset path (which succeeds), so the overall run still succeeds.
  PIP_FAIL_INDEX=1 RETRY_ATTEMPTS=3 RETRY_SLEEP=0 run "$BIN" --major v2
  [ "$status" -eq 0 ]
  [[ "$output" == *"attempt 1/3 failed"* ]]
  [[ "$output" == *"attempt 2/3 failed"* ]]
  [[ "$output" == *"falling back to the GitHub release-asset path"* ]]
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
    # Counter lives beside PIP_LOG (an EXPORTED path; \$WORK is not exported into
    # the stub's env, so a \$WORK-based path would be empty/unwritable).
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
  # It retried (attempt 1 failed) but then the INDEX path succeeded — so the stamp
  # is the index provenance, NOT the asset fallback.
  [[ "$output" == *"attempt 1/3 failed"* ]]
  [ "$(cat "$WORK/relcore/venv/release-source.tag")" = "index v2" ]
}
