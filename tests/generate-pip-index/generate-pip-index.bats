#!/usr/bin/env bats

# Contract suite for bin-internal/generate-pip-index.sh — the PEP 503 pip
# index generator for release_core wheels (epic #757 WS2 / release#759).
#
# Fully offline: a stub `gh` on PATH applies the script's own `--jq` filter to
# a fixture releases JSON with the REAL jq binary, so the test exercises the
# exact prerelease/draft + wheel-asset predicate the script ships (not a
# re-implementation of it). Asserts:
#   - prerelease / draft releases are EXCLUDED (RC-pollution guard, #689-class)
#   - the index is version-agnostic (parses whatever versions appear)
#   - PEP 503 layout: simple/index.html + simple/release-core/index.html
#   - hrefs point at the release-asset browser_download_url

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/generate-pip-index.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"

  # Fixture: mixed releases — two shippable, one prerelease, one draft, plus a
  # release with no wheel asset. Deliberately NOT 0.0.1 (version-agnostic).
  cat > releases.json <<'EOF'
[
  {
    "tag_name": "v3.2.0", "draft": false, "prerelease": false,
    "assets": [
      {"name": "release_core-3.2.0-py3-none-any.whl",
       "browser_download_url": "https://github.com/o/r/releases/download/v3.2.0/release_core-3.2.0-py3-none-any.whl"}
    ]
  },
  {
    "tag_name": "v3.1.5-release-rc", "draft": false, "prerelease": true,
    "assets": [
      {"name": "release_core-3.1.5rc0-py3-none-any.whl",
       "browser_download_url": "https://github.com/o/r/releases/download/v3.1.5-release-rc/release_core-3.1.5rc0-py3-none-any.whl"}
    ]
  },
  {
    "tag_name": "v3.1.0", "draft": false, "prerelease": false,
    "assets": [
      {"name": "release_core-3.1.0-py3-none-any.whl",
       "browser_download_url": "https://github.com/o/r/releases/download/v3.1.0/release_core-3.1.0-py3-none-any.whl"}
    ]
  },
  {
    "tag_name": "v3.0.9-draft", "draft": true, "prerelease": false,
    "assets": [
      {"name": "release_core-3.0.9-py3-none-any.whl",
       "browser_download_url": "https://github.com/o/r/releases/download/v3.0.9-draft/release_core-3.0.9-py3-none-any.whl"}
    ]
  },
  {
    "tag_name": "v3.0.0", "draft": false, "prerelease": false,
    "assets": [
      {"name": "some-other-tool-1.0.tar.gz",
       "browser_download_url": "https://github.com/o/r/releases/download/v3.0.0/some-other-tool-1.0.tar.gz"}
    ]
  }
]
EOF

  # Stub gh: for `gh api <path> --jq <expr>`, apply <expr> to the fixture with
  # the real jq (-r raw output, matching `gh api --jq`).
  mkdir -p bin-stub
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "api" ]; then
  jqexpr=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --jq) jqexpr="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  jq -r "$jqexpr" "${FIXTURE}"
  exit 0
fi
exit 0
EOF
  chmod +x bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"
  export FIXTURE="$PWD/releases.json"
  export OUTPUT_DIR="$PWD/out"
}

teardown() {
  cd / || true
  rm -rf "$TMP"
}

@test "writes PEP 503 layout (root + project index)" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ -f "$OUTPUT_DIR/simple/index.html" ]
  [ -f "$OUTPUT_DIR/simple/release-core/index.html" ]
}

@test "root index links to the release-core project" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'href="release-core/"' "$OUTPUT_DIR/simple/index.html"
}

@test "includes both shippable wheels (version-agnostic)" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'release_core-3.2.0-py3-none-any.whl' "$OUTPUT_DIR/simple/release-core/index.html"
  grep -q 'release_core-3.1.0-py3-none-any.whl' "$OUTPUT_DIR/simple/release-core/index.html"
}

@test "hrefs point at the release-asset download URL" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'href="https://github.com/o/r/releases/download/v3.2.0/release_core-3.2.0-py3-none-any.whl"' \
    "$OUTPUT_DIR/simple/release-core/index.html"
}

@test "EXCLUDES the prerelease (-release-rc) wheel" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  ! grep -q '3.1.5rc0' "$OUTPUT_DIR/simple/release-core/index.html"
}

@test "EXCLUDES the draft release wheel" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  ! grep -q '3.0.9' "$OUTPUT_DIR/simple/release-core/index.html"
}

@test "ignores a release with no release_core wheel asset" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  ! grep -q 'some-other-tool' "$OUTPUT_DIR/simple/release-core/index.html"
}

@test "fails loudly when no non-prerelease wheel exists" {
  # Fixture with only a prerelease → nothing to serve → exit 1.
  cat > only-rc.json <<'EOF'
[
  {"tag_name": "v3.0.0-release-rc", "draft": false, "prerelease": true,
   "assets": [{"name": "release_core-3.0.0rc0-py3-none-any.whl",
     "browser_download_url": "https://github.com/o/r/releases/download/v3.0.0-release-rc/release_core-3.0.0rc0-py3-none-any.whl"}]}
]
EOF
  export FIXTURE="$PWD/only-rc.json"
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"nothing to serve"* ]]
}
