#!/usr/bin/env bats

load helper

# ─── CLI basics ────────────────────────────────────────────

@test "--help prints usage" {
    run "$FETCH_DEPS" --help
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"deps.json"* ]]
}

@test "--version prints version" {
    run "$FETCH_DEPS" --version
    [[ "$status" -eq 0 ]]
    [[ "$output" == "fetch-deps "* ]]
}

@test "exits 1 when deps.json is missing" {
    run "$FETCH_DEPS"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"config not found"* ]]
}

@test "exits 1 on invalid JSON" {
    echo "not json" > deps.json
    run "$FETCH_DEPS"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"invalid JSON"* ]]
}

@test "exits 1 on empty config" {
    echo '{}' > deps.json
    run "$FETCH_DEPS"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"no deps defined"* ]]
}

# ─── Binary mode ──────────────────────────────────────────

@test "binary mode: fetches and installs a binary (flat layout)" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -x bin/mycli ]]
    [[ "$(bin/mycli)" == "hello" ]]
}

@test "binary mode: fetches from nested tarball layout" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli" "mycli-aarch64-apple-darwin"
    mock_release mycli v2.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v2.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "resources"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -x resources/mycli ]]
}

@test "binary mode: fails when binary not found in archive" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "wrong-name"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"not found in archive"* ]]
}

# ─── Extract mode ─────────────────────────────────────────

@test "extract mode: copies files to specified destinations" {
    setup_mock_curl
    make_multi_tarball "$HARNESS_WORKSPACE/ts.tar.gz" \
        "tree-sitter-lex.wasm:wasm-content" \
        "queries/highlights.scm:highlight-content" \
        "queries/injections.scm:injection-content" \
        "shared/embedded-grammars.json:{\"grammars\":[]}"
    mock_release ts v0.11.0 "tree-sitter.tar.gz" "$HARNESS_WORKSPACE/ts.tar.gz"

    cat > deps.json <<'JSON'
{
    "ts": {
        "repo": "test/ts",
        "version": "v0.11.0",
        "asset": "tree-sitter.tar.gz",
        "extract": {
            "tree-sitter-lex.wasm": "resources",
            "queries": "resources/queries",
            "shared/embedded-grammars.json": "resources"
        }
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -f resources/tree-sitter-lex.wasm ]]
    [[ "$(cat resources/tree-sitter-lex.wasm)" == "wasm-content" ]]
    [[ -f resources/queries/highlights.scm ]]
    [[ -f resources/queries/injections.scm ]]
    [[ -f resources/embedded-grammars.json ]]
}

@test "extract mode: handles missing source gracefully" {
    setup_mock_curl
    make_multi_tarball "$HARNESS_WORKSPACE/ts.tar.gz" \
        "tree-sitter-lex.wasm:wasm-content"
    mock_release ts v0.11.0 "tree-sitter.tar.gz" "$HARNESS_WORKSPACE/ts.tar.gz"

    cat > deps.json <<'JSON'
{
    "ts": {
        "repo": "test/ts",
        "version": "v0.11.0",
        "asset": "tree-sitter.tar.gz",
        "extract": {
            "tree-sitter-lex.wasm": "resources",
            "no-such-file.txt": "resources"
        }
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -f resources/tree-sitter-lex.wasm ]]
    [[ "$output" == *"not found in archive"* ]]
}

# ─── Simple extraction (no binary, no extract map) ────────

@test "simple mode: extracts entire archive to dest" {
    setup_mock_curl
    make_multi_tarball "$HARNESS_WORKSPACE/pkg.tar.gz" \
        "pkg/index.js:console.log" \
        "pkg/lib.wasm:wasmdata"
    mock_release mypkg v1.0.0 "mypkg.tar.gz" "$HARNESS_WORKSPACE/pkg.tar.gz"

    cat > deps.json <<'JSON'
{
    "mypkg": {
        "repo": "test/mypkg",
        "version": "v1.0.0",
        "asset": "mypkg.tar.gz",
        "dest": "wasm/mypkg"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -f wasm/mypkg/index.js ]]
    [[ -f wasm/mypkg/lib.wasm ]]
}

# ─── Stamp / --if-missing ─────────────────────────────────

@test "stamp file is created after fetch" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f .deps/mycli.stamp ]]
    [[ "$(cat .deps/mycli.stamp)" == "v1.0.0 aarch64-apple-darwin" ]]
}

@test "--if-missing skips when stamp matches" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    # First fetch
    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]

    # Second fetch with --if-missing should skip
    run "$FETCH_DEPS" --if-missing --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"up to date"* ]]
}

@test "--if-missing fetches when version changes" {
    setup_mock_curl

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v2.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    # Plant a stale stamp
    mkdir -p .deps
    printf 'v1.0.0 aarch64-apple-darwin' > .deps/mycli.stamp

    # Create release for v2
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v2.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    run "$FETCH_DEPS" --if-missing --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"mycli v2.0.0"* ]]
    [[ "$(cat .deps/mycli.stamp)" == "v2.0.0 aarch64-apple-darwin" ]]
}

# ─── Selective deps ───────────────────────────────────────

@test "fetches only named deps when specified" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/a.tar.gz" "dep-a"
    make_binary_tarball "$HARNESS_WORKSPACE/b.tar.gz" "dep-b"
    mock_release dep-a v1.0.0 "dep-a-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/a.tar.gz"
    mock_release dep-b v1.0.0 "dep-b-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/b.tar.gz"

    cat > deps.json <<'JSON'
{
    "dep-a": {
        "repo": "test/a",
        "version": "v1.0.0",
        "asset": "dep-a-{{target}}.tar.gz",
        "binary": "dep-a",
        "dest": "bin"
    },
    "dep-b": {
        "repo": "test/b",
        "version": "v1.0.0",
        "asset": "dep-b-{{target}}.tar.gz",
        "binary": "dep-b",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin dep-a
    echo "$output"
    [[ "$status" -eq 0 ]]
    [[ -x bin/dep-a ]]
    [[ ! -f bin/dep-b ]]
}

# ─── Template expansion ──────────────────────────────────

@test "{{ext}} expands to .tar.gz for non-windows" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}{{ext}}",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -x bin/mycli ]]
}

@test "--config points to alternate config file" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > custom-deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --config custom-deps.json --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -x bin/mycli ]]
}

# ─── Config validation ───────────────────────────────────

@test "errors on missing required fields" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "bad": {
        "repo": "test/bad"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing 'version'"* ]]
}

@test "errors on dep with missing asset" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "bad": {
        "repo": "test/bad",
        "version": "v1.0.0"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing 'asset'"* ]]
}
