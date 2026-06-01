#!/usr/bin/env bats

# ---------------------------------------------------------------------
# release-lex: shim CLI contract (shell→Python migration,
# docs/proposals/shell-to-python.md). The bash original was replaced by a
# thin Python shim over release_core.verbs.release_lex. These tests pin the
# byte-for-byte CLI edge — usage text, the usage exit code (64), the bad
# bump-kind / no-repos / unknown-arg paths, and the validation aborts
# (exit 1) — that the bash version had. The live multi-repo orchestration
# (push / PR / merge / CI-watch) is faithful side-effecting glue and is NOT
# exercised here (it needs real repos + GitHub); the pure decision logic is
# covered offline by pytest (test_core_release_lex.py). No remote is touched.
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

# Build a fake lex-fmt repo with the four executable Layer-0 primitives.
# $1 = dir, remaining args = primitive names to OMIT.
_make_repo() {
  local dir="$1"; shift
  local omit=" $* "
  mkdir -p "$dir/scripts/release"
  local prim
  for prim in get-current-version get-commits-since-release update-release trigger-release; do
    case "$omit" in
      *" $prim "*) continue ;;
    esac
    printf '#!/usr/bin/env bash\n' > "$dir/scripts/release/$prim"
    chmod +x "$dir/scripts/release/$prim"
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

@test "missing Layer-0 primitive exits 1" {
  _make_repo "$WORK/comms" trigger-release
  run "$BIN/release-lex" patch --comms "$WORK/comms"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing scripts/release/trigger-release"* ]]
  [[ "$output" == *"Layer 0 must be merged"* ]]
}

# ---------------------------------------------------------------------
# --status is read-only: with no repos it still hits the no-repos guard.
# ---------------------------------------------------------------------

@test "status mode with no repos exits 64" {
  run "$BIN/release-lex" --status
  [ "$status" -eq 64 ]
  [[ "$output" == *"no repo paths supplied"* ]]
}
