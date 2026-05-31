# tauri-ci

Reusable PR-time check workflow for `tauri-app` consumers. Lives at
`.github/workflows/tauri-ci.yml@v1`. The release-path sibling is
`tauri-app.yml` (see the top-level
[README's category matrix](../../README.md#category-matrix) and
`.github/workflows/tauri-app.yml`).

## Why this exists

Until this workflow landed, every tauri-app consumer's `ci.yml` was
hand-rolled: a `setup-node + setup-rust + npm ci + frontend lint /
typecheck / test + (sometimes) cargo fmt / clippy / test` sequence
repeated across the fleet. Same "CI workflow reusability gap" called
out for rust and electron, applied to the tauri-app Stack. A change
to the canonical node version, the cache config, the umbrella order,
or the umbrella itself did not propagate to anyone.

`tauri-ci.yml` collapses every consumer's check job to a thin caller.
The canonical sequence — `setup-node` + `setup-rust` + `Swatinem
rust-cache` scoped to `src-tauri/` + lockfile-detected install +
optional pre-test hook + `bin/check` — runs identically across the
fleet.

## What this workflow does NOT do

It deliberately does NOT include a Playwright e2e job. Tauri e2e
orchestration is per-consumer — fetching sibling release binaries,
xvfb, packaged-mode env flags, custom Playwright projects — and
trying to fold all that into one reusable callee turned into a
parameter-explosion in earlier drafts of the electron sibling. The
`playwright: true` input exists only to install browsers in the
`check` job's environment when the consumer needs them for a smoke;
the real e2e job stays bespoke in the consumer's caller workflow.

It also does NOT run `tauri build` by default. Full Tauri builds are
slow (Rust compile + frontend build + bundler) and the release-time
workflow (`tauri-app.yml`) is the right place for the matrix-cross-
platform bundle build. Opt in via `tauri-build: true` if you want
every PR to gate on "the bundle still produces" — rare. Most teams
accept release-time validation as enough.

## The split: frontend at root, rust at src-tauri/

A Tauri app has two halves:

- A **Rust core** under `src-tauri/` (Cargo workspace; produces the
  native binary + Tauri bundles).
- A **frontend** at the repo root (`package.json`, typically TypeScript
  + Vite + a UI framework).

Both are exercised by every PR via `bin/check` (which delegates to
`bin/check-fmt`, `bin/check-lint`, `bin/check-tests` — each runs
BOTH halves). Important: this is why we do NOT use `rust-ci.yml`
directly. `rust-ci.yml` assumes Cargo.toml at the repo root; a Tauri
app's Cargo workspace lives at `src-tauri/`. The check scripts handle
the `cd src-tauri/` themselves, but `Swatinem/rust-cache@v2` needs
the `workspaces: src-tauri` input — which this workflow plumbs.

## Caller shape

```yaml
# .github/workflows/ci.yml — single check job
name: CI
on:
  push:
    branches: ['**']
  pull_request:

permissions:
  contents: read

jobs:
  ci:
    uses: arthur-debert/release/.github/workflows/tauri-ci.yml@v1
    with:
      node-version: '22'
```

With a sibling `e2e` job (the most common shape):

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  ci:
    uses: arthur-debert/release/.github/workflows/tauri-ci.yml@v1
    with:
      node-version: '22'
      pre-test: 'pnpm fetch:wasm'
    secrets:
      # Plumb a PAT so `pnpm fetch:wasm` (which shells to
      # `gh release download`) can hit a private sibling repo.
      gh_token: ${{ secrets.RELEASE_TOKEN }}

  e2e:
    # Existing bespoke job — fetch upstream WASM, install Playwright,
    # run packaged-mode tests, etc. Untouched by this workflow.
    ...
```

`phos-app` is the reference adopter.

## Required-checks rename (do this in the same PR)

Reusable-workflow callee jobs are ALWAYS prefixed by the caller's
job ID — there is no way to suppress the prefix. So when a consumer
migrates from a hand-rolled `ci.yml` (jobs named e.g. `format`,
`frontend`) to a thin caller (`jobs: ci: uses: …/tauri-ci.yml@v1`),
the check names GitHub reports change:

- before: `format`, `frontend` (or whatever the consumer named their
  jobs)
- after:  `ci / check` (the caller's job ID + the callee's job ID)

If the repo's `main-branch-protection` ruleset still requires the
OLD names, the migration PR hangs forever on "Waiting for status to
be reported". Update the ruleset in the same PR sweep:

```sh
cd <consumer-repo>
# Use --checks explicitly — apply-ruleset's auto-detect reads the
# latest main-branch run, which still has the OLD names until the
# migration PR merges. Explicit override avoids the chicken-and-egg.
apply-ruleset --checks "ci / check"
```

If you preserved a bespoke e2e job alongside the thin caller, list
both:

```sh
apply-ruleset --checks "ci / check,e2e"
```

If the caller's job ID isn't `ci`, adjust the prefix accordingly
(e.g. `jobs: check: uses: …` produces `check / check`).

## `.prettierignore` the canonical files (do this in the same PR)

`bin/check-fmt` runs the consumer's `format:check` (typically
`prettier --check .`). Without exclusion, prettier reformats the
release-sync-managed files (`.markdownlint.json`, `lefthook.yml`,
the `bin/check*` scripts, etc.) per the consumer's prettier config —
drifting them from canonical and breaking the next sync.

Add an exhaustive ignore block to `.prettierignore` for every
release-sync-managed path:

```gitignore
# Canonical files managed by arthur-debert/release release-sync — they
# have their own formatting conventions (yamllint/markdownlint) and
# re-formatting them per the consumer's prettier config would drift
# them from canonical, breaking the next release-sync re-sync.
.markdownlint.json
.shellcheckrc
.yamllint
.editorconfig
lefthook.yml
.release-sync-state.yaml
.github/copilot-instructions.md
.github/pull_request_template.md
.github/CODEOWNERS
.github/dependabot.yml
.github/workflows/copilot-review.yml
bin/check
bin/check-fmt
bin/check-lint
bin/check-tests
bin/diff-since-release
scripts/setup-dev-env.sh
```

Same pattern as the electron-app Stack — surfaced empirically on
simple-gal-ui's adoption (the canonical `.markdownlint.json` doesn't
match a typical project's prettier rules; prettier rejects it on
`--check`).

## The `typecheck` script alias

`bin/check` probes for a `typecheck` script in `package.json` and
runs it if present (skip-with-notice otherwise). The portfolio
convention is:

- Vite/TS projects: `"typecheck": "tsc --noEmit"`.
- SvelteKit projects: alias `typecheck` to whatever your `check`
  already does — typically `"typecheck": "svelte-kit sync &&
  svelte-check --tsconfig ./tsconfig.json"`. Don't rename your
  existing `check` script — just add a `typecheck` alias pointing
  at the same command. phos-app does it this way.

## Inputs

All inputs are optional.

| Input | Default | Description |
|---|---|---|
| `node-version` | `'22'` | Forwarded to `actions/setup-node`. Match the consumer's `engines.node` pin. |
| `rust-toolchain` | `''` (empty) | When empty, the workflow honors `rust-toolchain.toml` at repo root if present (phos-app pins this way); otherwise falls back to `stable`. Set to an explicit channel (`'stable'`, `'1.85.0'`, `'nightly'`) to override the file. |
| `tauri-build` | `false` | When true, runs `tauri build` after `bin/check` as a smoke gate (invoked as `npx --no-install tauri build` for npm; `pnpm tauri build` / `yarn tauri build` for the other managers). Slow — leave off unless you need it. (Note: Linux Tauri system libs — libwebkit2gtk etc. — are installed unconditionally on Linux runners because `cargo clippy`/`cargo test` need them to compile the Tauri crate, not just `tauri build`.) |
| `pre-test` | `''` | Shell command run after deps are installed but before `bin/check`. Use for upstream WASM fetches, fixture prep, codegen. |
| `playwright` | `false` | When true, `npx playwright install --with-deps` runs after deps are installed. Browsers only — does NOT add an e2e job. |
| `runner` | `'ubuntu-latest'` | Runner label. Override to `macos-latest` if your check job needs platform-specific behavior (e.g. running Playwright against a macOS-only build). |
| `timeout` | `30` | Per-job timeout (minutes). |
| `submodules` | `'false'` | Forwarded to `actions/checkout`. `'true'` for first-level, `'recursive'` for nested. |

## Secrets

| Secret | Required | Description |
|---|---|---|
| `gh_token` | no | Exposed as `GH_TOKEN` to the `pre-test`, `bin/check`, and `tauri-build` smoke steps (step-scoped, not job-wide — third-party setup actions don't see it). Pass when your `pre-test` (or any check script) calls `gh release download` / `gh api` against a *private sibling repo*. The default `GITHUB_TOKEN` only has access to the calling repo. phos-app passes `${{ secrets.RELEASE_TOKEN }}` here so `pnpm fetch:wasm` can pull WASM from the private `phos-core`. Falls back to `github.token` when not set. Name matches the existing `copilot-review.yml` convention. |

Same-org consumers can also use `secrets: inherit` to forward every
caller secret, but the explicit `gh_token` shape is preferred — it
keeps the surface visible at the call site and works across orgs.

## Permissions

The `check` job declares `permissions: contents: read` explicitly.
The caller only needs `contents: read`. GitHub validates
reusable-workflow permission compatibility at workflow-load time
against every job, so this workflow keeps its surface minimal.

## Caching

Two cache layers, both mandatory per CLAUDE.md:

- `actions/setup-node`'s built-in `cache: pnpm` / `cache: yarn` /
  `cache: npm` (detected from lockfile). Without it `pnpm install`
  balloons on every PR.
- `Swatinem/rust-cache@v2` with `workspaces: src-tauri`. Without
  this the rust half (cargo fmt → clippy → test) recompiles Tauri's
  entire dependency graph on every PR — Tauri's Rust dep tree is
  big (~400 crates).

## What was deliberately not added

- **Matrix over OS / Node versions.** PR-time check runs on one
  Linux runner; cross-platform validation happens at release time
  via `tauri-app.yml`'s matrix. Adding a check-time matrix would
  pay cost on every PR for little extra signal.
- **A Playwright e2e job.** Per-consumer orchestration is too
  varied — see "What this workflow does NOT do" above.
- **A separate type-check job.** The umbrella runs typecheck inside
  `bin/check`; splitting it into a parallel job is not worth the
  extra setup-node + install cost.
- **A separate `tauri build` job.** When `tauri-build: true` is
  opted into, the build runs in-job after `bin/check` so the
  Rust cache built up during clippy/test is hot. A separate job
  would re-pay the cache-warm cost.

Add follow-up inputs when a real consumer needs them, not
speculatively.
