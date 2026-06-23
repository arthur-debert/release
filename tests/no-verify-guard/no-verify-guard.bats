#!/usr/bin/env bats
# Tests for bin/no-verify-guard — the PreToolUse forcing function that blocks
# `git commit --no-verify` (and the `-n` short form), since the gate is a HARD
# gate and `--no-verify` is never an acceptable bypass (release-local dogfood).
#
# The guard reads a Claude Code PreToolUse JSON event on stdin and either allows
# (exit 0, no stdout) or denies (exit 0, stdout = permissionDecision:deny JSON).

GUARD="$BATS_TEST_DIRNAME/../../bin/no-verify-guard"

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

@test "unarmed: git commit --no-verify is denied" {
    guard 'git commit --no-verify -m "x"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *'HARD gate'* ]]
}

@test "unarmed: git commit -n -m is denied (short flag)" {
    guard 'git commit -n -m "x"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "unarmed: git commit -nm (bundled short flags) is denied" {
    guard 'git commit -nm "x"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "unarmed: git commit -an (bundled, n last) is denied" {
    guard 'git commit -an -m "x"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "--no-verify inside an -m message body is NOT a bypass (allowed)" {
    # The phrase stays inside the single quoted message-value token, so it never
    # appears as a bare flag token after `git commit`.
    guard 'git commit -m "mentions --no-verify in body"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a normal git commit -m is allowed" {
    guard 'git commit -m "a real commit message"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a mention in a grep pattern is NOT a commit (allowed)" {
    guard 'grep -n "git commit --no-verify" skill.md'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a mention inside an echo string is NOT a commit (allowed)" {
    guard 'echo "never run git commit --no-verify"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an unrelated git subcommand is allowed" {
    guard 'git status -n'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "armed: git commit --no-verify is allowed and the sentinel is consumed" {
    touch "$GD/no-verify-armed"
    guard 'git commit --no-verify -m "x"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/no-verify-armed" ]
}

@test "armed in the cd-TARGET repo (payload cwd elsewhere) is allowed + consumed" {
    TARGET="$BATS_TEST_TMPDIR/target"
    mkdir -p "$TARGET"
    git -C "$TARGET" init -q
    TGD="$(git -C "$TARGET" rev-parse --git-dir)"
    case "$TGD" in /*) : ;; *) TGD="$TARGET/$TGD" ;; esac
    touch "$TGD/no-verify-armed"
    guard "cd $TARGET && git commit --no-verify -m x" "$REPO"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$TGD/no-verify-armed" ]
}

@test "deny reason lists the absolute sentinel path to arm" {
    guard 'git commit -n -m "x"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *"$GD/no-verify-armed"* ]]
}

@test "env-prefixed and extra-spaced invocation is still detected" {
    guard 'FOO=bar git   commit   --no-verify -m x'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
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

@test "empty command is allowed (fail open)" {
    guard ''
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "git commit --no-verify outside a git repo fails open (allowed)" {
    guard 'git commit --no-verify -m x' '/'
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
    printf '%s' '{"tool_name":"Bash","tool_input":{"command":["git","commit","--no-verify"]}}' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
