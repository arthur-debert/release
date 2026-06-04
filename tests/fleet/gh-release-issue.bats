#!/usr/bin/env bats

# ---------------------------------------------------------------------
# gh-release-issue: file a consumer bug against arthur-debert/release.
#
# Distributed tool (real source templates/commons/bin/gh-release-issue,
# symlinked from bin/gh-release-issue) migrated to a Python shim over
# release_core.verbs.gh_release_issue.
# These pin the byte-for-byte CLI contract: help/usage/arity exit codes
# and that a filed issue carries the `consumer-filed` label and the auto-
# collected context. Fully offline — a stub `gh` records `issue create`
# and returns canned context; a stub `git` supplies the branch.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/gh-release-issue.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# A stub `gh` that answers the context probes and records `issue create`.
# Writes the create invocation (one arg per line) to $CREATE_LOG.
_stub_gh() {
  mkdir -p stub
  export CREATE_LOG="$PWD/create.log"
  cat > stub/gh <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "repo view")  echo "arthur-debert/dodot"; exit 0 ;;
  "pr list")    echo "118"; exit 0 ;;
  "run list")   echo "https://github.com/arthur-debert/dodot/actions/runs/1"; exit 0 ;;
  "issue create")
    printf '%s\n' "$@" >> "$CREATE_LOG"
    echo "https://github.com/arthur-debert/release/issues/99"
    exit 0 ;;
esac
exit 0
EOF
  chmod +x stub/gh
  cat > stub/git <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "branch" ] && [ "$2" = "--show-current" ]; then echo "feat/x"; exit 0; fi
exit 0
EOF
  chmod +x stub/git
  export PATH="$PWD/stub:$PATH"
}

@test "no args prints usage and exits 64" {
  run "$BIN/gh-release-issue"
  [ "$status" -eq 64 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "one arg (missing symptom) exits 64" {
  run "$BIN/gh-release-issue" copilot-review
  [ "$status" -eq 64 ]
  [[ "$output" == *"need <component> <symptom>"* ]]
}

@test "--help exits 0 and prints usage" {
  run "$BIN/gh-release-issue" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "files an issue with the consumer-filed label and prints the url" {
  _stub_gh
  run "$BIN/gh-release-issue" copilot-review "reviewer empty after success"
  [ "$status" -eq 0 ]
  [[ "$output" == *"release/issues/99"* ]]
  grep -qx 'consumer-filed' create.log
  grep -Fqx '[copilot-review] reviewer empty after success' create.log
  grep -qx 'arthur-debert/release' create.log
}

@test "multi-word symptom is captured whole in the title" {
  _stub_gh
  run "$BIN/gh-release-issue" other this is a multi word symptom
  [ "$status" -eq 0 ]
  grep -Fqx '[other] this is a multi word symptom' create.log
}
