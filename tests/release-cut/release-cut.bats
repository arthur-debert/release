#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

# Drop a stub `gh` on PATH so dispatch tests don't actually call the
# real gh CLI. The stub echoes its args so we can assert on them.
_stub_gh() {
  mkdir -p bin-stub
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
echo "gh $*"
EOF
  chmod +x bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"
}

_write_release_yml() {
  mkdir -p .github/workflows
  echo "name: release" > .github/workflows/release.yml
}

# ---------------------------------------------------------------------
# CLI surface (preserved across the kind-aware refactor).
# ---------------------------------------------------------------------

@test "no args prints usage and exits 2" {
  run "$BIN/release-cut"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "--help exits 0 and prints usage" {
  run "$BIN/release-cut" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage:"* ]]
}

@test "-h is an alias for --help" {
  run "$BIN/release-cut" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage:"* ]]
}

@test "invalid version string is rejected" {
  _write_release_yml
  run "$BIN/release-cut" not-a-version
  [ "$status" -eq 2 ]
  [[ "$output" == *"version must be"* ]]
}

@test "leading-v version string is rejected" {
  _write_release_yml
  run "$BIN/release-cut" v1.2.3
  [ "$status" -eq 2 ]
  [[ "$output" == *"version must be"* ]]
}

# ---------------------------------------------------------------------
# Graceful no-op when there's nothing to dispatch.
# ---------------------------------------------------------------------

@test "no release.yml workflow: exits 0 with informative message" {
  # github-action repos and tree-sitter standalone grammars ship the
  # canonical shim but have nothing to dispatch.
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"no .github/workflows/release.yml"* ]]
  [[ "$output" == *"nothing to do"* ]]
}

@test "no release.yml workflow: graceful even with literal version" {
  run "$BIN/release-cut" 1.2.3
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
}

# ---------------------------------------------------------------------
# Per-Kind current-version readers.
# ---------------------------------------------------------------------

@test "rust-cli single crate: reads [package].version" {
  _write_release_yml
  cat > Cargo.toml <<EOF
[package]
name = "foo"
version = "1.4.2"
EOF
  _stub_gh
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 1.4.2 -> 1.5.0"* ]]
  [[ "$output" == *"gh workflow run release.yml -f version=1.5.0"* ]]
}

@test "rust-cli workspace root: reads [workspace.package].version" {
  _write_release_yml
  cat > Cargo.toml <<EOF
[workspace]
resolver = "2"
members = ["crates/foo"]

[workspace.package]
version = "0.7.2-rc.2"
edition = "2024"
EOF
  mkdir -p crates/foo
  cat > crates/foo/Cargo.toml <<EOF
[package]
name = "foo"
version.workspace = true
EOF
  _stub_gh
  run "$BIN/release-cut" patch
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping patch: 0.7.2-rc.2 -> 0.7.3"* ]]
}

@test "rust-cli workspace-only root: probes members (dodot pattern)" {
  # Root Cargo.toml has [workspace] but no [workspace.package].version.
  # The canonical version lives in the lib crate.
  _write_release_yml
  cat > Cargo.toml <<EOF
[workspace]
resolver = "2"
members = ["crates/lib", "crates/cli"]
EOF
  mkdir -p crates/lib crates/cli
  cat > crates/lib/Cargo.toml <<EOF
[package]
name = "foo-lib"
version = "5.0.1-rc.1"
EOF
  cat > crates/cli/Cargo.toml <<EOF
[package]
name = "foo"
version.workspace = true
EOF
  _stub_gh
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 5.0.1-rc.1 -> 5.1.0"* ]]
}

@test "rust-cli workspace-only root: ignores [workspace.dependencies] versions" {
  # Regression guard: a workspace-only root that declares dependency
  # versions inside [workspace.dependencies.<dep>] tables MUST NOT
  # have those mistaken for the package version. The reader is
  # section-aware: only [package] / [workspace.package] / top-level
  # keys count.
  _write_release_yml
  cat > Cargo.toml <<EOF
[workspace]
resolver = "2"
members = ["crates/lib"]

[workspace.dependencies.serde]
version = "1.0.219"
features = ["derive"]
EOF
  mkdir -p crates/lib
  cat > crates/lib/Cargo.toml <<EOF
[package]
name = "foo-lib"
version = "3.2.1"
EOF
  _stub_gh
  run "$BIN/release-cut" patch
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping patch: 3.2.1 -> 3.2.2"* ]]
}

@test "rust-cli workspace-only root: handles multi-line members array" {
  _write_release_yml
  cat > Cargo.toml <<EOF
[workspace]
resolver = "2"
members = [
    "crates/lib",
    "crates/cli",
]
EOF
  mkdir -p crates/lib
  cat > crates/lib/Cargo.toml <<EOF
[package]
name = "foo-lib"
version = "2.0.0"
EOF
  _stub_gh
  run "$BIN/release-cut" major
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping major: 2.0.0 -> 3.0.0"* ]]
}

