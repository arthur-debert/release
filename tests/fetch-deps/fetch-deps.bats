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
    [[ "$status" -eq 0 ]]
    [[ -f resources/tree-sitter-lex.wasm ]]
    [[ "$(cat resources/tree-sitter-lex.wasm)" == "wasm-content" ]]
    [[ -f resources/queries/highlights.scm ]]
    [[ -f resources/queries/injections.scm ]]
    [[ -f resources/embedded-grammars.json ]]
}

@test "extract mode: missing source fails the dep loudly" {
    # A source listed in `extract` that is absent from the archive is a
    # real error (the build will be missing a file), not a soft skip.
    # It must fail the dep with a clear message — never report a false ✓.
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
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"not found in archive"* ]]
    [[ "$output" == *"dep(s) failed"* ]]
    # No stamp should be written when the dep failed.
    [[ ! -f .deps/ts.stamp ]]
}

# ─── CRLF regression (lex-fmt/lexed#142) ─────────────────

# jq on Windows emits \r\n as the *line terminator* on every output
# line (it opens stdout in text mode). The `extract` @tsv map builder
# forgot the `| tr -d '\r'` that every other jq call uses, so each map
# line arrived as "src\tdst\r\n". Command substitution strips trailing
# \n but NOT \r, so all-but-last `dst` carried a trailing \r:
# `cp "$found" "resources\r/"` then created a bogus `resources<CR>/`
# dir and the real file went missing → downstream `vite build` couldn't
# resolve ../resources/*.wasm. We reproduce Windows jq with a `jq` shim
# on PATH that appends a CR to every output line, then assert the files
# land in the CORRECT dirs with no CR-suffixed dir.
#
# Install a `jq` wrapper that mimics jq-on-Windows: real jq, but every
# output line gets a trailing \r (\n → \r\n). Placed ahead of the real
# jq on PATH for the duration of the test.
crlf_jq_shim() {
    local real_jq; real_jq="$(command -v jq)"
    mkdir -p "$HARNESS_WORKSPACE/_bin"
    cat > "$HARNESS_WORKSPACE/_bin/jq" <<SHIM
#!/usr/bin/env bash
# Windows jq emulation: append CR to every stdout line.
"$real_jq" "\$@" | awk '{print \$0 "\r"}'
SHIM
    chmod +x "$HARNESS_WORKSPACE/_bin/jq"
    export PATH="$HARNESS_WORKSPACE/_bin:$PATH"
}

@test "extract mode: CRLF jq output lands files in correct dirs (no CR-suffixed dir)" {
    setup_mock_curl
    crlf_jq_shim
    make_multi_tarball "$HARNESS_WORKSPACE/ts.tar.gz" \
        "tree-sitter-lex.wasm:wasm-content" \
        "queries/highlights.scm:highlight-content"
    mock_release ts v0.11.0 "tree-sitter.tar.gz" "$HARNESS_WORKSPACE/ts.tar.gz"

    cat > deps.json <<'JSON'
{
    "ts": {
        "repo": "test/ts",
        "version": "v0.11.0",
        "asset": "tree-sitter.tar.gz",
        "extract": {
            "tree-sitter-lex.wasm": "resources",
            "queries": "resources/queries"
        }
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]

    # Files landed in the correct dirs...
    [[ -f resources/tree-sitter-lex.wasm ]]
    [[ "$(cat resources/tree-sitter-lex.wasm)" == "wasm-content" ]]
    [[ -f resources/queries/highlights.scm ]]

    # ...and NO CR-suffixed bogus dir was created.
    [[ ! -d "$(printf 'resources\r')" ]]
    run bash -c 'ls -1d resources* 2>/dev/null'
    [[ "$output" != *$'\r'* ]]
}

# Unit-level: drive apply_extract_map directly with a CRLF-laden map
# (the exact shape that reaches the function on Windows: every entry
# but the last carries a trailing \r). Proves the per-field hardening
# strips \r even if a future jq builder regresses and drops `tr -d`.
@test "apply_extract_map: strips \\r per field (defense in depth)" {
    mkdir -p root/sub
    printf 'A' > root/a.wasm
    printf 'B' > root/sub/b.scm

    # First entry carries trailing \r (non-last), second is clean.
    local map
    map="$(printf 'a.wasm\tresources\r\nsub\tresources/queries\n')"

    run bash -c '
        source "'"$FETCH_DEPS"'"
        apply_extract_map root "$1"
    ' _ "$map"

    [[ "$status" -eq 0 ]]
    [[ -f resources/a.wasm ]]
    [[ -d resources/queries ]]
    [[ ! -d "$(printf 'resources\r')" ]]
}

# A failed cp (e.g. dest dir cannot be created) must fail the dep loudly
# — never print a false ✓. Here `resources` is pre-created as a *file*,
# so `mkdir -p resources` fails and the dep must fail non-zero.
@test "extract mode: failed cp/mkdir fails the dep (no false success)" {
    setup_mock_curl
    make_multi_tarball "$HARNESS_WORKSPACE/ts.tar.gz" \
        "tree-sitter-lex.wasm:wasm-content"
    mock_release ts v0.11.0 "tree-sitter.tar.gz" "$HARNESS_WORKSPACE/ts.tar.gz"

    # Pre-create `resources` as a regular file so `mkdir -p resources`
    # (inside apply_extract_map) fails.
    printf 'i am a file, not a dir' > resources

    cat > deps.json <<'JSON'
{
    "ts": {
        "repo": "test/ts",
        "version": "v0.11.0",
        "asset": "tree-sitter.tar.gz",
        "extract": {
            "tree-sitter-lex.wasm": "resources"
        }
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"dep(s) failed"* ]]
    # No false ✓ for the file that never got copied.
    [[ "$output" != *"✓ tree-sitter-lex.wasm"* ]]
    # And no stamp written.
    [[ ! -f .deps/ts.stamp ]]
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

# ─── Authenticated (API) download path ────────────────────

@test "authenticated path uses API to resolve asset URL" {
    # Mock curl that handles both the API lookup and the asset download.
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases" "$HARNESS_WORKSPACE/_bin"
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    cp "$HARNESS_WORKSPACE/mybin.tar.gz" "$HARNESS_WORKSPACE/_mock_releases/mycli-aarch64-apple-darwin.tar.gz"

    cat > "$HARNESS_WORKSPACE/_bin/curl" <<'MOCKCURL'
#!/usr/bin/env bash
output="" ; url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o)   output="$2"; shift 2 ;;
        -H)   shift 2 ;;
        -*)   shift ;;
        *)    url="$1"; shift ;;
    esac
