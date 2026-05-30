# #301 — Consumer-side drift gate: rollout plan

Tracks the staged rollout of the consumer-side drift detector
(release#301). The detection machinery ships in this branch; this doc
records the phased plan to turn it into a fleet-wide gate.

## What already shipped (this branch)

- `release-sync` writes the provenance marker `.release/.release-sync-source`
  (full source SHA). [ADR-0002](../adr/0002-provenance-marker.md).
- `release-sync --check` now counts **conflicts** (a real file where a
  managed symlink belongs) as non-clean — previously it reported clean,
  missing the headline drift case.
- Fixed a latent bug: the build loop clobbered the global `mode`
  variable, so `--check` / `--dry-run` silently fell through to apply.
  They are now genuinely read-only.
- `bin/release-drift-check` — marker-aware gate: rebuilds against the
  recorded SHA (drift, not staleness), plus a `.release-sync.yaml`
  over-override check. Exits 0 clean / 1 drift / 0 when no marker yet.
- `tests/release-sync/*.bats` + `release-sync-tests.yml` CI.

`release-drift-check` is on `$PATH`, so it is usable **today** agent-side:
`gh-pr-review-loop` / `migrate-consumer-to-build-dir` can run it locally
during review before declaring a PR mergeable. That covers the weak-but-
immediate enforcement with zero consumer changes.

## Phase 1 — backfill markers (no gate yet)

The gate is meaningless until consumers actually carry a marker. A
consumer gets one on its next `release-sync` / re-sync. So:

1. Merge this branch, advance `@v1`.
2. As consumers re-sync for any reason, the marker lands. No forced
   sweep required; `release-drift-check` no-ops on markerless repos.
3. Optionally, a one-pass re-sync sweep across the ~16 consumers to
   backfill eagerly (each is a chore PR through `gh-pr-review-loop`).

## Phase 2 — wire the gate into consumer CI

The clean rollout exploits that consumers call the **reusable** CI
workflows by reference (`uses: arthur-debert/release/.github/workflows/<kind>-ci.yml@v1`).
Adding a `drift-check` job to those reusable workflows propagates to
every consumer on the next `@v1` advance **with no per-consumer file
change and no re-sync** — the consumer's thin caller is untouched.

Sketch of the job (added to each `<kind>-ci.yml`, or one shared
reusable workflow they all call):

```yaml
drift-check:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4                      # the consumer
    - uses: actions/checkout@v4
      with: { repository: arthur-debert/release, ref: v1, path: .release-src, fetch-depth: 0 }
    - run: |
        sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
        sudo chmod +x /usr/local/bin/yq
        export PATH="$PWD/.release-src/bin:$PATH"
        export RELEASE_HOME="$PWD/.release-src"
        release-drift-check
```

Notes:

- **release is public**, so the second checkout needs no token — the
  default `GITHUB_TOKEN` (or anonymous) is fine.
- `fetch-depth: 0` on the release checkout so the marker's recorded SHA
  resolves with its objects. `release-drift-check` sets `RELEASE_REF`
  itself from the marker — the `ref: v1` above only needs to be an
  ancestor-bearing checkout, not the exact baseline.
- This is a **MINOR** bump (new behavior, opt-in via `@v1` advance), not
  a re-sync. Six rust consumers + the rest pick it up automatically.

## Phase 3 — make it a required check (optional)

Once the job has run green across the fleet for a while, add its
check name to the `main-branch-protection` ruleset via `apply-ruleset`
so drift actually blocks merge rather than just reporting. Gate this
behind confidence that determinism holds in practice (watch for any
`yq`-version diff noise on `lefthook.yml`).

## Open question

Agent-side (Phase 0, available now) vs CI-side (Phase 2) is not
either/or — agent-side gives immediate, skippable coverage; CI-side
gives unskippable coverage once markers are backfilled. Recommend
shipping agent-side wiring in the skills now and scheduling Phase 2
after a marker-backfill sweep.
