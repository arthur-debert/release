#!/usr/bin/env bats

# Tests for the plain HTTPS URL shape (#310).

load helper

# Helper: write a small file at $1 with content $2 and echo its sha256.
make_url_asset() {
    local path="$1" content="$2"
    printf '%s' "$content" > "$path"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    else
        shasum -a 256 "$path" | awk '{print $1}'
    fi
}

@test "url mode: downloads a plain HTTPS URL to dest/<basename>" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/model.onnx" "fake-onnx-bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "segformer": {
        "url":  "https://huggingface.co/Xenova/foo/resolve/main/model.onnx",
        "dest": "static/models"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f static/models/model.onnx ]]
    [[ "$(cat static/models/model.onnx)" == "fake-onnx-bytes" ]]
}

@test "url mode: filename overrides URL basename" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/model.onnx" "fake-onnx-bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "segformer": {
        "url":      "https://huggingface.co/Xenova/foo/resolve/main/model.onnx",
        "filename": "segformer-b0-ade-512.onnx",
        "dest":     "static/models"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f static/models/segformer-b0-ade-512.onnx ]]
    [[ ! -f static/models/model.onnx ]]
}

@test "url mode: query string is stripped when deriving basename" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "thing": {
        "url":  "https://cdn.example.com/asset.bin?token=abc&v=1",
        "dest": "vendor"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f vendor/asset.bin ]]
}

@test "url mode: sha256 match succeeds" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    local hash
    hash="$(make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "good-bytes")"

    cat > deps.json <<JSON
{
    "thing": {
        "url":    "https://cdn.example.com/asset.bin",
        "dest":   "vendor",
        "sha256": "$hash"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ -f vendor/asset.bin ]]
}

@test "url mode: sha256 mismatch hard-fails" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "actual-bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "thing": {
        "url":    "https://cdn.example.com/asset.bin",
        "dest":   "vendor",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"sha256 mismatch"* ]]
}

@test "url mode: sha256 mismatch hard-fails even with --soft" {
    # Integrity failure must NEVER be swallowed by --soft. A wrong
    # hash means we got the wrong bytes — the consumer would link a
    # corrupted artifact silently.
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "actual-bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "thing": {
        "url":      "https://cdn.example.com/asset.bin",
        "dest":     "vendor",
        "sha256":   "0000000000000000000000000000000000000000000000000000000000000000",
        "optional": true
    }
}
JSON

    run "$FETCH_DEPS" --soft --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"sha256 mismatch"* ]]
}

@test "url mode: download failure under --soft exits 0 with warning" {
    setup_mock_curl
    # No mock asset → curl returns 22.

    cat > deps.json <<'JSON'
{
    "ghost": {
        "url":  "https://cdn.example.com/missing.bin",
        "dest": "vendor"
    }
}
JSON

    run "$FETCH_DEPS" --soft --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"soft mode"* ]]
    [[ ! -f vendor/missing.bin ]]
}

@test "url mode: --if-missing skips when stamp matches (sha256 identity)" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    local hash
    hash="$(make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "stable-bytes")"

    cat > deps.json <<JSON
{
    "thing": {
        "url":    "https://cdn.example.com/asset.bin",
        "dest":   "vendor",
        "sha256": "$hash"
    }
}
JSON

    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]

    run "$FETCH_DEPS" --if-missing --target aarch64-apple-darwin
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"up to date"* ]]
}

@test "url mode: --if-missing refetches when sha256 changes" {
    setup_mock_curl
    mkdir -p "$HARNESS_WORKSPACE/_mock_releases"
    make_url_asset "$HARNESS_WORKSPACE/_mock_releases/asset.bin" "v2-bytes" >/dev/null

    cat > deps.json <<'JSON'
{
    "thing": {
        "url":    "https://cdn.example.com/asset.bin",
        "dest":   "vendor",
        "sha256": "BOGUS"
    }
}
JSON

    # Plant a stale stamp for an old hash; current config asks for BOGUS.
    mkdir -p .deps
    printf 'sha256:OLDHASH' > .deps/thing.stamp

    # Will fail because sha256 mismatch, but the important thing is
    # that --if-missing did not skip.
    run "$FETCH_DEPS" --if-missing --target aarch64-apple-darwin
    [[ "$output" == *"thing → vendor/asset.bin"* ]]
}

@test "url mode: missing 'url' is a config error" {
    setup_mock_curl
    cat > deps.json <<'JSON'
{
    "broken": {
        "dest": "vendor"
    }
}
JSON
    # No url, no repo — falls through the dispatcher to the github
    # path, which complains about missing repo. Either way, it's a
    # config error.
    run "$FETCH_DEPS" --target aarch64-apple-darwin
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"missing"* ]]
}
