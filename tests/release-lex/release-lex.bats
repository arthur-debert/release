#!/usr/bin/env bats

# ---------------------------------------------------------------------
# release-lex: shim CLI contract. release-lex is a thin Python shim over
# release_core.verbs.release_lex. These tests pin the CLI edge — usage
# text, the usage exit code (64), the bad bump-kind / no-repos /
# unknown-arg paths, and the validation aborts (exit 1) — for the new
# post-`scripts/release` model: the cut path needs the managed
# `bin/release` tool (re-synced from arthur-debert/release), and release
# is driven by `bin/release` (= release-cut, which dispatches release.yml;
# CI does the mutation). The should-release decision is computed
# generically via git — there is no per-repo `bin/diff-since-release`.
#
# The live multi-repo orchestration (fetch / dispatch / CI-watch) is
# faithful side-effecting glue and is NOT exercised here (it needs real
# repos + GitHub — NO real releases are cut); the pure decision logic
# (the generic git should-release decision) is covered offline by
# pytest (test_core_release_lex.py). No remote is touched.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/rlx.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# Build a fake lex-fmt repo carrying the managed bin/ release tool.
# $1 = dir, remaining args = tool names to OMIT (of: release).
_make_repo() {
  local dir="$1"; shift
  local omit=" $* "
  mkdir -p "$dir/bin"
  local tool
  for tool in release; do
    case "$omit" in
      *" $tool "*) continue ;;
    esac
    printf '#!/usr/bin/env bash\n' > "$dir/bin/$tool"
    chmod +x "$dir/bin/$tool"
  done
}

# ---------------------------------------------------------------------
# Usage / help surface.
# ---------------------------------------------------------------------

@test "no args prints usage and exits 64" {
  run "$BIN/release-lex"
  [ "$status" -eq 64 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"<bump-kind>"* ]]
}

@test "--help prints usage and exits 0" {
  run "$BIN/release-lex" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "-h is an alias for --help" {
  run "$BIN/release-lex" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

# ---------------------------------------------------------------------
# Bad-usage exit codes (64).
# ---------------------------------------------------------------------

@test "unknown arg exits 64" {
  run "$BIN/release-lex" patch --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg: --bogus"* ]]
}

@test "bad bump-kind exits 64" {
  _make_repo "$WORK/comms"
  run "$BIN/release-lex" frobnicate --comms "$WORK/comms"
  [ "$status" -eq 64 ]
  [[ "$output" == *"bad bump-kind"* ]]
}

@test "no repo paths exits 64" {
  run "$BIN/release-lex" patch
  [ "$status" -eq 64 ]
  [[ "$output" == *"no repo paths supplied"* ]]
}

# ---------------------------------------------------------------------
# Validation aborts (1).
# ---------------------------------------------------------------------

@test "non-directory repo path exits 1" {
  run "$BIN/release-lex" patch --comms "$WORK/does-not-exist"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a directory"* ]]
  [[ "$output" == *"(for --comms)"* ]]
}

@test "missing managed bin/release exits 1" {
  _make_repo "$WORK/comms" release
  run "$BIN/release-lex" patch --comms "$WORK/comms"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing bin/release"* ]]
  [[ "$output" == *"release-sync"* ]]
}

# ---------------------------------------------------------------------
# --status is read-only: with no repos it still hits the no-repos guard.
# The should-release decision is generic git, so --status requires NO
# bin/ tool at all (just a directory).
# ---------------------------------------------------------------------

@test "status mode with no repos exits 64" {
  run "$BIN/release-lex" --status
  [ "$status" -eq 64 ]
  [[ "$output" == *"no repo paths supplied"* ]]
}

@test "status mode requires no bin tool" {
  # A bare directory (no bin/ tool) passes validation in --status mode.
  # It is not a git repo, so the per-repo `git fetch` + `git tag` error and
  # the line renders as a FAILED decision; the point is validation does NOT
  # abort 1 and the run completes (exit 0).
  mkdir -p "$WORK/comms"
  run "$BIN/release-lex" --status --comms "$WORK/comms"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Cascade status"* ]]
}
