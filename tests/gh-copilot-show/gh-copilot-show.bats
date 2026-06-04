#!/usr/bin/env bats
# Tests for bin/gh-copilot-show — the READ-ONLY Copilot-review printer.
#
# Network/auth is mocked by shadowing `gh` on PATH. The mock answers:
#   - `gh repo view --json nameWithOwner` → fixed nameWithOwner JSON
#   - `gh api .../reviews`                → one Copilot review fixture
#   - `gh api .../comments`               → one inline-comment fixture
# It also records every `gh` invocation so the tests can assert that
# gh-copilot-show NEVER requests a review (no `pr edit --add-reviewer`).

SHOW="$BATS_TEST_DIRNAME/../../bin/gh-copilot-show"

setup() {
    MOCKBIN="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$MOCKBIN"
    export GH_CALLS="$BATS_TEST_TMPDIR/gh-calls.log"
    : > "$GH_CALLS"

    cat > "$MOCKBIN/gh" <<'MOCKGH'
#!/usr/bin/env bash
# Mock gh for gh-copilot-show. Records calls, then mimics `gh api --jq <f>` by
# piping the response body through real jq (as the gh client does internally).
echo "$*" >> "$GH_CALLS"
if [[ "$1 $2" == "repo view" ]]; then
    echo "O/N"
    exit 0
fi

# Pull the --jq filter out of argv.
filter=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "--jq" ]]; then
        filter="${args[$((i + 1))]}"
    fi
done

emit() {
    if [[ -n "$filter" ]]; then jq -r "$filter"; else cat; fi
}

if [[ "$*" == *"/reviews"* ]]; then
    echo '[{"user":{"login":"copilot-pull-request-reviewer[bot]"},"state":"COMMENTED","submitted_at":"2026-06-04T00:00:00Z","body":"LGTM with nits"}]' | emit
    exit 0
fi
if [[ "$*" == *"/comments"* ]]; then
    echo '[{"user":{"login":"copilot-pull-request-reviewer[bot]"},"path":"src/x.rs","line":42,"body":"prefer ? here"}]' | emit
    exit 0
fi
echo "unexpected gh call: $*" >&2
exit 99
MOCKGH
    chmod +x "$MOCKBIN/gh"
    export PATH="$MOCKBIN:$PATH"
}

@test "prints the copilot review body and inline comments" {
    run "$SHOW" 91
    [ "$status" -eq 0 ]
    [[ "$output" == *"=== copilot review on O/N#91 ==="* ]]
    [[ "$output" == *"LGTM with nits"* ]]
    [[ "$output" == *"=== inline comments ==="* ]]
    [[ "$output" == *"[src/x.rs:42] prefer ? here"* ]]
}

@test "never requests a review (no add-reviewer, read-only)" {
    run "$SHOW" 91
    [ "$status" -eq 0 ]
    run grep -- "--add-reviewer" "$GH_CALLS"
    [ "$status" -ne 0 ]   # grep finds nothing → non-zero
    run grep -- "pr edit" "$GH_CALLS"
    [ "$status" -ne 0 ]
}

@test "no args prints usage with exit 64" {
    run "$SHOW"
    [ "$status" -eq 64 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "non-numeric PR number is rejected (exit 64)" {
    run "$SHOW" abc
    [ "$status" -eq 64 ]
    [[ "$output" == *"PR number must be numeric (got: abc)"* ]]
}

@test "--help prints usage with exit 0" {
    run "$SHOW" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    [[ "$output" == *"read-only"* || "$output" == *"Read-only"* ]]
}