done
mock_dir="$(dirname "$0")/../_mock_releases"
if [[ "$url" == *"api.github.com"* && "$url" == *"/releases/tags/"* ]]; then
    # API call: return a fake release JSON with one asset
    cat <<JSON
{"assets":[{"name":"mycli-aarch64-apple-darwin.tar.gz","url":"https://fake-api/asset/12345"}]}
JSON
    exit 0
elif [[ "$url" == *"fake-api/asset"* ]]; then
    # Asset download via API URL
    cp "$mock_dir/mycli-aarch64-apple-darwin.tar.gz" "$output"
    exit 0
else
    echo "mock curl: unexpected url: $url" >&2; exit 22
fi
MOCKCURL
    chmod +x "$HARNESS_WORKSPACE/_bin/curl"
    export PATH="$HARNESS_WORKSPACE/_bin:$PATH"
    export GH_TOKEN="fake-token"

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

# ─── {{node_platform}} / {{node_arch}} templates ──────────

@test "{{node_platform}} and {{node_arch}} expand in dest" {
    # Electron extraResource convention: per-platform subdir under
    # resources/bin/. {{node_platform}}-{{node_arch}} → darwin-arm64
    # on macOS arm64.
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
        "dest": "resources/bin/{{node_platform}}-{{node_arch}}"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -x "resources/bin/darwin-arm64/mycli" ]]
}

@test "{{node_platform}} expands to linux for linux target" {
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-x86_64-unknown-linux-gnu.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "out/{{node_platform}}-{{node_arch}}"
    }
}
JSON

    run "$FETCH_DEPS" --target x86_64-unknown-linux-gnu
    [[ "$status" -eq 0 ]]
    [[ -x "out/linux-x64/mycli" ]]
}

@test "{{node_arch}} accepts LLVM-style arm64 triple (not just aarch64)" {
    # LLVM/Clang spelling: arm64-apple-darwin. Rust spells the same
    # target aarch64-apple-darwin. Both should map to node arm64.
    setup_mock_curl
    make_binary_tarball "$HARNESS_WORKSPACE/mybin.tar.gz" "mycli"
    mock_release mycli v1.0.0 "mycli-arm64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/mybin.tar.gz"

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}.tar.gz",
        "binary": "mycli",
        "dest": "out/{{node_platform}}-{{node_arch}}"
    }
}
JSON

    run "$FETCH_DEPS" --target arm64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -x "out/darwin-arm64/mycli" ]]
}

