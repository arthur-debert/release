#!/usr/bin/env bats

# Contract suite for bin-internal/check-changelog-unreleased.sh — the
# cheap preflight guard that fails a release when there is no unreleased
# changelog content (no CHANGELOG/unreleased-*.md fragment AND no
# non-empty `## [Unreleased]` section). Pure bash, no release_core.

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/check-changelog-unreleased.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"
}

teardown() {
  cd / || true  # leave $TMP before removing it (avoid getcwd/ENOENT in later setup())
  rm -rf "$TMP"
}

# --- fragment-dir model -----------------------------------------------------

@test "passes when an unreleased fragment is present" {
  mkdir CHANGELOG
  echo "- a change" > CHANGELOG/unreleased-foo.md
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 unreleased fragment(s)"* ]]
}

@test "passes with multiple fragments" {
  mkdir CHANGELOG
  echo "- one" > CHANGELOG/unreleased-a.md
  echo "- two" > CHANGELOG/unreleased-b.md
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"2 unreleased fragment(s)"* ]]
}

@test "fails when CHANGELOG/ exists but holds no unreleased fragment" {
  mkdir CHANGELOG
  echo "- shipped" > CHANGELOG/1.2.3.md  # a cut version file, not unreleased
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"No unreleased changelog content"* ]]
}

# --- literal Keep-a-Changelog section ---------------------------------------

@test "passes when the [Unreleased] section has content" {
  cat > CHANGELOG.md <<'EOF'
# Changelog

## [Unreleased]

- An unreleased entry

## [1.0.0] - 2026-01-01

- Shipped
EOF
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"## [Unreleased]"* ]]
}

@test "passes for a bare 'Unreleased' heading (no brackets) with content" {
  cat > CHANGELOG.md <<'EOF'
# Changelog

## Unreleased

- An unreleased entry
EOF
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "fails when the [Unreleased] section is empty (next heading follows)" {
  cat > CHANGELOG.md <<'EOF'
# Changelog

## [Unreleased]

## [1.0.0] - 2026-01-01

- Shipped
EOF
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"No unreleased changelog content"* ]]
}

@test "fails when the [Unreleased] section holds only whitespace" {
  printf '# Changelog\n\n## [Unreleased]\n\n   \n\n## [1.0.0] - 2026-01-01\n\n- Shipped\n' > CHANGELOG.md
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 1 ]
}

@test "fails when there is no changelog at all" {
  run env CHANGELOG="$TMP/CHANGELOG.md" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"No unreleased changelog content"* ]]
}

# --- contract ---------------------------------------------------------------

@test "errors when CHANGELOG env var is unset" {
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
}
