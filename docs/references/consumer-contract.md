# The consumer-contract manifest (epic #583, WS-A #584)

`docs/references/consumer-contract.yaml` states what a consumer tree looks
like: the tracked real files (the bootstrap quartet + `.github/workflows/*`
managed copies), the untracked ephemeral mirror dests (WS7), the gitignored
`.release/` build dir (WS4), the gate-internal set (WS3), the retired-file sets
(WS6), and the managed-path patterns a CI job may only reference after
building the managed tree.

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
entry in the root `lefthook.yml` regenerates in memory and fails the gate when
the rendered manifest no longer matches the checked-in one. The gate wrappers (`bin-internal/check-consumer-contract.sh`,
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

- a build step that sets up the managed tree — the `arm-gate` composite or an
  explicit `release-core init` run line (exactly these two: the standard recipe
  is demanded, not one option among many); or
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
finding. The baseline was drained to zero and the file deleted in
release#588 (a missing file is an empty baseline); the lint now runs with no
grandfathered jobs, and re-creating the file is never the fix for a new
finding.

## The `# UNMANAGED` sanctioned-bespoke marker (release#630)

Most hand-rolled consumer `.github/workflows/*` are bypasses to normalize
onto the shared spine. A few are **genuinely repo-domain** — a case the
shared workflows do not (and should not) cover, so there is nothing to
migrate onto. release#630's gap analysis blessed four such workflows across
the fleet: phos-app's self-hosted GPU E2E lane (`e2e-gpu.yml`),
tree-sitter-lex's quarterly grammar-bump cron, supage's Cloud Run
`deploy.yml`, and phos-core's `corpus` extra-asset release job. (A fifth gap,
phos-core's PR-time `wasm.yml`, was the opposite verdict — it re-implemented
logic the spine owns, so it was folded into `rust-ci.yml` as the opt-in
`wasm-packages` companion rather than blessed.)

To stop a periodic conformance sweep from re-flagging a blessed-bespoke
workflow on every pass, the convention is a header marker: a consumer
workflow whose top-of-file comment block carries a **`# UNMANAGED`** line
declares itself sanctioned-bespoke and is **exempt from the hand-rolled-bypass
finding**. phos-app's `e2e-gpu.yml` already self-declares this
(`# UNMANAGED workflow (owned by this repo, NOT by arthur-debert/release).`).

Scope and non-scope, deliberately:

- The marker is **only** the bypass/conformance signal — "this is bespoke on
  purpose; do not propose migrating it onto a shared workflow." It is NOT a
  blanket lint-suppression token.
- It does **not** exempt the workflow from the **assumption lint**
  (`release-core admin contract lint`): an `# UNMANAGED` workflow that
  references a managed ephemeral path (`.release/`, `bin/check`,
  `lib/release_core/`, an untracked mirror) still must build the managed tree
  first. Being domain-bespoke does not license assuming the old managed-tree
  shape.
- There is currently **no automated fat-workflow / bypass linter** in this
  repo — the release#569/#630 "litmus sweep" was a one-time manual fleet
  analysis, not a gate. So this marker is, today, a **documented convention**
  any future conformance sweep (manual or automated) MUST honor; when such a
  sweep is mechanized, it reads the `# UNMANAGED` header and skips the file
  for the bypass finding (the same shape the assumption lint's
  `lint_workflow_dir` consumer sweep would grow if the bypass check is ever
  added there).
