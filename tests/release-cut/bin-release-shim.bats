#!/usr/bin/env bats

load helper

# Tests for templates/commons/bin/release — the canonical thin shim
# that release-sync materializes as bin/release in every Consumer.

@test "shim is executable" {
  [ -x "$TEMPLATES_BIN/release" ]
}

@test "shim shebang is bash" {
  run head -1 "$TEMPLATES_BIN/release"
  [[ "$output" == "#!/usr/bin/env bash" ]]
}

@test "shim errors with helpful message when release-cut is not on PATH" {
  # Empty PATH except for coreutils so `command -v release-cut` fails
  # but bash itself still works.
  run env -i PATH=/usr/bin:/bin bash "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"release-cut not on \$PATH"* ]]
}

@test "shim forwards args to release-cut on PATH" {
  # Drop a stub release-cut on PATH that echoes its args; the shim
  # should exec into it transparently.
  mkdir -p bin-stub
  cat > bin-stub/release-cut <<'EOF'
#!/usr/bin/env bash
echo "release-cut called with: $*"
exit 0
EOF
  chmod +x bin-stub/release-cut
  export PATH="$PWD/bin-stub:$PATH"

  run "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 0 ]
  [[ "$output" == "release-cut called with: minor" ]]
}

@test "shim propagates release-cut exit status" {
  mkdir -p bin-stub
  cat > bin-stub/release-cut <<'EOF'
#!/usr/bin/env bash
exit 42
EOF
  chmod +x bin-stub/release-cut
  export PATH="$PWD/bin-stub:$PATH"

  run "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 42 ]
}