@test "{{node_platform}} expands to win32 for windows target" {
    # Use simple mode (no binary) to avoid the .exe path quirk in the mock harness.
    setup_mock_curl
    make_multi_tarball "$HARNESS_WORKSPACE/pkg.zip" "marker:ok"
    # mock_release just copies the file; the .zip extension matters
    # for extract_archive to pick the right tool.
    mock_release mycli v1.0.0 "mycli-x86_64-pc-windows-msvc.zip" "$HARNESS_WORKSPACE/pkg.zip"
    # The "zip" we made is actually a tarball; swap it for a real one
    # by re-creating it as a zip if `zip` is available; otherwise skip.
    if ! command -v zip >/dev/null 2>&1; then
        skip "zip not available in this environment"
    fi
    rm "$HARNESS_WORKSPACE/_mock_releases/mycli-x86_64-pc-windows-msvc.zip"
    ( cd "$HARNESS_WORKSPACE" && mkdir -p _zipstage && echo ok > _zipstage/marker && \
      cd _zipstage && zip -q "$HARNESS_WORKSPACE/_mock_releases/mycli-x86_64-pc-windows-msvc.zip" marker )

    cat > deps.json <<'JSON'
{
    "mycli": {
        "repo": "test/mycli",
        "version": "v1.0.0",
        "asset": "mycli-{{target}}{{ext}}",
        "dest": "out/{{node_platform}}-{{node_arch}}"
    }
}
JSON

    run "$FETCH_DEPS" --target x86_64-pc-windows-msvc
    [[ "$status" -eq 0 ]]
    [[ -f "out/win32-x64/marker" ]]
}

# ─── --soft / "optional": true ───────────────────────────

@test "download failure exits non-zero by default" {
    setup_mock_curl
    # No mock_release call -> mock curl returns 22 (asset not found).
    cat > deps.json <<'JSON'
{
    "ghost": {
        "repo": "test/ghost",
        "version": "v1.0.0",
        "asset": "ghost-{{target}}.tar.gz",
        "binary": "ghost",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"dep(s) failed"* ]]
}

@test "--soft: download failure exits 0 with warning" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "ghost": {
        "repo": "test/ghost",
        "version": "v1.0.0",
        "asset": "ghost-{{target}}.tar.gz",
        "binary": "ghost",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --soft --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"soft mode"* ]]
    [[ ! -f bin/ghost ]]
}

@test "\"optional\": true: per-entry soft-fail exits 0 with warning" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "ghost": {
        "repo": "test/ghost",
        "version": "v1.0.0",
        "asset": "ghost-{{target}}.tar.gz",
        "binary": "ghost",
        "dest": "bin",
        "optional": true
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"soft mode"* ]]
    [[ ! -f bin/ghost ]]
}

@test "\"optional\": true: hard dep in same run still fails the script" {
    setup_mock_curl
    # one ok dep that downloads fine + one optional dep that fails
    # + one hard dep that fails → exit non-zero because of the hard one.
    make_binary_tarball "$HARNESS_WORKSPACE/ok.tar.gz" "ok-cli"
    mock_release ok-cli v1.0.0 "ok-cli-aarch64-apple-darwin.tar.gz" "$HARNESS_WORKSPACE/ok.tar.gz"

    cat > deps.json <<'JSON'
{
    "ok-cli": {
        "repo": "test/ok",
        "version": "v1.0.0",
        "asset": "ok-cli-{{target}}.tar.gz",
        "binary": "ok-cli",
        "dest": "bin"
    },
    "soft-ghost": {
        "repo": "test/soft-ghost",
        "version": "v1.0.0",
        "asset": "soft-ghost-{{target}}.tar.gz",
        "binary": "soft-ghost",
        "dest": "bin",
        "optional": true
    },
    "hard-ghost": {
        "repo": "test/hard-ghost",
        "version": "v1.0.0",
        "asset": "hard-ghost-{{target}}.tar.gz",
        "binary": "hard-ghost",
        "dest": "bin"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"1 dep(s) failed"* ]]
    [[ "$output" == *"soft mode"* ]]
    [[ -x bin/ok-cli ]]
}

@test "--soft does not mask config errors (missing required fields)" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "bad": {
        "repo": "test/bad"
    }
}
JSON

    # Missing required fields are bugs in deps.json — not transient
    # runtime errors. --soft must still hard-fail so the developer sees
    # the typo. (Contrast with download failures, which --soft swallows.)
    run "$FETCH_DEPS" --soft --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing 'version'"* ]]
    [[ "$output" != *"soft mode"* ]]
}
