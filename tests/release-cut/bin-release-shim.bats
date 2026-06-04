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

@test "shim errors with helpful message when release-core is not on PATH" {
  # Empty PATH except for coreutils so `command -v release-core` fails.
  # Use $BASH for the bash binary instead of relying on `/usr/bin/bash`
  # or `/bin/bash` — those don't exist on NixOS, FreeBSD, or any host
  # with a custom toolchain.
  run env -i PATH=/usr/bin:/bin "$BASH" "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 1 ]
  [[ "$output" == *"release-core not on \$PATH"* ]]
}

@test "shim forwards args to release-core cut on PATH" {
  # Drop a stub release-core on PATH that echoes its args; the shim
  # should exec into it transparently, prepending the `cut` subcommand.
  mkdir -p bin-stub
  cat > bin-stub/release-core <<'EOF'
#!/usr/bin/env bash
echo "release-core called with: $*"
exit 0
EOF
  chmod +x bin-stub/release-core
  export PATH="$PWD/bin-stub:$PATH"

  run "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 0 ]
  [[ "$output" == "release-core called with: cut minor" ]]
}

@test "shim propagates release-core cut exit status" {
  mkdir -p bin-stub
  cat > bin-stub/release-core <<'EOF'
#!/usr/bin/env bash
exit 42
EOF
  chmod +x bin-stub/release-core
  export PATH="$PWD/bin-stub:$PATH"

  run "$TEMPLATES_BIN/release" minor
  [ "$status" -eq 42 ]
}
