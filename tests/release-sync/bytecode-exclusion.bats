#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# The ephemeral .release/ is self-ignoring; bytecode is never written
# (release#450 + WS4, release#521)
#
# WS4 makes the whole .release/ temp dir ephemeral: a managed .release/.gitignore
# of `*` keeps git from seeing ANY of it (bytecode included), so it is never
# committed and drift is impossible by construction. Defense-in-depth: the
# installer still skips Python bytecode sources outright.
# ---------------------------------------------------------------------

@test "init ships a self-ignoring managed .release/.gitignore" {
  run release_sync
  [ "$status" -eq 0 ]
  [ -f .release/.gitignore ]
  # `*` on its own line ignores the entire temp dir (the .gitignore included).
  grep -qx '\*' .release/.gitignore
}

@test "the managed .gitignore is release-internal — not mirrored out to repo root" {
  release_sync >/dev/null
  [ -f .release/.gitignore ]
  # Nothing at the repo root links to or copies the managed .gitignore.
  [ ! -e ./.gitignore ]
  run bash -c 'find . -path ./.release -prune -o -type l -lname "*.gitignore" -print'
  [ -z "$output" ]
}

@test "a source tree carrying __pycache__/*.pyc never writes bytecode into .release/" {
  # Build a throwaway commit that adds a poison .pyc under the commons engine
  # package, then sync FROM it (pinned via RELEASE_REF) and prove the bytecode
  # is dropped: ls-tree lists it, but should_skip_source filters it from the
  # plan. All new git objects land in a per-test --shared clone of $RELEASE_HOME
  # (cheap: objects are borrowed via alternates), so the real clone is untouched
  # and everything is cleaned up with the temp dir.
  poison_rel="templates/commons/lib/release_core/release_core/__pycache__/poison.cpython-313.pyc"
  clone="$BATS_TEST_TMPDIR/release-clone"
  git clone -q --shared --no-checkout "$RELEASE_HOME" "$clone"

  tmp_index="$BATS_TEST_TMPDIR/poison-index"
  blob=$(printf 'BYTECODE\n' | git -C "$clone" hash-object -w --stdin)
  # Seed the temp index from HEAD, then add the poison blob at its path.
  GIT_INDEX_FILE="$tmp_index" git -C "$clone" read-tree HEAD
  GIT_INDEX_FILE="$tmp_index" git -C "$clone" \
    update-index --add --cacheinfo "100644,$blob,$poison_rel"
  poison_tree=$(GIT_INDEX_FILE="$tmp_index" git -C "$clone" write-tree)
  poison_commit=$(GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t \
    GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
    git -C "$clone" commit-tree "$poison_tree" -p HEAD -m "test: poison pyc")

  # Sanity: the poison blob really is in that commit's tree (so the test would
  # fail loudly if the installer ever stopped filtering).
  git -C "$clone" ls-tree -r --name-only "$poison_commit" | grep -qx "$poison_rel"

  RELEASE_HOME="$clone" RELEASE_REF="$poison_commit" run release_sync
  [ "$status" -eq 0 ]
  # The poison bytecode must not appear anywhere under .release/.
  run bash -c 'find .release \( -name "*.pyc" -o -name "__pycache__" \) -print'
  [ -z "$output" ]
  [ ! -e .release/lib/release_core/release_core/__pycache__/poison.cpython-313.pyc ]
}
