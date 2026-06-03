#!/usr/bin/env bats

# Tests for manifest-driven iteration (#309).

load helper

# Drop a single-file "asset" into the mock release dir under a given
# filename. Used to mock per-grammar wasm assets.
mock_asset_file() {
    local name="$1" content="$2"
    local mock_dir="$HARNESS_WORKSPACE/_mock_releases"
    mkdir -p "$mock_dir"
    printf '%s' "$content" > "$mock_dir/$name"
}

@test "iter mode: iterates manifest with binary-as (single-file assets)" {
    setup_mock_curl

    # Mock per-grammar wasm files. Iteration mode treats each asset
    # as a single-file binary (no archive extraction) when binary-as
    # is set.
    mock_asset_file "tree-sitter-python.wasm" "py-wasm"
    mock_asset_file "tree-sitter-rust.wasm"   "rust-wasm"

    # Manifest already on disk (the real flow would have an earlier
    # deps.json entry extract this from a tarball, but the iteration
    # logic itself doesn't care where it came from).
    mkdir -p resources
    cat > resources/embedded-grammars.json <<'JSON'
{
    "grammars": [
        { "name": "python", "repo": "tree-sitter/tree-sitter-python", "version": "v0.20.4", "wasm_asset": "tree-sitter-python.wasm" },
        { "name": "rust",   "repo": "tree-sitter/tree-sitter-rust",   "version": "v0.20.3", "wasm_asset": "tree-sitter-rust.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "embedded-grammars": {
        "from-manifest": "resources/embedded-grammars.json",
        "for-each":      ".grammars[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "resources/embedded-grammars/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f resources/embedded-grammars/python/parser.wasm ]]
    [[ -f resources/embedded-grammars/rust/parser.wasm ]]
    [[ "$(cat resources/embedded-grammars/python/parser.wasm)" == "py-wasm" ]]
    [[ "$(cat resources/embedded-grammars/rust/parser.wasm)" == "rust-wasm" ]]
}

@test "iter mode: per-item template substitutes {{.field}}" {
    setup_mock_curl
    mock_asset_file "tree-sitter-go.wasm" "go-wasm"

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "name": "go", "repo": "tree-sitter/tree-sitter-go", "version": "v0.20.0", "wasm_asset": "tree-sitter-go.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f out/go/parser.wasm ]]
}

@test "iter mode: raw-files downloads from raw.githubusercontent.com" {
    setup_mock_curl
    mock_asset_file "tree-sitter-py.wasm"  "py-wasm"
    mock_asset_file "highlights.scm"        "(comment) @comment"

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        {
            "name":          "py",
            "repo":          "tree-sitter/tree-sitter-python",
            "version":       "v0.20.4",
            "wasm_asset":    "tree-sitter-py.wasm",
            "queries_path":  "queries/highlights.scm"
        }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm",
        "raw-files":     { "{{.queries_path}}": "highlights.scm" }
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f out/py/parser.wasm ]]
    [[ -f out/py/highlights.scm ]]
    [[ "$(cat out/py/highlights.scm)" == "(comment) @comment" ]]
}

@test "iter mode: per-item stamps are independent" {
    setup_mock_curl
    mock_asset_file "g-a.wasm" "a-wasm"
    mock_asset_file "g-b.wasm" "b-wasm"

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "name": "a", "repo": "ex/a", "version": "v1.0.0", "wasm_asset": "g-a.wasm" },
        { "name": "b", "repo": "ex/b", "version": "v2.0.0", "wasm_asset": "g-b.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f .deps/grammars/a.stamp ]]
    [[ -f .deps/grammars/b.stamp ]]
    [[ "$(cat .deps/grammars/a.stamp)" == "v1.0.0 aarch64-apple-darwin" ]]
    [[ "$(cat .deps/grammars/b.stamp)" == "v2.0.0 aarch64-apple-darwin" ]]
}

@test "iter mode: --if-missing skips items with matching stamps" {
    setup_mock_curl
    mock_asset_file "x.wasm" "x"

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "name": "x", "repo": "ex/x", "version": "v1.0.0", "wasm_asset": "x.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]

    run "$FETCH_DEPS" --if-missing --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"up to date"* ]]
}

