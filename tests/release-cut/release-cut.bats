#!/usr/bin/env bats

load helper

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

@test "no release.yml workflow: exits 0 with informative message" {
  # No .github/workflows/release.yml — common case for github-action
  # repos and tree-sitter standalone grammars that ship the canonical
  # shim but have nothing to dispatch.
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

@test "RELEASE_CUT_WORKFLOW override: missing workflow is a hard error" {
  # When the user explicitly overrides the workflow name, a typo or
  # missing file must fail loudly — otherwise the override silently
  # no-ops, which defeats the purpose of overriding.
  mkdir -p .github/workflows
  echo "name: release" > .github/workflows/release.yml
  RELEASE_CUT_WORKFLOW=custom.yml run "$BIN/release-cut" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"workflow not found"* ]]
  [[ "$output" == *"custom.yml"* ]]
  [[ "$output" == *"RELEASE_CUT_WORKFLOW override"* ]]
}

@test "release.yml present but no manifest: errors on missing manifest" {
  mkdir -p .github/workflows
  echo "name: release" > .github/workflows/release.yml
  run "$BIN/release-cut" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"manifest not found"* ]]
}

@test "invalid version string is rejected" {
  mkdir -p .github/workflows
  echo "name: release" > .github/workflows/release.yml
  cat > Cargo.toml <<EOF
[package]
version = "1.0.0"
EOF
  run "$BIN/release-cut" not-a-version
  [ "$status" -eq 2 ]
  [[ "$output" == *"version must be"* ]]
}

@test "leading-v version string is rejected" {
  mkdir -p .github/workflows
  echo "name: release" > .github/workflows/release.yml
  cat > Cargo.toml <<EOF
[package]
version = "1.0.0"
EOF
  run "$BIN/release-cut" v1.2.3
  [ "$status" -eq 2 ]
  [[ "$output" == *"version must be"* ]]
}

@test "RELEASE_CUT_MANIFEST workspace override is honored" {
  # Workspace pattern (dodot, arami-core): version lives in a sub-crate
  # manifest. RELEASE_CUT_MANIFEST escape hatch points at it.
  mkdir -p .github/workflows crates/foo
  echo "name: release" > .github/workflows/release.yml
  cat > Cargo.toml <<EOF
[workspace]
members = ["crates/foo"]
EOF
  cat > crates/foo/Cargo.toml <<EOF
[package]
name = "foo"
version = "2.4.1"
EOF
  # Stub `gh` so we don't actually dispatch.
  mkdir -p bin-stub
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
echo "gh $*"
EOF
  chmod +x bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"

  RELEASE_CUT_MANIFEST="$PWD/crates/foo/Cargo.toml" run "$BIN/release-cut" minor
  [ "$status" -eq 0 ]
  [[ "$output" == *"Bumping minor: 2.4.1 -> 2.5.0"* ]]
  [[ "$output" == *"gh workflow run release.yml -f version=2.5.0"* ]]
}