@test "electron-app: reads package.json .version" {
  _write_release_yml
  cat > package.json <<'EOF'
{
  "name": "lexed",
  "productName": "LexEd",
  "version": "0.10.7-rc.2",
  "devDependencies": {
    "electron-builder": "^25.0.0"
  }
}
EOF
  _stub_gh
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 0.10.7-rc.2 -> 0.11.0"* ]]
}

@test "package.json reader ignores nested 'version' keys in dependencies" {
  # A dependency map entry like "@scope/pkg": "1.2.3" must not be
  # mistaken for the top-level version field. Our regex anchors to
  # "version" as the field name, not the value.
  _write_release_yml
  cat > package.json <<'EOF'
{
  "name": "foo",
  "version": "2.0.0",
  "devDependencies": {
    "electron-builder": "^25.0.0",
    "some-package-version": "9.9.9"
  }
}
EOF
  _stub_gh
  run "$BIN/release-cut" patch
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping patch: 2.0.0 -> 2.0.1"* ]]
}

@test "zed-extension: reads extension.toml top-level version" {
  _write_release_yml
  cat > extension.toml <<EOF
id = "lex"
name = "Lex"
version = "0.1.2-rc.1"
schema_version = 1
EOF
  # detect-kind requires both extension.toml AND Cargo.toml for
  # zed-extension classification.
  cat > Cargo.toml <<EOF
[package]
name = "zed-lex"
version = "0.1.2-rc.1"
EOF
  _stub_gh
  run "$BIN/release-cut" patch
  [ "$status" -eq 0 ]
  # semver bump patch strips the -rc.1 suffix and increments: rc.1 → .3.
  # To land on the un-suffixed 0.1.2 you'd type the version literally.
  [[ "$output" == *"Bumping patch: 0.1.2-rc.1 -> 0.1.3"* ]]
}

@test "tauri-app: reads src-tauri/Cargo.toml [package].version" {
  _write_release_yml
  # detect-kind classifies as tauri-app when both src-tauri/Cargo.toml
  # and package.json are present.
  cat > package.json <<'EOF'
{
  "name": "arami",
  "version": "0.7.2-rc.2"
}
EOF
  mkdir -p src-tauri
  cat > src-tauri/Cargo.toml <<EOF
[package]
name = "arami"
version = "0.7.2-rc.2"
EOF
  _stub_gh
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 0.7.2-rc.2 -> 0.8.0"* ]]
}

@test "nvim-plugin / go-cli pattern: reads latest git tag" {
  # Kinds without a manifest version use `git describe --tags`. Use a
  # go-mod fixture to keep the test independent of nvim's directory
  # heuristics — same code path either way.
  _write_release_yml
  echo "module example.com/foo" > go.mod
  git add -A
  git -c user.email=t@t -c user.name=t commit -q -m init
  git tag v0.3.1
  _stub_gh
  run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 0.3.1 -> 0.4.0"* ]]
}

@test "go-cli with no tags: informative error pointing at explicit version" {
  _write_release_yml
  echo "module example.com/foo" > go.mod
  run "$BIN/release-cut" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"no git tags found"* ]]
  [[ "$output" == *"explicit version"* ]]
}

# ---------------------------------------------------------------------
# Literal-version dispatches bypass Kind detection — they work in any
# Consumer, even when detect-kind can't classify the directory.
# ---------------------------------------------------------------------

@test "literal version dispatches without needing detect-kind" {
  _write_release_yml
  # Empty repo — detect-kind would fail here. But a literal version
  # doesn't need to read the current value, so dispatch succeeds.
  _stub_gh
  run "$BIN/release-cut" 1.2.3
  [ "$status" -eq 0 ]
  [[ "$output" == *"gh workflow run release.yml -f version=1.2.3"* ]]
}

@test "literal pre-release version is accepted" {
  _write_release_yml
  _stub_gh
  run "$BIN/release-cut" 1.0.0-rc.1
  [ "$status" -eq 0 ]
  [[ "$output" == *"gh workflow run release.yml -f version=1.0.0-rc.1"* ]]
}

# ---------------------------------------------------------------------
# Bump shortcut error paths.
# ---------------------------------------------------------------------

@test "bump shortcut on unclassifiable dir: detect-kind error" {
  _write_release_yml
  # No Cargo.toml, no package.json, no go.mod, no plugin layout —
  # detect-kind exits 1. Bump shortcut surfaces that.
  run "$BIN/release-cut" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"detect-kind could not identify"* ]]
}
