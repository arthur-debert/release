#!/usr/bin/env bats
# Tests for bin/delegate-guard — the PreToolUse forcing function that keeps the
# MAIN agent loop delegating implementation to subagents (it does not hand-edit
# project files; subagents do the impl).
#
# The guard reads a Claude Code PreToolUse JSON event on stdin and either allows
# (exit 0, no stdout) or denies (exit 0, stdout = permissionDecision:deny JSON).
# The discriminator is `agent_id` — present ONLY inside a subagent call.

GUARD="$BATS_TEST_DIRNAME/../../bin/delegate-guard"

setup() {
    REPO="$BATS_TEST_TMPDIR/repo"
    mkdir -p "$REPO"
    git -C "$REPO" init -q
    GD="$(git -C "$REPO" rev-parse --git-dir)"
    case "$GD" in /*) : ;; *) GD="$REPO/$GD" ;; esac
}

# Write an Edit PreToolUse event and run the guard against it on stdin. Args:
#   $1 = file_path  $2 = cwd (default $REPO)  $3 = agent_id ('' for main loop)
#   $4 = tool_name (default Edit)
# Populates $status and $output via `run`.
guard() {
    local fp="$1" cwd="${2:-$REPO}" agent="${3:-}" tool="${4:-Edit}"
    local f="$BATS_TEST_TMPDIR/ev.json"
    python3 - "$fp" "$cwd" "$agent" "$tool" > "$f" <<'PY'
import json, sys
fp, cwd, agent, tool = sys.argv[1:5]
event = {"tool_name": tool, "tool_input": {"file_path": fp}, "cwd": cwd}
if agent:
    event["agent_id"] = agent
print(json.dumps(event))
PY
    run bash -c "'$GUARD' < '$f'"
}

@test "main loop: a project-file edit is denied" {
    guard "$REPO/src/main.rs"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *'subagent'* ]]
    [[ "$output" == *"$GD/delegate-armed"* ]]
}

@test "subagent (agent_id present): the edit is allowed" {
    guard "$REPO/src/main.rs" "$REPO" "sub-123"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "main loop: an edit under \$HOME/.claude/ is allowed" {
    guard "$HOME/.claude/projects/foo/memory/note.md"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "armed: a main-loop edit is allowed and the sentinel is consumed" {
    touch "$GD/delegate-armed"
    guard "$REPO/src/main.rs"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/delegate-armed" ]
}

@test "one-shot: a second edit after consuming the sentinel is denied again" {
    touch "$GD/delegate-armed"
    guard "$REPO/src/main.rs"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/delegate-armed" ]
    # sentinel consumed → next edit denies
    guard "$REPO/src/other.rs"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "Write is guarded too (denied for the main loop)" {
    guard "$REPO/README.md" "$REPO" "" "Write"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "NotebookEdit is guarded too (denied for the main loop)" {
    guard "$REPO/nb.ipynb" "$REPO" "" "NotebookEdit"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
}

@test "a non-guarded tool (Bash) is allowed" {
    printf '%s' '{"tool_name":"Bash","tool_input":{"command":"echo hi"},"cwd":"'"$REPO"'"}' \
        > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a non-guarded tool (Read) is allowed" {
    printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/x"},"cwd":"'"$REPO"'"}' \
        > "$BATS_TEST_TMPDIR/ev.json"
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

@test "well-formed JSON of the wrong shape (a list) fails open (allowed)" {
    printf '%s' '[1,2,3]' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a main-loop edit outside a git repo fails open (allowed)" {
    guard "/tmp/loose-file.txt" "/"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "main loop: target OUTSIDE any repo while cwd IS a repo is allowed" {
    # The decision keys off the TARGET's repo, not cwd. A loose file outside any
    # git work tree is not a project edit, even when cwd is a project repo.
    local loose="$BATS_TEST_TMPDIR/loose-outside.txt"
    guard "$loose" "$REPO"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "main loop: a NEW (non-existent) file inside the repo is denied" {
    # Creating a project file is a project edit. The target doesn't exist yet,
    # so detection resolves via its nearest existing ancestor dir (the repo).
    guard "$REPO/src/brand-new.rs"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *"$GD/delegate-armed"* ]]
}

@test "main loop: a relative target resolved against a repo cwd is denied" {
    # Relative file_path → joined with the event cwd; lands inside the repo.
    guard "src/rel.rs" "$REPO"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"permissionDecision": "deny"'* ]]
    [[ "$output" == *"$GD/delegate-armed"* ]]
}

@test "main loop: a relative target resolved OUTSIDE any repo is allowed" {
    # Relative file_path joined with a non-repo cwd stays outside any work tree.
    guard "scratch.txt" "$BATS_TEST_TMPDIR"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "armed: sentinel at the TARGET's git-dir is honored across cwd" {
    # Arm at the target repo's git-dir, run from an unrelated non-repo cwd. The
    # sentinel is consumed and the edit allowed — sentinel keying follows the
    # target, not cwd.
    touch "$GD/delegate-armed"
    guard "$REPO/src/main.rs" "$BATS_TEST_TMPDIR"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$GD/delegate-armed" ]
}

@test "missing tool_input fails open (allowed, never crashes)" {
    # No file_path → a real Edit/Write/NotebookEdit always carries an absolute
    # file_path, so its absence is a malformed event; the guard's contract says
    # a malformed event must ALLOW (fail open), never hard-block — and never crash.
    printf '%s' '{"tool_name":"Edit","cwd":"'"$REPO"'"}' > "$BATS_TEST_TMPDIR/ev.json"
    run bash -c "'$GUARD' < '$BATS_TEST_TMPDIR/ev.json'"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}
