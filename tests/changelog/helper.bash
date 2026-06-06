# Shared test setup for tests/changelog/*.bats.
#
# Each @test runs in a fresh mktemp -d working directory so the
# console-scripts (which all touch CHANGELOG/ relative to PWD) are
# sandboxed from each other. The tmpdir is initialized as a git repo
# because changelog{,-add,-cut,-render} resolve repo root via
# `git rev-parse --show-toplevel` (per #219). Tests that want to
# verify the "not in a git repo" error path can skip git init
# explicitly.
#
# The changelog/semver `bin/` shims were retired (release#476): the CLI
# now ships as release_core console-scripts. These BATS suites therefore
# exercise the CONSOLE-SCRIPTS — we pip-install the bundled release_core
# package into a per-suite venv and point $BIN at its scripts dir. The
# venv is built once (cached under BATS_FILE_TMPDIR) and reused across
# the @tests in a file.

# The harness lives in the bats component template during development;
# consumers get it via release-sync.
source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

_REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
_PKG_SRC="$_REPO_ROOT/templates/commons/lib/release_core"

# Build (once) a venv with release_core installed, exposing the
# changelog*/semver console-scripts; export BIN to its scripts dir.
_ensure_release_core_venv() {
  # BATS_FILE_TMPDIR is unique per .bats file and cleaned up by bats —
  # one venv per file is the right granularity (fast enough, isolated).
  local venv="$BATS_FILE_TMPDIR/release-core-venv"
  if [ ! -x "$venv/bin/changelog" ]; then
    python3 -m venv "$venv" >/dev/null
    "$venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null
    "$venv/bin/python" -m pip install --quiet "$_PKG_SRC" >/dev/null
  fi
  BIN="$venv/bin"
  # bin-internal/roll-changelog.sh invokes the bare `changelog`
  # console-script via PATH lookup, so put the venv scripts dir first.
  PATH="$BIN:$PATH"
  export PATH
}

setup() {
  _ensure_release_core_venv
  harness_create_workspace_notrap
  TMPDIR_TEST="$HARNESS_WORKSPACE"
  cd "$HARNESS_WORKSPACE"
  git init -q
  # CI runners carry no ambient git identity, and with auto-detection
  # disabled (user.useConfigOnly) `git commit` aborts with exit 128. Pin a
  # self-contained identity (+ disable signing, set the default branch) in
  # the fixture repo so any test that commits is independent of host git
  # config. Regression: PR #408 (run-precommit-gate-deletions failed 128 in CI).
  git config user.email "tests@release.invalid"
  git config user.name "Release Tests"
  git config commit.gpgsign false
  git config tag.gpgsign false
  git config init.defaultBranch main
}

teardown() {
  cd /
  harness_cleanup
}
