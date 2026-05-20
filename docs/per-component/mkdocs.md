# `mkdocs` Component

Opt-in Component for repos that build documentation with mkdocs.
Plugin-agnostic — the consumer's `mkdocs.yml` + `docs/requirements.txt`
(or equivalent) own the theme/plugin choices; the Component just
provides the canonical build-check + deploy mechanism.

## What ships

Synced by `release-sync` when a consumer opts in:

- **`bin/check-docs`** — local `mkdocs build --strict` runner.
  Auto-discovers `./mkdocs.yml` or `./docs/mkdocs.yml`. Skips with
  exit 0 if no config found (so it's safe to include in
  `bin/check` for non-docs sub-projects too).

Plus a reusable workflow at the release/ repo (not synced — called
via `uses:`):

- **`.github/workflows/mkdocs.yml`** — two-job workflow:
  - `build` runs on every trigger: setup Python → install
    deps (`requirements` or `pre-install-cmd`) → `mkdocs build --strict`
    → upload site artifact (only on deploy-eligible runs).
  - `deploy` runs only when `deploy` input is true *or* the trigger
    is a push to the default branch. Uses first-party
    `actions/configure-pages` + `actions/deploy-pages` (no
    third-party deploy action). PRs and non-default-branch pushes
    exercise the build only.

  Inputs: `config-file` (default `mkdocs.yml`), `requirements`
  (default `docs/requirements.txt`), `pre-install-cmd` (escape
  hatch for `uv`/`poetry`/etc.), `site-dir` (default `site`),
  `python-version` (default `3.x`), `runs-on`, `timeout-minutes`,
  `deploy` (override).

## How a consumer adopts

Put `.release-sync.yaml` at the repo root and add `mkdocs` to the
`components:` list. The override **fully replaces** the Stack
defaults — include any defaults you want to keep:

```yaml
# .release-sync.yaml — rust-cli consumer that also ships mkdocs docs
components:
  - shell-quality   # Stack default
  - rust-quality    # Stack default
  - mkdocs          # opt-in
```

Then run `release-sync` (or wait for the next session-start sync).
`bin/check-docs` lands in `bin/`.

For CI, add a thin caller workflow:

```yaml
# .github/workflows/docs.yml
name: Docs
on:
  push:
    branches: [main]
    paths: ['docs/**', 'mkdocs.yml', '.github/workflows/docs.yml']
  pull_request:
    paths: ['docs/**', 'mkdocs.yml']
  workflow_dispatch:

permissions:
  contents: read     # required for checkout
  pages: write       # required even for deploy: false (see note below)
  id-token: write    # required even for deploy: false (see note below)

jobs:
  docs:
    uses: arthur-debert/release/.github/workflows/mkdocs.yml@v1
    with:
      requirements: docs/requirements.txt   # consumer's pin file
```

GitHub Pages setup: one-time, in repo Settings → Pages → set source
to "GitHub Actions". After that, the first push to main triggers
build + deploy.

**Permissions gotcha:** the caller MUST grant `pages: write` and
`id-token: write` even when running with `deploy: false`. GitHub
validates callee permissions at workflow-load time (before evaluating
`if:` guards), so the presence of the deploy job's permissions on the
callee forces the caller to grant them too. Missing them produces a
silent `startup_failure` with no useful detail in the UI. Grant all
three always; it's a one-time copy-paste.

## Plugin-agnostic by design

The Component does NOT decide theme or plugins. Whatever's in the
consumer's `requirements` file is installed verbatim. Examples
from the fleet:

- **dodot**: `mkdocs==1.6.1`, `mkdocs-material==9.7.6`,
  `mkdocs-lex-plugin==0.2.0` (lex-fmt's plugin that powers
  syntax-highlight for the `lex` editor language).
- **standout**: pinned set TBD (currently has a broken docs.yml
  that references `book.toml` — to be cleaned up as part of its
  mkdocs Component adoption).

The Component would happily build either site as-is. If a consumer
uses `poetry`, `uv`, `hatch`, etc. instead of pip + requirements.txt,
they pass `pre-install-cmd: 'uv sync --group docs'` (or equivalent).

## Why no pre-commit lefthook fragment

`mkdocs build --strict` is seconds-to-minutes on a real docs site —
slower than the rest of the pre-commit gate. Out of scope for
pre-commit; CI's the right place. `shell-quality`'s markdownlint
already lints the markdown source files at pre-commit time, which
covers the lightweight check.

## Deploy semantics

| Trigger                                | `deploy` input | Builds | Deploys |
|----------------------------------------|----------------|--------|---------|
| push to default branch (main)          | (default)      | ✓      | ✓       |
| pull_request                           | (default)      | ✓      | —       |
| push to non-main branch                | (default)      | ✓      | —       |
| any trigger                            | `false`        | ✓      | —       |
| any trigger                            | `true`         | ✓      | ✓       |

The default branch is detected via `github.event.repository.default_branch`
so renaming `main` → `default` doesn't break consumers.

## Concurrency

`mkdocs-${{ github.ref }}` — a second push to main waits for the
current deploy to finish rather than racing or canceling. PRs
have their own ref so they don't queue behind main.
