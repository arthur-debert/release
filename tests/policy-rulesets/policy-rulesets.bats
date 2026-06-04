#!/usr/bin/env bats

# ---------------------------------------------------------------------
# policy-rulesets shims: apply-ruleset / sweep-github-policy /
# enable-dependabot-security — the shell→Python migration's Phase 1
# policy-rulesets unit (docs/proposals/shell-to-python.md).
#
# The bash originals were replaced by thin Python shims over
# release_core.verbs.{apply_ruleset,sweep_github_policy,
# enable_dependabot_security}. These tests pin the CLI contract the bash
# had: --help exits 0 with a Usage block, unknown flags / bad arity exit
# 64, and the offline paths (dry-runs, conflict→nonzero) behave. The data
# layer (gh) is exercised by unit tests; here a tiny stub gh keeps the
# shim end-to-end offline. No network.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/policy-rulesets.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# A stub `gh`: `gh repo view` emits o/r; every `gh api` 404s (exit 1) so the
# verbs' GhError paths are exercised without a network.
_stub_gh_404() {
  mkdir -p stub
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
case "$1" in
  repo)
    for a in "$@"; do [ "$a" = "nameWithOwner" ] && echo "o/r"; done
    # gh repo list <owner> --json nameWithOwner → empty array
    [ "$2" = "list" ] && echo "[]"
    exit 0 ;;
  api) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
  *) exit 0 ;;
esac
STUB
  chmod +x stub/gh
  export PATH="$PWD/stub:$PATH"
}

# --- apply-ruleset ----------------------------------------------------

@test "apply-ruleset --help exits 0 and prints usage" {
  run "$BIN/release-core" admin policy ruleset --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"apply-ruleset"* ]]
}

@test "apply-ruleset unknown flag is a usage error (exit 64)" {
  run "$BIN/release-core" admin policy ruleset --nope
  [ "$status" -eq 64 ]
}

@test "apply-ruleset --checks needs a value (exit 64)" {
  run "$BIN/release-core" admin policy ruleset --checks
  [ "$status" -eq 64 ]
}

@test "apply-ruleset --dry-run with --checks builds + prints the payload, sends nothing" {
  _stub_gh_404
  run "$BIN/release-core" admin policy ruleset --dry-run --checks "Test,Build (x)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"repo:    o/r"* ]]
  [[ "$output" == *"ruleset: main-branch-protection (existing id: none)"* ]]
  [[ "$output" == *"checks:  Test,Build (x)"* ]]
  [[ "$output" == *"--- payload (dry-run, not sent) ---"* ]]
  [[ "$output" == *'"context": "Test"'* ]]
  [[ "$output" == *'"context": "Build (x)"'* ]]
}

# --- sweep-github-policy ----------------------------------------------

@test "sweep-github-policy --help exits 0 and prints usage" {
  run "$BIN/release-core" admin policy sweep --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"sweep-github-policy"* ]]
}

@test "sweep-github-policy unknown flag is a usage error (exit 64)" {
  run "$BIN/release-core" admin policy sweep --nope
  [ "$status" -eq 64 ]
}

@test "sweep-github-policy --stack needs a value (exit 64)" {
  run "$BIN/release-core" admin policy sweep --stack
  [ "$status" -eq 64 ]
}

# --- enable-dependabot-security ---------------------------------------

@test "enable-dependabot-security --help exits 0 and prints usage" {
  run "$BIN/release-core" admin policy dependabot --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"enable-dependabot-security"* ]]
}

@test "enable-dependabot-security unknown flag is a usage error (exit 64)" {
  run "$BIN/release-core" admin policy dependabot --nope
  [ "$status" -eq 64 ]
}

@test "enable-dependabot-security --repos needs a value (exit 64)" {
  run "$BIN/release-core" admin policy dependabot --repos
  [ "$status" -eq 64 ]
}

@test "enable-dependabot-security --dry-run over explicit repos lists + summarizes" {
  run "$BIN/release-core" admin policy dependabot --repos o/a,o/b --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"found 2 repo(s):"* ]]
  [[ "$output" == *"[dry] PUT vulnerability-alerts"* ]]
  [[ "$output" == *"[dry] PUT automated-security-fixes"* ]]
  [[ "$output" == *"summary: 2 repos, alerts + automated fixes enabled"* ]]
}

@test "enable-dependabot-security finds no onboarded repos (exit 1)" {
  _stub_gh_404
  run "$BIN/release-core" admin policy dependabot --owners someorg
  [ "$status" -eq 1 ]
  [[ "$output" == *"no onboarded repos found"* ]]
}
