# electron-ci

Reusable PR-time check workflow for `electron-app` consumers. Lives at
`.github/workflows/electron-ci.yml@v1`. The release-path sibling is
`electron-app.yml` (see the top-level
[README's category matrix](../../README.md#category-matrix) — a
dedicated per-category doc for electron-app is not yet written; the
canonical reference is the workflow file itself at
`.github/workflows/electron-app.yml`).

## Why this exists

Until this workflow landed, every electron-app consumer's `test.yml`
was hand-rolled: a `setup-node + npm ci + (build shared) + typecheck
+ lint + test` sequence repeated verbatim across the fleet. Same
"CI workflow reusability gap" called out for rust — see the top-level
README — applied to the electron-app Stack. A change to the canonical
node version, the cache config, the umbrella order, or the umbrella
itself did not propagate to anyone.

`electron-ci.yml` collapses every consumer's check job to a thin
caller. The canonical sequence — `setup-node` + `npm ci` + optional
shared-package build + optional pre-test hook + `bin/check` — runs
identically across the fleet.

## What this workflow does NOT do

It deliberately does NOT include a Playwright e2e job. Electron e2e
orchestration is per-consumer — fetching sibling release binaries,
xvfb, packaged-mode env flags, custom Playwright projects — and
trying to fold all that into one reusable callee turned into a
parameter-explosion in early drafts. The `playwright: true` input
exists only to install browsers in the `check` job's environment
when the consumer needs them for a smoke; the real e2e job stays
bespoke in the consumer's caller workflow.

## Caller shape

```yaml
# .github/workflows/test.yml — single check job
name: CI
on:
  push:
    branches: ['**']
  pull_request:

permissions:
  contents: read

jobs:
  check:
    uses: arthur-debert/release/.github/workflows/electron-ci.yml@v1
    with:
      node-version: '22'
```

With a sibling `e2e` job (the most common shape — see lexed):

```yaml
name: Lexed CI
on:
  push:
    branches: ['**']
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  check:
    uses: arthur-debert/release/.github/workflows/electron-ci.yml@v1
    with:
      node-version: '22.18.0'
      shared-build-dir: shared
      submodules: 'true'

  e2e:
    # Existing bespoke job — fetch sibling artifacts, xvfb,
    # packaged-mode env flags, etc. Untouched by this workflow.
    ...
```

`lexed` is the reference adopter — see
[`lex-fmt/lexed/.github/workflows/test.yml`](https://github.com/lex-fmt/lexed/blob/main/.github/workflows/test.yml).

## Required-checks rename (do this in the same PR)

Reusable-workflow callee jobs are ALWAYS prefixed by the caller's job
ID — there is no way to suppress the prefix. So when a consumer
migrates from a hand-rolled `test.yml` (jobs named `build`, `e2e`) to
a thin caller (`jobs: check: uses: …/electron-ci.yml@v1`) plus a
bespoke `e2e` job, the check names GitHub reports change:

- before: `Build and Test`, `E2E Tests` (or whatever the consumer
  named their job-display names)
- after:  `check / check`, `e2e` (the caller's job ID + the callee's
  job ID for the reusable side; the bespoke job keeps its own name)

If the repo's `main-branch-protection` ruleset still requires the
OLD names, the migration PR hangs forever on "Waiting for status to
be reported". Update the ruleset in the same PR sweep:

```sh
cd <consumer-repo>
# Use --checks explicitly — apply-ruleset's auto-detect reads the
# latest main-branch run, which still has the OLD names until the
# migration PR merges. Explicit override avoids the chicken-and-egg.
apply-ruleset --checks "check / check,e2e"
```

If the caller's job ID isn't `check`, adjust the prefix accordingly
(e.g. `jobs: ci: uses: …` produces `ci / check`).

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

Surfaced on simple-gal-ui's adoption (the second electron-app
consumer) — CI's `check / check` job failed on
`prettier --check .markdownlint.json` because the canonical
markdownlint config doesn't match the consumer's prettier rules.
Same pattern will apply to any future electron consumer.

## Inputs

All inputs are optional.

| Input | Default | Description |
|---|---|---|
| `node-version` | `'22'` | Forwarded to `actions/setup-node`. Match the consumer's `engines.node` pin. |
| `pre-test` | `''` | Shell command run after `npm ci` (+ shared-build) but before `bin/check`. Use for grammar fetches, fixture prep, codegen. |
| `shared-build-dir` | `''` | Relative path to a sub-package whose deps + build need to land before the renderer/main typecheck (e.g. `shared` in lexed). |
| `playwright` | `false` | When true, `npx playwright install --with-deps` runs after `npm ci`. Browsers only — does NOT add an e2e job. |
| `runner` | `'ubuntu-latest'` | Runner label. |
| `timeout` | `30` | Per-job timeout (minutes). |
| `submodules` | `'false'` | Forwarded to `actions/checkout`. `'true'` for first-level, `'recursive'` for nested. |

## Permissions

The `check` job declares `permissions: contents: read` explicitly.
The caller only needs `contents: read`. GitHub validates
reusable-workflow permission compatibility at workflow-load time
against every job (see `feedback_reusable_workflow_permissions_upfront`),
so this workflow keeps its surface minimal.

## Caching

`actions/setup-node` ships built-in `cache: 'npm'` support, which
this workflow opts into. Without it the `npm ci` step balloons on
every PR. Caching is mandatory per the repo's CLAUDE.md.

## What was deliberately not added

- **Matrix over OS / Node versions.** PR-time check runs on one
  Linux runner; cross-platform validation happens at release time
  via `electron-app.yml`'s matrix. Adding a check-time matrix would
  pay cost on every PR for little extra signal.
- **A Playwright e2e job.** Per-consumer orchestration is too
  varied — see "What this workflow does NOT do" above.
- **A separate type-check job.** The umbrella runs typecheck inside
  `bin/check`; splitting it into a parallel job is not worth the
  extra setup-node + npm ci cost.

Add follow-up inputs when a real consumer needs them, not
speculatively.
