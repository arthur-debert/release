#!/usr/bin/env bats

# Integration suite for bin-internal/prepare-tauri-release.sh — the fresh-release
# path's commit contents. The load-bearing regression guard (WS2 #811): the bump
# commit must contain CHANGELOG.md + the CHANGELOG/unreleased-*.md fragment
# REMOVALS, not just the three version files. The old flow's HARD pre-commit gate
# masked the missing `git add`; WS2 dropped that gate, so the staging must be
# correct on its own — and WS2's premise is that the bump commit is exactly
# {version files + changelog}.
#
# Hermetic: stubs the three PATH tools the script shells out to — `semver`
# (validation), `changelog` (the cut, mimicking `changelog new-version`: write
# CHANGELOG/<v>.md, rewrite CHANGELOG.md, delete the unreleased fragments), and
# `gh` (the base-sha CI-green assertion → green). git is real, on a temp repo.

SCRIPT="${BATS_TEST_DIRNAME}/../../bin-internal/prepare-tauri-release.sh"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"

  mkdir -p bin-stub
  # semver stub: the script calls `semver validate`, `semver get build`,
  # `semver get prerel`. 0.2.0 is a valid, non-prerelease, no-build version.
  cat > bin-stub/semver <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "validate "*) echo valid ;;
  "get build") echo "" ;;
  "get prerel") echo "" ;;
  *) echo "" ;;
esac
EOF
  # changelog stub: mimic `changelog new-version <v>` — cut the unreleased
  # fragments into CHANGELOG/<v>.md, rewrite CHANGELOG.md, delete the fragments.
  cat > bin-stub/changelog <<'EOF'
#!/usr/bin/env bash
[ "$1" = "new-version" ] || exit 0
v="$2"
{ echo "## ${v} - 2026-06-20"; echo; cat CHANGELOG/unreleased-*.md; } > "CHANGELOG/${v}.md"
{ echo "# Changelog"; echo; cat "CHANGELOG/${v}.md"; } > CHANGELOG.md
rm -f CHANGELOG/unreleased-*.md
EOF
  # gh stub: the base-sha CI-green assertion → one green check-run.
  cat > bin-stub/gh <<'EOF'
#!/usr/bin/env bash
filter='.'
prev=""
for arg in "$@"; do
  [ "$prev" = "--jq" ] && filter="$arg"
  prev="$arg"
done
printf '%s' '{"check_runs":[{"name":"gate","status":"completed","conclusion":"success"}]}' | jq -s -r "$filter"
EOF
  chmod +x bin-stub/semver bin-stub/changelog bin-stub/gh
  export PATH="$PWD/bin-stub:$PATH"
  export GITHUB_REPOSITORY=acme/widget

  # A minimal Tauri project tree on a real git repo.
  git init -q
  git config user.email t@t.t
  git config user.name t
  mkdir -p src-tauri CHANGELOG
  # Realistic indented JSON: bump_json_version matches a "version" key on its own
  # indented line (format-preserving), not an inline single-line object.
  printf '{\n  "name": "w",\n  "version": "0.1.0"\n}\n' > package.json
  printf '[package]\nname = "w"\nversion = "0.1.0"\n' > src-tauri/Cargo.toml
  printf '{\n  "productName": "w",\n  "version": "0.1.0"\n}\n' > src-tauri/tauri.conf.json
  printf '# Changelog\n' > CHANGELOG.md
  printf -- '- did a thing\n' > CHANGELOG/unreleased-feat.md
  printf -- '- fixed a thing\n' > CHANGELOG/unreleased-fix.md
  git add -A
  git commit -qm "init"

  # The script pushes HEAD + the tag to `origin`; give it a bare remote to land
  # in so the push succeeds hermetically.
  git init -q --bare "$TMP/origin.git"
  git remote add origin "$TMP/origin.git"
  git push -q origin HEAD
}

teardown() {
  cd / || true
  rm -rf "$TMP"
}

run_prepare() {
  NEW_VERSION=0.2.0 TAURI_DIR=. CHANGELOG=CHANGELOG.md \
    GITHUB_OUTPUT="$PWD/gh_output" \
    bash "$SCRIPT"
}

@test "fresh release: succeeds and creates the bump commit + tag" {
  run run_prepare
  [ "$status" -eq 0 ]
  git rev-parse v0.2.0 >/dev/null
}

@test "bump commit contains the three version files" {
  run_prepare
  changed="$(git show --name-only --format= HEAD)"
  [[ "$changed" == *"package.json"* ]]
  [[ "$changed" == *"src-tauri/Cargo.toml"* ]]
  [[ "$changed" == *"src-tauri/tauri.conf.json"* ]]
}

@test "bump commit contains the rolled CHANGELOG.md (regression: WS2 #811)" {
  run_prepare
  # CHANGELOG.md must be modified IN the release commit, not left in the tree.
  git show --name-status HEAD | grep -E '^M[[:space:]]+CHANGELOG\.md$'
  git diff-tree --no-commit-id HEAD -- CHANGELOG.md | grep -q .
}

@test "bump commit stages the fragment REMOVALS (regression: WS2 #811)" {
  run_prepare
  # Both unreleased-*.md fragments must be DELETED in the release commit.
  git show --name-status HEAD | grep -E '^D[[:space:]]+CHANGELOG/unreleased-feat\.md$'
  git show --name-status HEAD | grep -E '^D[[:space:]]+CHANGELOG/unreleased-fix\.md$'
  # And the tree at HEAD must no longer carry them.
  ! git cat-file -e "HEAD:CHANGELOG/unreleased-feat.md" 2>/dev/null
}

@test "no changelog changes left unstaged after prepare (WS2 premise)" {
  run_prepare
  # WS2 premise: the bump commit is exactly {version files + changelog}. If the
  # roll left a fragment deletion or CHANGELOG.md unstaged, it would show here.
  # release-notes.md is an intentional untracked artifact (uploaded, not
  # committed), so scope the check to the changelog paths.
  leftover="$(git status --porcelain -- CHANGELOG.md CHANGELOG/)"
  [ -z "$leftover" ]
}
