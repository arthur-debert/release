#!/usr/bin/env bats

# ---------------------------------------------------------------------
# done-check: command contract (offline — a stub `gh` returns canned JSON)
#
# done-check is a shell→Python migration unit. The bash original was
# replaced by a thin Python
# shim over release_core.verbs.done_check. These tests pin the CLI
# contract: --help/usage, arg arity (exit 64), the stack-detect failure
# path (exit 65), and a full happy-path sweep against a stubbed gh so the
# table/--json output + exit-code policy are exercised end-to-end without
# the network.
#
# Per-verb logic + status aggregation are unit-tested in pytest
# (templates/commons/lib/release_core/tests/test_core_done_check.py); the
# BATS layer guards the shim edge.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/done-check.XXXXXX")"
  cd "$WORK"
  mkdir -p stub
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# A stub `gh` that dispatches on the api path argument. Each handler echoes
# canned JSON (or the raw file body). $WORK/release_yml holds the release.yml
# body returned for both the bare contents call and the ?ref= call.
_install_gh_stub() {
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
# Only `gh api <path> [flags]` is used by done-check.
[ "$1" = "api" ] || { echo "unexpected gh call: $*" >&2; exit 1; }
shift
# Find the path arg (last non-flag, or after stripping known flags).
path=""
for a in "$@"; do
  case "$a" in
    --paginate|-H) ;;            # flags we ignore
    application/*) ;;            # -H value
    *) path="$a" ;;
  esac
done
b64() { printf '{"content":"%s","encoding":"base64"}' "$(printf '%s' "$1" | base64)"; }
case "$path" in
  *"/contents/.github/workflows/release.yml"*)
      b64 "$(cat "$WORK/release_yml")" ;;
  *"/contents/bin/"*) echo '{"name":"x"}' ;;
  *"/actions/workflows/ci.yml/runs"*)
      echo '{"total_count":1,"workflow_runs":[{"status":"completed","conclusion":"success"}]}' ;;
  *"/actions/workflows/test.yml/runs"*) echo '{"total_count":0,"workflow_runs":[]}' ;;
  *"/releases/latest"*) echo '{"tag_name":"v1.2.3"}' ;;
  */repos/*/[!/]*) echo '{"default_branch":"main"}' ;;
  *) echo '{"default_branch":"main"}' ;;
esac
STUB
  chmod +x stub/gh
  export WORK
  export PATH="$PWD/stub:$PATH"
}

# --- help / usage -----------------------------------------------------

@test "--help prints usage and exits 0" {
  run "$BIN/release-core" status --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"done-check"* ]]
  [[ "$output" == *"Usage:"* ]]
}

# --- arg arity --------------------------------------------------------

@test "unknown arg exits 64" {
  run "$BIN/release-core" status --nope
  [ "$status" -eq 64 ]
  [[ "$output" == *"unknown arg: --nope"* ]]
}

@test "--repo without a value exits 64" {
  run "$BIN/release-core" status --repo
  [ "$status" -eq 64 ]
}

# --- happy path (canonical rust-cli, all green) -----------------------

@test "pilot-running: canonical rust-cli sweeps green, exit 0" {
  printf 'jobs:\n  release:\n    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v1\n' \
    > "$WORK/release_yml"
  _install_gh_stub
  run "$BIN/release-core" status --repo o/r
  [ "$status" -eq 0 ]
  [[ "$output" == *"Stack: rust-cli"* ]]
  [[ "$output" == *"pilot-running"* ]]
  [[ "$output" == *"→ o/r/rust-cli : pilot-running"* ]]
}

@test "--json emits a parseable object with verbs[]" {
  printf 'jobs:\n  release:\n    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v1\n' \
    > "$WORK/release_yml"
  _install_gh_stub
  run "$BIN/release-core" status --repo o/r --json
  [ "$status" -eq 0 ]
  # last line is the JSON object (the "Detecting…/Stack:" lines precede it)
  line="$(printf '%s\n' "$output" | tail -1)"
  [ "$(echo "$line" | jq -r '.repo')" = "o/r" ]
  [ "$(echo "$line" | jq -r '.stack')" = "rust-cli" ]
  [ "$(echo "$line" | jq -r '.state')" = "pilot-running" ]
  [ "$(echo "$line" | jq -r '.verbs[0].verb')" = "check" ]
}

# --- stack detection failure -----------------------------------------

@test "undetectable stack exits 65" {
  # release.yml absent + repo contents unreadable → exit 65.
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
  chmod +x stub/gh
  export PATH="$PWD/stub:$PATH"
  run "$BIN/release-core" status --repo o/r
  [ "$status" -eq 65 ]
  [[ "$output" == *"could not detect stack"* ]]
}
