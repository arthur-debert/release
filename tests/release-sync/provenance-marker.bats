#!/usr/bin/env bats

load helper

# ---------------------------------------------------------------------
# Provenance marker (ADR-0002)
#
# `release-core init` still writes a `.release/.release-sync-source` marker
# holding the source SHA. Since WS4 (release#521) the whole .release/ tree is
# gitignored + recomposed every session, so the marker is purely informational —
# the drift-check that used to read it was retired with the rest of the
# drift/sync subsystem. These tests pin only that the marker is written and stays
# release-internal.
# ---------------------------------------------------------------------

@test "init writes a provenance marker holding the source SHA" {
  run release_sync
  [ "$status" -eq 0 ]
  [ -f .release/.release-sync-source ]
  expected=$(git -C "$RELEASE_HOME" rev-parse HEAD)
  grep -qx "$expected" .release/.release-sync-source
}

@test "the marker lives only in .release/ — never mirrored out as a symlink" {
  run release_sync
  [ "$status" -eq 0 ]
  # No symlink/copy of the marker leaks into the consumer tree root.
  [ ! -e ./.release-sync-source ]
  # And nothing symlinks to it.
  run bash -c 'find . -type l -lname "*release-sync-source*" | head -1'
  [ -z "$output" ]
}
