#!/usr/bin/env bats

# Contract suite for bin-internal/create-github-release.sh — the shared
# GH-release create/update unit (used by nvim-plugin.yml + tauri-app.yml).
#
# Fully offline: a stub `gh` on PATH logs every invocation so we can assert
# that BOTH the create path and the resume/edit path re-assert the prerelease
# status (release#726 — `gh release edit` left isPrerelease unchanged, so a
# `-release-rc` cut that hit the exists path stayed isPrerelease=false).

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/create-github-release.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"
  echo "notes" > release-notes.md
  mkdir -p bin-stub
  # GH_VIEW controls whether `gh release view` succeeds (release exists).
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
echo "gh $*" >> "$GH_LOG"
if [ "$1" = "release" ] && [ "$2" = "view" ]; then
  [ "${GH_VIEW:-fail}" = "ok" ] && exit 0 || exit 1
fi
exit 0
EOF
  chmod +x bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"
  export GH_LOG="$PWD/gh.log"
  : > "$GH_LOG"
}

teardown() {
  cd / || true  # leave $TMP before removing it (avoid getcwd/ENOENT in later setup())
  rm -rf "$TMP"
}

@test "create path: prerelease=true passes --prerelease=true" {
  export GH_VIEW=fail TAG=v1.2.3-release-rc PRERELEASE=true
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'gh release create v1.2.3-release-rc --prerelease=true' "$GH_LOG"
}

@test "create path: prerelease=false passes --prerelease=false" {
  export GH_VIEW=fail TAG=v1.2.3 PRERELEASE=false
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'gh release create v1.2.3 --prerelease=false' "$GH_LOG"
}

@test "edit path re-asserts --prerelease=true (release#726 regression)" {
  export GH_VIEW=ok TAG=v1.2.3-release-rc PRERELEASE=true
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  # The exists/resume path must edit WITH the prerelease flag, not drop it.
  grep -q 'gh release edit v1.2.3-release-rc --notes-file release-notes.md --prerelease=true' "$GH_LOG"
  # And must NOT fall through to create.
  ! grep -q 'gh release create' "$GH_LOG"
}

@test "edit path re-asserts --prerelease=false for a final release" {
  export GH_VIEW=ok TAG=v1.2.3 PRERELEASE=false
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'gh release edit v1.2.3 --notes-file release-notes.md --prerelease=false' "$GH_LOG"
}

@test "default PRERELEASE (unset) is treated as false" {
  export GH_VIEW=ok TAG=v1.2.3
  unset PRERELEASE
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'gh release edit v1.2.3 --notes-file release-notes.md --prerelease=false' "$GH_LOG"
}
