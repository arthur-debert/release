#!/usr/bin/env bats
# Tests for bin/gh-pr-resolve-thread (stdlib-only Python port).
#
# Network/auth is mocked by shadowing `gh` on PATH. The mock answers three
# call shapes the script makes:
#   - `gh repo view --json owner,name`  → fixed owner/name JSON
#   - `gh api graphql ... query(...)`   → the reviewThreads fixture on disk
#   - `gh api graphql ... mutation(...)`→ a resolveReviewThread payload
# It distinguishes query vs mutation by scanning argv for `mutation`.

RESOLVE="$BATS_TEST_DIRNAME/../../bin/gh-pr-resolve-thread"

setup() {
    MOCKBIN="$BATS_TEST_TMPDIR/bin"
    mkdir -p "$MOCKBIN"
    export RESOLVE_THREADS_FIXTURE="$BATS_TEST_TMPDIR/threads.json"

    cat > "$MOCKBIN/gh" <<'MOCKGH'
#!/usr/bin/env bash
# Mock gh for gh-pr-resolve-thread tests.
if [[ "$1 $2" == "repo view" ]]; then
    echo '{"owner":{"login":"O"},"name":"N"}'
    exit 0
fi
# api graphql: mutation vs query by argv scan.
if [[ "$*" == *mutation* ]]; then
    echo '{"data":{"resolveReviewThread":{"thread":{"isResolved":true}}}}'
    exit 0
fi
cat "$RESOLVE_THREADS_FIXTURE"
MOCKGH
    chmod +x "$MOCKBIN/gh"
    export PATH="$MOCKBIN:$PATH"

    # Two threads: one unresolved (comment 111), one resolved (comment 222).
    cat > "$RESOLVE_THREADS_FIXTURE" <<'JSON'
{
  "data": { "repository": { "pullRequest": { "reviewThreads": { "nodes": [
    { "id": "T_UNRESOLVED", "isResolved": false,
      "comments": { "nodes": [ { "databaseId": 111 } ] } },
    { "id": "T_RESOLVED", "isResolved": true,
      "comments": { "nodes": [ { "databaseId": 222 } ] } }
  ] } } } }
}
JSON
}

@test "resolves the unresolved thread containing the comment" {
    run "$RESOLVE" 5 111
    [ "$status" -eq 0 ]
    [[ "$output" == "resolved: thread T_UNRESOLVED (O/N#5, comment 111)" ]]
}

@test "already-resolved thread is a no-op success" {
    run "$RESOLVE" 5 222
    [ "$status" -eq 0 ]
    [[ "$output" == "already resolved: thread T_RESOLVED (O/N#5)" ]]
}

@test "unknown comment id fails with exit 1" {
    run "$RESOLVE" 5 999
    [ "$status" -eq 1 ]
    [[ "$output" == *"no review thread contains comment 999 on O/N#5"* ]]
}

@test "no args prints usage with exit 64" {
    run "$RESOLVE"
    [ "$status" -eq 64 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "single arg is a usage error (exit 64)" {
    run "$RESOLVE" 5
    [ "$status" -eq 64 ]
    [[ "$output" == *"need <pr-number> <comment-id>"* ]]
}

@test "non-numeric PR number is rejected (exit 64)" {
    run "$RESOLVE" abc 111
    [ "$status" -eq 64 ]
    [[ "$output" == *"PR number must be numeric (got: abc)"* ]]
}

@test "non-numeric comment id is rejected (exit 64)" {
    run "$RESOLVE" 5 xyz
    [ "$status" -eq 64 ]
    [[ "$output" == *"comment id must be numeric (got: xyz)"* ]]
}

@test "--help prints usage with exit 0" {
    run "$RESOLVE" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
}
