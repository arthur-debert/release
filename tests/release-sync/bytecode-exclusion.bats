#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Bytecode never lands in a consumer's .release/ (release#450)
#
# Python bytecode (__pycache__/, *.pyc, *.pyo) is host- and Python-version-
# specific and must never be committed into a consumer. Two layers defend it:
#   1. the materializer skips bytecode sources (defense-in-depth)
#   2. a managed .release/.gitignore keeps a local regeneration from tracking it
# ---------------------------------------------------------------------

@test "sync ships a managed .release/.gitignore covering bytecode" {
  run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  [ -f .release/.gitignore ]
  grep -qx '__pycache__/' .release/.gitignore
  grep -qx '\*.pyc' .release/.gitignore
  grep -qx '\*.pyo' .release/.gitignore
}

@test "the managed .gitignore is release-internal — not mirrored out to repo root" {
  "$BIN/release-sync" >/dev/null
  [ -f .release/.gitignore ]
  # Nothing at the repo root links to or copies the managed .gitignore.
  [ ! -e ./.gitignore ]
  run bash -c 'find . -path ./.release -prune -o -type l -lname "*.gitignore" -print'
  [ -z "$output" ]
}

@test "a source tree carrying __pycache__/*.pyc never materializes bytecode" {
  # Build a throwaway commit in $RELEASE_HOME that adds a poison .pyc under the
  # commons engine package — using pure plumbing in a temp index so the real
  # working tree, index, and refs are untouched. Sync FROM that commit (pinned
  # via RELEASE_REF) and prove the bytecode is dropped: ls-tree lists it, but
  # should_skip_source filters it out of the plan.
  poison_rel="templates/commons/lib/release_core/release_core/__pycache__/poison.cpython-313.pyc"
  tmp_index="$(mktemp)"
  blob=$(printf 'BYTECODE\n' | git -C "$RELEASE_HOME" hash-object -w --stdin)
  # Seed the temp index from HEAD, then add the poison blob at its path.
  GIT_INDEX_FILE="$tmp_index" git -C "$RELEASE_HOME" read-tree HEAD
  GIT_INDEX_FILE="$tmp_index" git -C "$RELEASE_HOME" \
    update-index --add --cacheinfo "100644,$blob,$poison_rel"
  poison_tree=$(GIT_INDEX_FILE="$tmp_index" git -C "$RELEASE_HOME" write-tree)
  poison_commit=$(git -C "$RELEASE_HOME" commit-tree "$poison_tree" -p HEAD -m "test: poison pyc")
  rm -f "$tmp_index"

  # Sanity: the poison blob really is in that commit's tree (so the test would
  # fail loudly if the materializer ever stopped filtering).
  git -C "$RELEASE_HOME" ls-tree -r --name-only "$poison_commit" | grep -qx "$poison_rel"

  RELEASE_REF="$poison_commit" run "$BIN/release-sync"
  [ "$status" -eq 0 ]
  # The poison bytecode must not appear anywhere under .release/.
  run bash -c 'find .release \( -name "*.pyc" -o -name "__pycache__" \) -print'
  [ -z "$output" ]
  [ ! -e .release/lib/release_core/release_core/__pycache__/poison.cpython-313.pyc ]
}
