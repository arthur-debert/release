# Shared setup for tests/fleet/*.bats — exercises managed-repos against a
# fixture manifest + synthetic $REPOS_ROOT, fully offline (no gh/network).

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  harness_create_workspace_notrap
  cd "$HARNESS_WORKSPACE"

  # A fixture manifest covering the three layout shapes the real one has:
  # multi-repo project (org-named dir), multi-repo project (project-named
  # dir), and single-repo project (collapsed bare dir). Paths are NOT
  # derivable from the repo name — that's the point.
  cat > manifest.yaml <<'EOF'
projects:
  lex:
    - { repo: lex-fmt/lex,   path: lex-fmt/lex }
    - { repo: lex-fmt/comms, path: lex-fmt/comms }
  phos:
    - { repo: phos-editor/app, path: phos/phos-app }
  dodot:
    - { repo: arthur-debert/dodot, path: dodot }
EOF
  export MANAGED_REPOS_MANIFEST="$PWD/manifest.yaml"

  # A synthetic REPOS_ROOT where some repos "exist" (have .git) and some
  # don't, to exercise found/missing.
  export REPOS_ROOT="$PWD/root"
  mkdir -p "$REPOS_ROOT/lex-fmt/lex/.git"
  mkdir -p "$REPOS_ROOT/dodot/.git"
  # lex-fmt/comms and phos/phos-app intentionally absent → missing.
}

teardown() {
  cd /
  harness_cleanup
}
