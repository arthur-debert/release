# ADR-0003: tooling is distributed by pip-installing a wheel from the GitHub release

## Status

Accepted. Supersedes parts of [ADR-0001](0001-release-sync-build-dir-with-symlinks.md)
(see "What this changes").

## Context

[ADR-0001](0001-release-sync-build-dir-with-symlinks.md) makes `release-sync`
materialize _all_ managed content — both per-repo config (`lefthook.yml`, lint
configs, skills) **and** the `release_core` package code — into a committed
`.release/` build directory, with symlinks pointing at it. The package itself is
not installed anywhere; consumers run it off `sys.path` reaching into the synced
tree, the `bin/` shims are symlinks into `.release/`, and tooling updates arrive
by re-running `release-sync` (driven fleet-wide by `orc propagate`).

That arrangement forces three constraints that have outlived their reason:

- **`release_core` must be stdlib-only.** There is no install step, so there is
  no place for `pip` to resolve dependencies; the package can only import what
  the system Python already has. This was never a value we wanted — it is a side
  effect of the no-install sys.path-shim transport.
- **Bootstrapping deps is a bespoke curl.** Because nothing is installed,
  CI bootstraps the toolchain by `curl`-ing `fetch-deps` (and friends) from
  `main` and executing them. That couples every consumer's CI to release's
  default branch HEAD and to a hand-rolled fetch script.
- **Updating tooling is a sync.** Advancing the toolchain everywhere means
  `orc propagate` re-running `release-sync` across the fleet and opening a PR per
  consumer, because the package code lives _inside_ each consumer's git tree.

`setup-dev-env.sh` carries its own bespoke package-resolution to find and wire
`release_core` for local dev, duplicating the logic the sync materializer already
encodes.

Meanwhile `release_core` is already a real Python package with a `pyproject.toml`
and a verb-per-module layout. Python has a standard answer for "ship a package
and put its tools on PATH": build a wheel, `pip install` it, declare
`[project.scripts]`. We were hand-rolling a worse version of that.

We considered three transports for the wheel: publish to **PyPI**, install via
**`pip install git+https://`** against the repo, or attach the **wheel as a gh
release asset** and install that. PyPI adds a public-namespace + credentials
surface for what is internal fleet tooling; `git+https` reinstalls from a moving
branch tip with no immutable artifact and drags a build step into every install.
The wheel-on-release is an immutable, versioned artifact tied 1:1 to the tag the
rest of the pipeline already produces, with no extra namespace to own.

## Decision

**Boot everywhere = `pip install -U <release_core wheel from the GitHub release>`
followed by `release-core init`.**

- **Tools become console-scripts.** `pyproject.toml` `[project.scripts]` maps
  each on-PATH command name (the hyphenated names the `bin/` shims use today) to
  a zero-arg wrapper in `release_core/entrypoints.py` that delegates to the
  verb's existing `main(argv) -> int`. Installing the wheel puts the tools on
  PATH; they are current by virtue of the install.
- **Transport is a wheel attached to each GitHub release.** The release pipeline
  builds `release_core-<ver>-py3-none-any.whl` and uploads it as a release asset.
  Boot resolves the asset URL for the target release and `pip install -U`s it.
  This is decided; PyPI and `git+https` were considered and rejected for the
  reasons above.
- **`release-core init` materializes the per-repo committed bits.** The package
  arrives via pip; the files a consumer must have _in its own git tree_ (managed
  `lefthook.yml`, lint configs) are written by an idempotent `release-core init`
  subcommand. Init is the seam that replaces sync's _config_ materialization.

The canonical boot resolver — used by both the CI and local-dev contexts
(`gh` is the GitHub CLI):

```bash
url=$(gh api repos/arthur-debert/release/releases/latest \
        --jq '.assets[] | select(.name|test("^release_core-.*\\.whl$")) | .browser_download_url')
pip install -U "$url"
release-core init
```

