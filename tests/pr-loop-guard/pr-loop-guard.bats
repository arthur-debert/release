#!/usr/bin/env bats
# Tests for bin/pr-loop-guard — the PreToolUse forcing function that routes PR
# creation through the gh-pr-review-loop skill (release#495, epic #348).
#
# The guard reads a Claude Code PreToolUse JSON event on stdin and either allows
# (exit 0, no stdout) or denies (exit 0, stdout = permissionDecision:deny JSON).

GUARD="$BATS_TEST_DIRNAME/../../bin/pr-loop-guard"

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    mkdir -p "$REPO"
    git -C "$REPO" init -q
    GD="$(git -C "$REPO" rev-parse --git-dir)"
    case "$GD" in /*) : ;; *) GD="$REPO/$GD" ;; esac
}

# Write a Bash PreToolUse event (command=$1, cwd=$2 or $REPO) to a file and run
# the guard against it on stdin. Populates $status and $output via `run`.
guard() {
    local cmd="$1" cwd="${2:-$REPO}" f="$BATS_TEST_TMPDIR/ev.json"
    python3 - "$cmd" "$cwd" > "$f" <<'PY'
import json, sys
print(json.dumps({"tool_name": "Bash",
                  "tool_input": {"command": sys.argv[1]},
                  "cwd": sys.argv[2]}))
PY
    run bash -c "'$GUARD' < '$f'"
}

@test "unarmed: a real gh pr create is denied" {
    guard 'gh pr create --title x --body y'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *'gh-pr-review-loop'* ]]
}

@test "armed: gh pr create is allowed and the sentinel is consumed" {
    touch "$GD/pr-loop-armed"
    guard 'gh pr create --fill'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/pr-loop-armed" ]
}

@test "armed in the cd-TARGET repo (payload cwd elsewhere) is allowed + consumed" {
    # The 5/5 subagent bug: payload cwd = session root, command cd's into the PR
    # repo and arms THAT .git. The guard must honour the cd target's sentinel.
    TARGET="$BATS_TEST_TMPDIR/target"
    mkdir -p "$TARGET"
    git -C "$TARGET" init -q
    TGD="$(git -C "$TARGET" rev-parse --git-dir)"
    case "$TGD" in /*) : ;; *) TGD="$TARGET/$TGD" ;; esac
    touch "$TGD/pr-loop-armed"
    guard "cd $TARGET && gh pr create --fill" "$REPO"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$TGD/pr-loop-armed" ]
}

@test "a relative cd target resolves against the payload cwd" {
    parent="$(dirname "$REPO")"
    name="$(basename "$REPO")"
    touch "$GD/pr-loop-armed"
    guard "cd $name && gh pr create --fill" "$parent"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/pr-loop-armed" ]
}

@test "chained relative cds fold left: cd a && cd b lands in cwd/a/b" {
    # release#632: the second relative target must resolve against the FIRST
    # cd's result (cwd/a/b), not the original payload cwd (cwd/b).
    NESTED="$BATS_TEST_TMPDIR/work/a/b"
    mkdir -p "$NESTED"
    git -C "$NESTED" init -q
    NGD="$(git -C "$NESTED" rev-parse --git-dir)"
    case "$NGD" in /*) : ;; *) NGD="$NESTED/$NGD" ;; esac
    touch "$NGD/pr-loop-armed"
    guard "cd a && cd b && gh pr create --fill" "$BATS_TEST_TMPDIR/work"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$NGD/pr-loop-armed" ]
}

@test "an absolute cd target resets the fold-left accumulator" {
    # After `cd /abs`, a following relative cd resolves against /abs, and the
    # earlier accumulated path is irrelevant.
    BASE="$BATS_TEST_TMPDIR/abs-base"
    mkdir -p "$BASE/sub"
    git -C "$BASE/sub" init -q
    SGD="$(git -C "$BASE/sub" rev-parse --git-dir)"
    case "$SGD" in /*) : ;; *) SGD="$BASE/sub/$SGD" ;; esac
    touch "$SGD/pr-loop-armed"
    guard "cd elsewhere && cd $BASE && cd sub && gh pr create --fill" "$REPO"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$SGD/pr-loop-armed" ]
}

@test "deny reason lists the absolute sentinel paths checked (payload + cd target)" {
    TARGET="$BATS_TEST_TMPDIR/target"
    mkdir -p "$TARGET"
    git -C "$TARGET" init -q
    guard "cd $TARGET && gh pr create --fill" "$REPO"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *"$TARGET"*"pr-loop-armed"* ]]
}

@test "a mention of the phrase in a grep pattern is NOT a create (allowed)" {
    guard 'grep -n "gh pr create" skill.md'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a mention inside an echo string is NOT a create (allowed)" {
    guard 'echo "run gh pr create next"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "env-prefixed and extra-spaced invocation is still detected" {
    guard 'FOO=bar gh   pr   create --draft'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "an unrelated gh subcommand is allowed" {
    guard 'gh pr view 42 --json state'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a non-Bash tool is allowed" {
    printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x"}}' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "malformed stdin fails open (allowed)" {
    printf 'not json' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "gh pr create outside a git repo fails open (allowed)" {
    guard 'gh pr create' '/'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "well-formed JSON of the wrong shape (a list) fails open (allowed)" {
    printf '%s' '[1,2,3]' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a non-string command field fails open (allowed)" {
    printf '%s' '{"tool_name":"Bash","tool_input":{"command":["gh","pr","create"]}}' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
