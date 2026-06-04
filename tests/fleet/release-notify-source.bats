#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# release-notify-source: close-the-loop notifier (release#348 §5.2 / #369).
# Offline — a stub `gh` returns a canned release issue and records any
# `gh pr comment` / `gh issue close` calls to ./gh-calls.log.
# ---------------------------------------------------------------------

# $1 = JSON for `gh issue view`. The stub logs mutating subcommands so we can
# assert dry-run posts nothing and --post posts to each source PR.
_stub_gh() {
  mkdir -p stub
  cat > stub/issue.json <<JSON
$1
JSON
  cat > stub/gh <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "issue" ] && [ "$2" = "view" ]; then
  cat "$(dirname "$0")/issue.json"; exit 0
fi
echo "gh $*" >> gh-calls.log
exit 0
EOF
  chmod +x stub/gh
  export PATH="$PWD/stub:$PATH"
}

_issue_two_prs() {
  cat <<'JSON'
{"number":42,"title":"[copilot-review] reviewer empty","url":"https://github.com/arthur-debert/release/issues/42","body":"**Reported from:** arthur-debert/dodot\n**PR:** https://github.com/arthur-debert/dodot/pull/118\n","comments":[{"body":"Also hit on **arthur-debert/clapfig**.\n- PR: https://github.com/arthur-debert/clapfig/pull/9\n"}]}
JSON
}

@test "missing issue number exits 64" {
  run "$BIN/release-core" admin inbox notify-source --fix x
  [ "$status" -eq 64 ]
}

@test "missing --fix exits 64" {
  run "$BIN/release-core" admin inbox notify-source 42
  [ "$status" -eq 64 ]
}

@test "non-numeric issue exits 64" {
  run "$BIN/release-core" admin inbox notify-source not-a-number --fix x
  [ "$status" -eq 64 ]
}

@test "dry-run lists every source PR and posts nothing" {
  _stub_gh "$(_issue_two_prs)"
  run "$BIN/release-core" admin inbox notify-source 42 --fix "release#371"
  [ "$status" -eq 0 ]
  [[ "$output" == *"DRY-RUN"* ]]
  [[ "$output" == *"dodot/pull/118"* ]]
  [[ "$output" == *"clapfig/pull/9"* ]]
  [[ "$output" == *"release#371"* ]]
  [ ! -f gh-calls.log ]   # nothing posted
}

@test "--post comments on each source PR" {
  _stub_gh "$(_issue_two_prs)"
  run "$BIN/release-core" admin inbox notify-source 42 --fix "release#371" --post
  [ "$status" -eq 0 ]
  [[ "$output" == *"notified:"* ]]
  grep -q 'pr comment https://github.com/arthur-debert/dodot/pull/118' gh-calls.log
  grep -q 'pr comment https://github.com/arthur-debert/clapfig/pull/9' gh-calls.log
}

@test "--post --close also closes the release issue" {
  _stub_gh "$(_issue_two_prs)"
  run "$BIN/release-core" admin inbox notify-source 42 --fix "release#371" --post --close
  [ "$status" -eq 0 ]
  grep -q 'issue close 42' gh-calls.log
}

@test "an issue with no source PR exits 3 and names the reported repo" {
  _stub_gh '{"number":7,"title":"[ruleset] x","url":"https://github.com/arthur-debert/release/issues/7","body":"**Reported from:** arthur-debert/padz\n","comments":[]}'
  run "$BIN/release-core" admin inbox notify-source 7 --fix "release#999"
  [ "$status" -eq 3 ]
  [[ "$output" == *"arthur-debert/padz"* ]]
  [ ! -f gh-calls.log ]
}
