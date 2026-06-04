#!/usr/bin/env bats

# ---------------------------------------------------------------------
# release-lex: CLI contract via `release-core admin release lex` (the flat
# `release-lex` console-script was retired in the B2 cutover; #468). It is a
# thin click leaf over release_core.verbs.release_lex. These tests pin the CLI
# edge — usage text, the usage exit code (64), the bad bump-kind / no-repos /
# unknown-arg paths, and the validation aborts (exit 1) — for the self-contained
# model: the cut path dispatches via the MAINTAINER's `release-core cut`
# (resolved from PATH, run in each repo's cwd), so it requires `release-core` on
# PATH (checked once, up front) and NOT a per-repo `bin/release` shim (which is
# missing on stale chain repos). The should-release decision is computed
# generically via git — there is no per-repo `bin/diff-since-release` either.
#
# The live multi-repo orchestration (fetch / dispatch / CI-watch) is
# faithful side-effecting glue and is NOT exercised here (it needs real
# repos + GitHub — NO real releases are cut); the pure decision logic
# (the generic git should-release decision) is covered offline by
# pytest (test_core_release_lex.py). No remote is touched.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"
# Invoke the verb through the hierarchical CLI.
LEX=("$BIN/release-core" admin release lex)

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/rlx.XXXXXX")"
  cd "$WORK"
  # Preserve the real PATH (which carries the click-bearing python the
  # release-core shim runs under) so the "dispatch tool absent" test can drop
  # ONLY the fake release-core, not the whole toolchain.
  ORIG_PATH="$PATH"
  # A fake `release-core` on PATH so cut-mode validation's up-front PATH
  # check (shutil.which) passes; the "not on PATH" test runs with $ORIG_PATH
  # instead so this stub is invisible. (This stub satisfies the dispatch-tool
  # lookup only — the verb itself is invoked by absolute path via $LEX.)
  FAKEBIN="$WORK/fakebin"
  mkdir -p "$FAKEBIN"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKEBIN/release-core"
  chmod +x "$FAKEBIN/release-core"
  PATH="$FAKEBIN:$PATH"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# Build a fake lex-fmt repo. release-lex no longer requires a per-repo
# bin/ tool — cut mode dispatches via the maintainer's `release-core cut`
# on PATH — so this is just a directory that exists.
_make_repo() {
  local dir="$1"
  mkdir -p "$dir"
}

# ---------------------------------------------------------------------
# Usage / help surface.
# ---------------------------------------------------------------------

@test "no args prints usage and exits 64" {
  run "${LEX[@]}"
  [ "$status" -eq 64 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"<bump-kind>"* ]]
}

@test "--help prints usage and exits 0" {
  run "${LEX[@]}" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "-h is an alias for --help" {
  run "${LEX[@]}" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

# ---------------------------------------------------------------------
# Bad-usage exit codes (64).
# ---------------------------------------------------------------------

@test "unknown arg exits 64" {
  run "${LEX[@]}" patch --bogus
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg: --bogus"* ]]
}

@test "bad bump-kind exits 64" {
  _make_repo "$WORK/comms"
  run "${LEX[@]}" frobnicate --comms "$WORK/comms"
  [ "$status" -eq 64 ]
  [[ "$output" == *"bad bump-kind"* ]]
}

@test "no repo paths exits 64" {
  run "${LEX[@]}" patch
  [ "$status" -eq 64 ]
  [[ "$output" == *"no repo paths supplied"* ]]
}

# ---------------------------------------------------------------------
# Validation aborts (1).
# ---------------------------------------------------------------------

@test "non-directory repo path exits 1" {
  run "${LEX[@]}" patch --comms "$WORK/does-not-exist"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a directory"* ]]
  [[ "$output" == *"(for --comms)"* ]]
}

@test "release-core not on PATH exits 1" {
  _make_repo "$WORK/comms"
  # Drop every PATH entry that carries a `release-core` (the fake stub AND any
  # real on-PATH release-core, e.g. a dev machine's release bin/) so the verb's
  # up-front shutil.which("release-core") fails — while KEEPING the python the
  # release-core shim runs under (and its click dep). $LEX[0] is an absolute
  # path, so the verb itself still runs; only the dispatch-tool lookup fails.
  local scrubbed="" d
  IFS=: read -ra _dirs <<< "$ORIG_PATH"
  for d in "${_dirs[@]}"; do
    [ -n "$d" ] || continue
    [ -e "$d/release-core" ] && continue
    scrubbed="${scrubbed:+$scrubbed:}$d"
  done
  PATH="$scrubbed" run "${LEX[@]}" patch --comms "$WORK/comms"
  [ "$status" -eq 1 ]
  [[ "$output" == *"release-core not on PATH"* ]]
  [[ "$output" == *"add the release repo's bin/ to PATH"* ]]
}

# ---------------------------------------------------------------------
# --status is read-only: with no repos it still hits the no-repos guard.
# The should-release decision is generic git, so --status requires NO
# bin/ tool at all (just a directory).
# ---------------------------------------------------------------------

@test "status mode with no repos exits 64" {
  run "${LEX[@]}" --status
  [ "$status" -eq 64 ]
  [[ "$output" == *"no repo paths supplied"* ]]
}

@test "status mode requires no bin tool" {
  # A bare directory (no bin/ tool) passes validation in --status mode.
  # It is not a git repo, so the per-repo `git fetch` + `git tag` error and
  # the line renders as a FAILED decision; the point is validation does NOT
  # abort 1 and the run completes (exit 0).
  mkdir -p "$WORK/comms"
  run "${LEX[@]}" --status --comms "$WORK/comms"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Cascade status"* ]]
}
