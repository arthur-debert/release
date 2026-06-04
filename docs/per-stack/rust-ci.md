# rust-ci

Reusable PR-time check workflow for Rust consumers (both `rust-cli`
and `rust-lib`). Lives at
`.github/workflows/rust-ci.yml@v1`. The release-path sibling is
`rust-cli.yml`; see `per-category/rust-cli.md`.

## Why this exists

Until this workflow landed, `release/` shipped reusable RELEASE
workflows but no reusable CI workflow — every consumer's `ci.yml`
was hand-rolled. That broke "fix once, propagate everywhere" for
the check path: a change to canonical clippy flags, the cache
config, the toolchain pin, or the matrix shape did not propagate
to anyone. See the "CI workflow reusability gap" section of the
top-level README for the consumer-level evidence and the
`feedback_ci_reusability_gap` memory for the full surface.

`rust-ci.yml` collapses every consumer's `ci.yml` to a thin caller.
The canonical sequence — `setup-rust` + cargo-nextest + `bin/check`
+ (optional) release-binary build + (optional) bats e2e — runs
identically across the fleet.

## Caller shape

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: ['**']
  pull_request:

permissions:
  contents: read

jobs:
  ci:
    uses: arthur-debert/release/.github/workflows/rust-ci.yml@v1
    with:
      binary-name: my-cli      # optional — see below
      bats: true               # optional — see below
```

`dodot` is the reference adopter — see
[`arthur-debert/dodot/.github/workflows/ci.yml`](https://github.com/arthur-debert/dodot/blob/main/.github/workflows/ci.yml).
Its hand-rolled ci.yml went from ~75 lines to ~12.

## Required-checks rename (do this in the same PR)

Reusable-workflow callee jobs are ALWAYS prefixed by the caller's
job ID — there is no way to suppress the prefix. So when a
consumer migrates from a hand-rolled `ci.yml` (jobs named `check`,
`e2e`) to a thin caller (`jobs: ci: uses: …/rust-ci.yml@v1`), the
check names GitHub reports change:

- before: `check`, `e2e`
- after:  `ci / check`, `ci / e2e`

If the repo's `main-branch-protection` ruleset still requires the
OLD names, the migration PR hangs forever on "Waiting for status
to be reported". Update the ruleset in the same PR sweep:

```sh
cd <consumer-repo>
# Use --checks explicitly — the ruleset auto-detect reads the
# latest main-branch run, which still has the OLD names until the
# migration PR merges. Explicit override avoids the chicken-and-egg.
release-core admin policy ruleset --checks "ci / check,ci / e2e"
```

If the caller's job ID isn't `ci`, adjust the prefix accordingly
(e.g. `jobs: tests: uses: …` produces `tests / check`, `tests / e2e`).

## Inputs

All inputs are optional.

| Input | Default | Description |
|---|---|---|
| `extra-targets` | `''` | Space-separated extra rustup targets (e.g. `wasm32-wasip2` for zed-lex). Forwarded to the shared `setup-rust` composite, which re-runs `rustup target add` with this value — that command is strictly space-separated, so commas will fail. Most consumers only need a single target. |
| `pre-test` | `''` | Path to a script run before `bin/check`. Use for grammar downloads, fixture prep, codegen. |
| `bats` | `false` | When `true`, add a second `e2e` job that installs bats-core and calls `bin/check-e2e`. |
| `binary-name` | `''` | When set, the check job runs `cargo build --release -p <binary-name>` (cargo `-p` selects a workspace **package**, not a bin target — see below) and uploads `<binary-name>-linux`. |
| `runner` | `'ubuntu-latest'` | Runner label for both jobs. |
| `timeout` | `30` | Per-job timeout (minutes). |

## On `binary-name` — package vs bin

The build uses `cargo build --release -p <binary-name>` where `-p`
selects a workspace **package**. The convention across the fleet
is that the package name and the produced binary file name are the
same string: dodot, padz, lex, supage all line up this way, and
`rust-cli.yml`'s release workflow assumes the same via its
`bin-name` input. If your package name and binary name diverge,
this input isn't the right shape for you — file an issue.

## How `binary-name` and `bats` interact

When **both** are set, the e2e job:

1. Downloads the artifact `<binary-name>-linux`.
2. Exports `<BINARY_NAME_UPPER>_BIN=$GITHUB_WORKSPACE/<binary-name>`
   (e.g. `DODOT_BIN=/home/runner/work/dodot/dodot/dodot`) before
   invoking `bin/check-e2e`.
3. `chmod +x` the downloaded binary.

This convention matches what dodot's bats suite (and others using
the `bats` Component) expects: a `<PROJECT>_BIN` env var pointing
at a prebuilt binary. The binary-name → env-var mapping uppercases
and replaces `-` with `_` (so `binary-name: foo-bar` →
`FOO_BAR_BIN`).

If `bats: true` but `binary-name` is empty, the e2e job still runs
— `bin/check-e2e` is expected to build / locate its target itself
(supage / live-tests patterns work this way).

If `binary-name` is set but `bats` is false, the binary is built
and uploaded but no e2e job runs. Useful if downstream jobs in the
caller need the artifact.

## Permissions

Both jobs declare `permissions: contents: read` explicitly. The
caller only needs `contents: read`. GitHub validates
reusable-workflow permission compatibility at workflow-load time
against every job — including ones gated by `if:` conditions (see
`feedback_reusable_workflow_permissions_upfront`) — so this
workflow keeps its surface minimal and explicit per-job.

## Caching

Caching is mandatory per the repo's CLAUDE.md ("without it a
2-minute Rust job balloons to 10"). The shared `setup-rust`
composite action wires up `Swatinem/rust-cache@v2`; no consumer
opt-in needed.

## What was deliberately not added

* **Matrix over OS / Rust channels.** PR-time check runs on one
  Linux runner; cross-platform validation happens at release time
  via `rust-cli.yml`'s matrix. Adding a check-time matrix would
  pay cost on every PR with little extra signal — keep it lean.
* **`cargo-deny` / supply-chain checks.** Out of scope for the
  umbrella; that's a separate Component / workflow.
* **Coverage upload.** Same reasoning.

Add follow-up inputs when a real consumer needs them, not
speculatively.
