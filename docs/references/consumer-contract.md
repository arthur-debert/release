# The consumer-contract manifest (epic #583, WS-A #584)

`docs/references/consumer-contract.yaml` states what a consumer tree looks
like: the tracked real files (the bootstrap quartet + `.github/workflows/*`
managed copies), the untracked ephemeral mirror dests (WS7), the gitignored
`.release/` build dir (WS4), the gate-internal set (WS3), the tombstone sets
(WS6), and the managed-path patterns a CI job may only reference after
materializing the managed tree.

## Generated, never hand-maintained

The manifest is a checkable VIEW of `release_core/sync.py` (the lockfile
pattern): `release-core admin contract dump` renders it from sync.py's
constants and classification predicates (`BOOTSTRAP_REAL_FILES`,
`GATE_INTERNAL_FILES`, `needs_real_file`, `is_release_internal`, the
`RETIRED_*` sets) applied to this repo's `templates/` source tree. sync.py
stays the single source of truth; the manifest is its mechanical projection —
byte-stable (no timestamps, no SHAs), versioned by `contract_schema`.

**The process rule: a contract-changing PR regenerates the manifest in the
same PR.** This is mechanical, not discipline — the `consumer-contract-check`
entry in the root `lefthook.yml` regenerates in memory and fails the gate on
any drift. The gate wrappers (`bin-internal/check-consumer-contract.sh`,
`bin-internal/lint-consumer-contract.sh`) run the WORKING-TREE `release_core`
via `PYTHONPATH`, never the installed wheel — the PATH `release-core` is the
latest release, which would regenerate from the old classification code.

## The assumption lint

`bin-internal/lint-consumer-contract.sh` (also `release-core admin contract
lint`) is the mechanical "who still assumes the old shape" sweep that root
cause A of #583 was missing — #579 (`rust-ci.yml`'s e2e job sparse-checking
out paths that WS4/WS7 made untracked) stayed latent because nothing could
enumerate stale assumptions.

It scans every CI surface — `.github/workflows/*.yml`,
`.github/actions/*/action.yml`, and the workflow copies shipped under
`templates/` — for any **job** whose steps reference a managed path (the
manifest's `managed_path_prefixes` plus every `untracked_mirrors` dest)
without a prior step in the same job that provides it:

- a materialize step — the `arm-gate` composite or an explicit
  `release-core init` run line (exactly these two: the standard recipe is
  demanded, not one option among many); or
- an `actions/checkout` step whose `with.path` checks content out INTO the
  referenced path (tauri-app.yml / nvim-plugin.yml stage release's
  `bin-internal/` at `path: .release`).

Violations are reported as `file → job → step` and fail the gate.

## Prescriptive, with a shrink-only baseline

Per the #583 owner constraint, the manifest is **prescriptive, not
descriptive**: it states what a consumer tree MUST look like. Where a repo or
a workflow diverges, the fix is normalizing it to the standard recipe — never
a manifest exception, a lint escape hatch, or a central knob. Escape hatches
shrink, not grow.

The one shrink-only mechanism is the lint baseline
(`docs/references/consumer-contract-lint-baseline.yaml`): the `file → job`
pairs that already violated the contract when the lint landed, grandfathered
so the gate could turn on hard for everything else. The ratchet only
tightens — a baseline entry that stops matching fails the lint ("stale
baseline entry — delete it"), and adding an entry is never the fix for a new
finding. Draining the baseline to zero is tracked in release#588.
