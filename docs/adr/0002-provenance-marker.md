# ADR-0002: release-sync records source provenance (not state)

## Status

Accepted, then **superseded by [ADR-0005](0005-minimal-footprint-invoke-dont-discover.md)**
as of WS4 (release#521, shipped). The marker existed to let `release-drift-check`
tell a hand-edited managed file from a merely stale one by rebuilding against the
recorded revision. WS4
makes the whole `.release/` tree gitignored + recomposed from the pinned wheel
every session, so the build dir is rebuilt every session and can't fall out of
sync — `release-drift-check` was retired along with the rest of the sync subsystem. The
`.release/.release-sync-source` marker is **still written** by `release-core init`
(identical bytes), but it is now **transient and purely informational** — it has
no reader. The rationale below is preserved for history.

## Context

[ADR-0001](0001-release-sync-build-dir-with-symlinks.md) deliberately removed
the `.release-sync-state.yaml` file: the filesystem is the state, and removals
propagate via broken-symlink cleanup, so no bookkeeping is needed.

But there is a separate question ADR-0001 left unanswerable from the consumer
alone: **which release revision generated this `.release/`?** Without it, a
drift check (did the consumer hand-edit a managed file?) cannot tell an edited
file from a merely stale one. If it compares the consumer against a moving ref
like `v1` or `main`, every consumer that simply hasn't re-synced since the
shared templates moved ahead looks "dirty" — drowning the real signal (an
actually-edited managed file) in staleness noise. This is not hypothetical:
`release-sync` already stamps the generating short-SHA into `lefthook.yml`'s
header, so comparing a consumer against any ref other than the one it was synced
from produces a spurious diff on that line for free.

## Decision

`release-sync` writes a provenance marker at `.release/.release-sync-source`
containing the full 40-char SHA of the release revision that generated the
tree. The marker is part of `.release/` (so `--check` sees it change) but is
**never mirrored out** as a symlink into the consumer tree — it lives only
inside `.release/`.

`release-drift-check` reads that SHA and rebuilds against **exactly that
revision** (`RELEASE_REF=<sha> release-sync --check`). A clean consumer
reproduces its committed tree and reports zero diff no matter how far behind the
shared templates it is; only a genuinely-edited managed file shows up.

## This is provenance, not the state file ADR-0001 rejected

The distinction is the whole point, so it is worth stating plainly:

- The rejected `.release-sync-state.yaml` was **operational** state: a manifest
  of what had been synced, which sync *read to decide what to remove*. It could
  desync from reality (shelved repos, hand edits) and corrupt those decisions.
- The provenance marker is **informational**: sync never reads it to decide
  anything. It is rewritten wholesale on every sync, exactly like every other
  file in `.release/`, so it cannot desync — it always reflects the last sync.
  Nothing downstream breaks if it is deleted; the next sync just rewrites it.

So ADR-0001's "no state file" still holds. We did not reintroduce bookkeeping;
we added a passive breadcrumb.

## Consequences

- **An edited file vs a stale one is structurally separated.** The drift gate
  needs no out-of-band knowledge of which tag a consumer pins; the tree declares
  its own baseline.
- **Determinism is required.** "Rebuild from the recorded SHA == committed tree"
  only holds if sync output is deterministic for a fixed (source SHA + consumer
  `.release-sync.yaml`). Templates must carry no timestamps / hostnames / random
  content (audited clean as of this ADR), and the marker's only dynamic line is
  the SHA it round-trips to. The one remaining dependency is the `yq` version
  used to compose `lefthook.yml`; the drift workflow pins mikefarah v4.
- **Staleness becomes separately measurable.** "marker SHA vs `origin/v1`" is a
  non-blocking number — a fleet-staleness audit falls out of the same marker.
- **Backfill is lazy.** Consumers synced before this ADR have no marker;
  `release-drift-check` treats a missing marker as "nothing to compare" and
  exits 0. Re-syncing once backfills it.