@test "iter mode: missing manifest fails the dep" {
    setup_mock_curl

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/does-not-exist.json",
        "for-each":      ".items[]",
        "repo":          "ex/x",
        "version":       "v1.0.0",
        "asset":         "x.wasm",
        "dest":          "out"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"manifest not found"* ]]
}

@test "iter mode: for-each beyond the supported subset fails clearly" {
    setup_mock_curl

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{ "items": [ { "name": "a" } ] }
JSON

    # A jq filter (pipe/select) is outside the supported dotted-path subset
    # now that for-each is evaluated in-process without a jq engine.
    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[] | select(.name)",
        "repo":          "ex/x",
        "version":       "v1.0.0",
        "asset":         "x.wasm",
        "dest":          "out"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"unsupported for-each expression"* ]]
}

@test "iter mode: item without 'name' is a hard error" {
    setup_mock_curl

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "repo": "ex/x", "version": "v1.0.0", "wasm_asset": "x.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing 'name' field"* ]]
}

@test "iter mode: missing from-manifest is a config error" {
    setup_mock_curl

    # has for-each but no from-manifest → die("missing 'from-manifest'")
    # is unreachable since the dispatcher only routes to fetch_iter
    # when from-manifest is present. So instead, test the inverse:
    # from-manifest present but for-each missing.
    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{ "items": [] }
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing 'for-each'"* ]]
}

@test "iter mode: empty iteration succeeds with no work" {
    setup_mock_curl

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{ "items": [] }
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
}

@test "iter mode: one bad item fails the dep (hard by default)" {
    setup_mock_curl
    mock_asset_file "g-ok.wasm" "ok"
    # g-missing.wasm intentionally not mocked

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "name": "ok",      "repo": "ex/ok",  "version": "v1.0.0", "wasm_asset": "g-ok.wasm" },
        { "name": "missing", "repo": "ex/bad", "version": "v1.0.0", "wasm_asset": "g-missing.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"dep(s) failed"* ]]
    # Good item still installed
    [[ -f out/ok/parser.wasm ]]
}

@test "iter mode: declaration order is preserved (manifest-extractor runs before iter dep)" {
    # Real-world case: an "extract" dep pulls a JSON manifest out of a
    # tarball into resources/, and a later iter dep reads that
    # manifest via from-manifest. If keys were processed alphabetically
    # the iter dep ('grammars' < 'tree-sitter') would run first and
    # fail because the manifest isn't on disk yet.
    setup_mock_curl
    mock_asset_file "g-rs.wasm" "rs-wasm"

    # Mock the tree-sitter tarball that contains the manifest.
    make_multi_tarball "$HARNESS_WORKSPACE/ts.tar.gz" \
        'shared/embedded-grammars.json:{"items":[{"name":"rs","repo":"ex/rs","version":"v1.0.0","wasm_asset":"g-rs.wasm"}]}'
    mock_release ts v0.11.0 "tree-sitter.tar.gz" "$HARNESS_WORKSPACE/ts.tar.gz"

    cat > deps.json <<'JSON'
{
    "tree-sitter": {
        "repo": "ex/ts",
        "version": "v0.11.0",
        "asset": "tree-sitter.tar.gz",
        "extract": {
            "shared/embedded-grammars.json": "resources"
        }
    },
    "grammars": {
        "from-manifest": "resources/embedded-grammars.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f resources/embedded-grammars.json ]]
    [[ -f out/rs/parser.wasm ]]
}

@test "iter mode: optional: true makes one bad item soft-fail the whole dep" {
    setup_mock_curl
    mock_asset_file "g-ok.wasm" "ok"

    mkdir -p resources
    cat > resources/manifest.json <<'JSON'
{
    "items": [
        { "name": "ok",      "repo": "ex/ok",  "version": "v1.0.0", "wasm_asset": "g-ok.wasm" },
        { "name": "missing", "repo": "ex/bad", "version": "v1.0.0", "wasm_asset": "g-missing.wasm" }
    ]
}
JSON

    cat > deps.json <<'JSON'
{
    "grammars": {
        "from-manifest": "resources/manifest.json",
        "for-each":      ".items[]",
        "repo":          "{{.repo}}",
        "version":       "{{.version}}",
        "asset":         "{{.wasm_asset}}",
        "dest":          "out/{{.name}}",
        "binary-as":     "parser.wasm",
        "optional":      true
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"soft mode"* ]]
    [[ -f out/ok/parser.wasm ]]
}
