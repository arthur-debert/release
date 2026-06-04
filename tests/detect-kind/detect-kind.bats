#!/usr/bin/env bats

# ---------------------------------------------------------------------
# detect-kind: command contract (offline — pure filesystem inspection)
#
# detect-kind is the shell→Python migration canary. The bash original
# was replaced by a thin Python
# shim over release_core.manifest.detect_kind. These tests pin the
# byte-for-byte CLI contract the bash version had — the SAME kind
# strings on stdout, the SAME exit codes — because release-sync,
# release-cut, and done-check consume detect-kind's output directly.
#
# Each test builds a minimal fixture tree under a temp dir (the same
# inline-fixture style as tests/release-sync/docs-site-kind.bats) and
# asserts the classification. No gh, no network, no toolchain.
# ---------------------------------------------------------------------

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  WORK="$(mktemp -d "${BATS_TMPDIR:-/tmp}/detect-kind.XXXXXX")"
  cd "$WORK"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# --- help / usage -----------------------------------------------------

@test "--help prints usage and exits 0" {
  run "$BIN/detect-kind" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"detect-kind"* ]]
  [[ "$output" == *"Usage:"* ]]
}

# --- every Kind the bash version detected -----------------------------

@test "brew-tap via Formula/" {
  mkdir Formula
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "brew-tap" ]
}

@test "brew-tap via Casks/" {
  mkdir Casks
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "brew-tap" ]
}

@test "tree-sitter via grammar.js" {
  touch grammar.js
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "tree-sitter" ]
}

@test "tauri-app via src-tauri/Cargo.toml + package.json" {
  mkdir src-tauri
  touch src-tauri/Cargo.toml package.json
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "tauri-app" ]
}

@test "zed-extension via extension.toml + Cargo.toml" {
  touch extension.toml Cargo.toml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "zed-extension" ]
}

@test "rust-cli via Cargo.toml" {
  touch Cargo.toml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "rust-cli" ]
}

@test "go-cli via go.mod" {
  touch go.mod
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "go-cli" ]
}

@test "electron-app via electron-builder in package.json" {
  printf '{"devDependencies": {"electron-builder": "^24"}}\n' > package.json
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "electron-app" ]
}

@test "electron-app via electron in package.json" {
  printf '{"dependencies": {"electron": "^30"}}\n' > package.json
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "electron-app" ]
}

@test "vscode-ext via @vscode/vsce in package.json" {
  printf '{"devDependencies": {"@vscode/vsce": "^2"}}\n' > package.json
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "vscode-ext" ]
}

@test "github-action via action.yml" {
  touch action.yml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "github-action" ]
}

@test "github-action via action.yaml" {
  touch action.yaml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "github-action" ]
}

@test "nvim-plugin via plugin/ + lua/" {
  mkdir plugin lua
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "nvim-plugin" ]
}

@test "nvim-plugin via queries/ + nested .lua file" {
  mkdir -p queries lua/mod
  touch lua/mod/init.lua
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "nvim-plugin" ]
}

@test "docs-site via mkdocs.yml" {
  touch mkdocs.yml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

@test "static-site via book.toml" {
  touch book.toml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "static-site" ]
}

@test "static-site via _config.yml" {
  touch _config.yml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "static-site" ]
}

# --- precedence (the WHY-ordered bash guards) -------------------------

@test "tauri-app wins over rust-cli + electron" {
  mkdir src-tauri
  touch src-tauri/Cargo.toml Cargo.toml
  printf '{"devDependencies": {"electron": "^30"}}\n' > package.json
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "tauri-app" ]
}

@test "tree-sitter wins over an nvim layout" {
  mkdir plugin
  touch grammar.js lua_dummy
  mkdir lua && touch lua/x.lua
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "tree-sitter" ]
}

@test "mkdocs.yml wins over a legacy _config.yml" {
  touch mkdocs.yml _config.yml
  run "$BIN/detect-kind"
  [ "$status" -eq 0 ]
  [ "$output" = "docs-site" ]
}

# --- undetermined -----------------------------------------------------

@test "unclassifiable dir: exit 1 with 'could not detect kind'" {
  touch README.md
  run "$BIN/detect-kind"
  [ "$status" -eq 1 ]
  [[ "$output" == *"could not detect kind of"* ]]
}

@test "plugin/ dir without lua source is NOT nvim-plugin (falls through)" {
  mkdir plugin
  run "$BIN/detect-kind"
  [ "$status" -eq 1 ]
  [[ "$output" == *"could not detect kind of"* ]]
}

# --- positional <dir> argument ----------------------------------------

@test "accepts an explicit <dir> argument" {
  mkdir -p sub && touch sub/Cargo.toml
  run "$BIN/detect-kind" sub
  [ "$status" -eq 0 ]
  [ "$output" = "rust-cli" ]
}
