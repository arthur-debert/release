source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"
FETCH_DEPS="$BIN/fetch-deps"

setup() {
    harness_create_workspace_notrap
    cd "$HARNESS_WORKSPACE"
    # Prevent ambient tokens from leaking into tests. Tests that need
    # authentication (e.g. the authenticated API path test) set GH_TOKEN
    # explicitly. Without this, a shell-level GH_TOKEN makes fetch-deps
    # take the API path, which the simple mock_curl does not handle.
    unset GH_TOKEN GITHUB_TOKEN 2>/dev/null || true
}

teardown() {
    cd /
    harness_cleanup
}

# Create a tarball containing a single binary at the expected path.
# Usage: make_binary_tarball <output.tar.gz> <binary-name> [<nested-dir>]
make_binary_tarball() {
    local out="$1" bin_name="$2" nested="${3:-}"
    local staging="$HARNESS_WORKSPACE/_staging_$$"
    mkdir -p "$staging"

    if [[ -n "$nested" ]]; then
        mkdir -p "$staging/$nested"
        printf '#!/bin/sh\necho hello\n' > "$staging/$nested/$bin_name"
        chmod +x "$staging/$nested/$bin_name"
    else
        printf '#!/bin/sh\necho hello\n' > "$staging/$bin_name"
        chmod +x "$staging/$bin_name"
    fi

    tar -czf "$out" -C "$staging" .
    rm -rf "$staging"
}

# Create a tarball with multiple files for extract-mode testing.
# Usage: make_multi_tarball <output.tar.gz> <file1:content> [<file2:content> ...]
make_multi_tarball() {
    local out="$1"; shift
    local staging="$HARNESS_WORKSPACE/_staging_$$"
    mkdir -p "$staging"

    for spec in "$@"; do
        local path="${spec%%:*}"
        local content="${spec#*:}"
        mkdir -p "$staging/$(dirname "$path")"
        printf '%s' "$content" > "$staging/$path"
    done

    tar -czf "$out" -C "$staging" .
    rm -rf "$staging"
}

# Write a deps.json that points to a local file:// URL (via a mock server).
# Instead, we override the download function by creating a tarball at the
# exact path fetch-deps will try to download. We do this by wrapping curl.
#
# Usage: mock_release <dep-name> <version> <asset-filename> <tarball-path>
# Sets up a directory that serves as a fake GitHub release.
mock_release() {
    local dep="$1" version="$2" asset="$3" tarball="$4"
    local mock_dir="$HARNESS_WORKSPACE/_mock_releases"
    # repo doesn't matter for the mock — we just need the tarball at the right path
    mkdir -p "$mock_dir"
    cp "$tarball" "$mock_dir/$asset"
}

# A wrapper script that intercepts curl calls and serves local files.
setup_mock_curl() {
    local mock_dir="$HARNESS_WORKSPACE/_mock_releases"
    local mock_curl="$HARNESS_WORKSPACE/_bin/curl"
    mkdir -p "$HARNESS_WORKSPACE/_bin"

    cat > "$mock_curl" <<'MOCKCURL'
#!/usr/bin/env bash
# Mock curl that serves files from _mock_releases/ instead of GitHub.
# Parses -o <output> and the URL to find the asset filename.
output=""
url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o)   output="$2"; shift 2 ;;
        -H)   shift 2 ;;
        -*)   shift ;;
        *)    url="$1"; shift ;;
    esac
done

# Extract asset filename from URL (strip query string, last path component)
asset="${url%%\?*}"
asset="$(basename "$asset")"
mock_dir="$(dirname "$0")/../_mock_releases"

if [[ -f "$mock_dir/$asset" ]]; then
    cp "$mock_dir/$asset" "$output"
    exit 0
else
    echo "mock curl: asset not found: $asset (in $mock_dir)" >&2
    exit 22
fi
MOCKCURL
    chmod +x "$mock_curl"
    export PATH="$HARNESS_WORKSPACE/_bin:$PATH"
}