This snippet (frozen verbatim in the contract doc) assumes exactly one wheel asset
per release matches the regex — the publish job uploads a single
`release_core-<ver>-py3-none-any.whl`. Hardening the resolver to select one URL
and error otherwise, and to pick the latest release whose tag matches the
consumer's pinned `vN` instead of `releases/latest`, is the major-line-resolution
follow-up below, not part of this PoC.

## What this changes

This supersedes the package-distribution half of ADR-0001 and reframes several
constraints. Concretely:

- **ADR-0001 `.release/` materialization of the package is superseded.**
  `release_core` now arrives via `pip install`, not as committed code symlinked
  out of `.release/`. ADR-0001's build-dir + symlink mechanism remains the model
  for any _config_ still materialized into the consumer tree until `init` fully
  absorbs it — the build directory stops being the package's delivery vehicle.
- **`orc propagate` is no longer the tooling-update path.** `pip install -U` is
  the update: a consumer re-resolves the latest release wheel and installs it.
  Propagate's role narrows to per-repo config changes, not shipping new tool code.
- **The stdlib-only rationale is retired.** It existed solely because the
  sys.path-shim model had no install step. Pip resolves dependencies, so real
  third-party deps are now possible. The package stays deliberately
  dependency-light for now — this removes the _prohibition_, it does not mandate
  adding deps.
- **The `fetch-deps` curl-from-`main` CI bootstrap is on the way out.** The wheel
  install is the bootstrap. Folding `fetch-deps` / `fetch-artifact` into
  console-scripts is a follow-up (below), not part of this PoC.
- **`setup-dev-env.sh`'s bespoke package-resolution is subsumed.** Local dev boots
  through the same resolver + `release-core init`, so the hand-rolled
  find-and-wire logic goes away once both contexts are wired (a follow-up).

## Scope: this PoC vs follow-ups

This ADR records the decision; the PoC's boundaries are:

**This PoC does:**

- Add `release_core/entrypoints.py` + `[project.scripts]` so the wheel exposes
  every tool that has a `bin/` shim today, byte-identical `--help` preserved.
- Add the `release-core` top-level CLI with an idempotent, create-if-absent
  `init` subcommand.
- Add the wheel build + upload job to the release pipeline.
- Prove the whole chain — build wheel → `pip install` → console-scripts on PATH →
  `release-core init` idempotent — **inside release's own CI**, no consumer
  touched. Green smoke is the PoC's definition of done.

**This PoC does NOT do:**

- Does not rip out `release-sync` / `.release/` materialization fleet-wide.
- Does not touch consumer repos (phos especially excluded).
- Does not remove the `fetch-deps` curl bootstrap from consumer workflows.

**Deferred follow-ups (not this PoC):**

- Wiring the boot resolver into both contexts — `setup-dev-env.sh` (SessionStart)
  and the reusable CI workflows.
- Folding `fetch-deps` / `fetch-artifact` into console-scripts (the "deps fail →
  non-zero exit" hard requirement rides with that migration).
- Retiring `release-sync` package materialization + the curl bootstrap fleet-wide.
- Major-line-aware wheel resolution — pinning to the consumer's `vN` instead of
  `releases/latest`.

## Consequences

- **Updates are an install, not a fleet PR sweep.** Advancing the toolchain
  everywhere becomes `pip install -U` at boot; there is no per-consumer commit of
  tool code to propagate and review.
- **The artifact is immutable and tag-pinned.** The wheel is built once per
  release and tied to that tag, matching the versioning contract — no moving
  branch tip in the install path, no extra namespace to own.
- **Dependencies are now an option.** Removing the stdlib-only prohibition opens
  real dependency use behind pip's resolver; we keep the package dependency-light
  by choice, not by constraint.
- **`init` must be idempotent and honest.** Re-running it is a clean no-op, and it
  exits non-zero on any write it intended to make but could not — no silent
  best-effort swallowing for its own writes.
- **The PoC is self-contained.** Mechanics are proven in release's own CI smoke
  job; consumer rollout and curl/sync retirement are sequenced as follow-ups, so
  the fleet is not disturbed by proving the model.
