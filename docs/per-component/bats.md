# `bats` Capability

Opt-in Capability that gives consumers a uniform local-runner + CI
workflow for bats-based e2e suites. Adapts to existing test trees —
doesn't force layout migration.

## What ships

Synced by `release-sync` when a consumer opts in:

- `bin/check-e2e` — convention-discovery runner. Picks the first match:

  | Order | Looks for                              | Pattern in the fleet           |
  |-------|----------------------------------------|--------------------------------|
  | 1     | `scripts/check-e2e`                    | consumer override (full control) |
  | 2     | `live-tests/run-tests`                 | padz                            |
  | 3     | `live-tests/run`                       | too                             |
  | 4     | `tests/integration/run.sh`             | supage                          |
  | 5     | `tests/e2e/bats/` (any `*.bats`)       | dodot                           |
  | 6     | `tests/e2e/` (any `*.bats`)            | generic flat                    |
  | 7     | `live-tests/tests/` (any `*.bats`)     | padz fallback                   |

  No match: exits 0 with a notice. Repos without e2e tests don't fail
  the umbrella `bin/check`.

Plus a reusable workflow available at the release/ repo (not synced —
called by consumer thin-callers via `uses:`):

- `.github/workflows/bats-e2e.yml` — installs bats-core (via
  [`bats-core/bats-action@4.0.0`](https://github.com/bats-core/bats-action))
  and runs `bin/check-e2e`. Inputs: `runner` (override the default),
  `pre-test` (build/fixture step), `setup-script` (extra setup before
  pre-test), `bats-version`, `runs-on`, `submodules`, `timeout-minutes`.

## How a consumer adopts

Put `.release-sync.yaml` at the repo root and add `bats` to the
`capabilities:` list. **The override fully replaces the Kind default —
include the defaults you want to keep:**

```yaml
# .release-sync.yaml — example for a rust-cli consumer
capabilities:
  - rust-quality    # Kind default
  - bats            # opt-in
# (the shell/markdown/yaml lint gate ships universally from commons —
#  not listed as a Capability; see release#320)
```

Then run `release-sync` (or wait for the next session-start sync).
`bin/check-e2e` lands in `bin/`.

For CI, add a thin caller workflow:

```yaml
# .github/workflows/e2e.yml
name: E2E
on:
  pull_request:
  push:
    branches: [main]
jobs:
  e2e:
    uses: arthur-debert/release/.github/workflows/bats-e2e.yml@v1
    with:
      pre-test: scripts/build-for-e2e   # optional — typical: build binary, prep fixtures
```

The default `runner: bin/check-e2e` picks up the discovery; override
only if you need a custom entry point.

## Why no pre-commit lefthook fragment

bats suites are seconds-to-minutes; pre-commit hooks need to be
sub-second to stay out of contributors' way. The Capability is
CI-time + local-invocation only. If a consumer wants a pre-push hook,
they can wire it in their own `lefthook.yml` (outside the Capability).

## Why no forced layout

Five existing consumers have five different layouts (padz, too,
supage, dodot, lex-fmt/vscode). Migrating all of them is the wrong
trade — the discovery script costs ~50 lines and accommodates every
known pattern. New consumers default to `tests/e2e/` (flat) which is
the most idiomatic bats layout; the longer-living conventions
(`live-tests/`) keep working without churn.

## Bats-version pinning

Default `bats-version: latest` in the workflow. Pin to a specific
version in the consumer caller if a bats-core release regresses your
suite (rare).
