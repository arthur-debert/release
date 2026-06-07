Workflows

    `release/` ships reusable GitHub Actions workflows: one canonical pipeline
    per artifact category. A consumer doesn't copy a pipeline — it calls one
    with a thin `with:` block. The logic stays here; the consumer's workflow
    file is a few lines.

1. How a Consumer Uses Them

    A consumer's `.github/workflows/*.yml` is a thin caller (synced as a real
    copy — see injected-files.lex §3). It references a reusable workflow here
    by version.

    A consumer's thin caller looks like this:

        jobs:
          release:
            uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
            with:
              version: ${{ inputs.version }}
            secrets: inherit

    :: yaml ::

    The pin is a floating major: `@v2` always points at the latest non-breaking
    tag in that line, so patch and minor fixes here reach every consumer on
    their next run with nothing to edit. An exact pin (`@v2.1.3`) is available
    when a consumer needs to freeze. A breaking change cuts a new major and
    every consumer's thin caller must be bumped deliberately — that is the cost
    the versioning contract is designed to make rare (see CLAUDE.md and the
    README versioning table).

    :: note :: Cross-org consumers (lex-fmt/*) cannot use `secrets: inherit` —
    that only propagates within one owner. They must list each secret
    explicitly and may need name mapping. `arthur-debert/*` consumers inherit.

2. CI Gate vs Release Pipeline

    Most categories come as a pair:

    - `<category>-ci.yml` — the PR/push gate. Runs the test/lint/build checks
      (almost always by invoking the repo's `bin/check`, which runs the same
      lefthook gate). This is the required check on a PR.
    - `<category>.yml` — the full release pipeline. Build + sign + publish +
      tag, run on a release trigger, not on every push.

    Examples of the pairing: `rust-ci.yml` / `rust-cli.yml`, `go-ci.yml` /
    `go-cli.yml`, `tauri-ci.yml` / `tauri-app.yml`, `electron-ci.yml` /
    `electron-app.yml`.

3. The Catalog

    Runnable CLIs (build, sign, publish binaries):
        - `rust-cli.yml` — Rust CLI binaries across macOS/Linux/Windows;
          crates.io publish + brew formula.
        - `go-cli.yml` — Go CLI binaries, cross-compiled; ships from git tags
          (no registry), optional brew.

    Libraries (registry only, no binaries):
        - `rust-lib.yml` — publish library crates to crates.io, workspace-aware
          in topological order.
        - `python-pkg.yml` — build with `uv` and publish to PyPI / TestPyPI.

    GUI apps (release artifacts, signing):
        - `electron-app.yml` — electron-builder; macOS sign + notarize; GH
          release with .dmg / .AppImage.
        - `tauri-app.yml` — Tauri 2.x across the platform matrix; sign +
          optional notarize; bumps the three version files atomically.

    Editor extensions:
        - `vscode-ext.yml` — vsce package; Marketplace + optional Open VSX.
        - `zed-extension.yml` — wasm32-wasip2 build; bundles wasm + sources.
        - `nvim-plugin.yml` — source-only; validate, changelog, tag, release
          (the tag IS the version).

    Other stacks:
        - `tree-sitter.yml` — bundle .wasm + grammar + queries; optional npm
          publish.
        - `gh-action.yml` — composite actions + reusable workflows; validate,
          changelog, tag, advance the floating-major branch.

    Docs sites (deploy to GitHub Pages on push to main):
        - `mdbook.yml` — build an mdBook site.
        - `mkdocs.yml` — build a MkDocs site (`--strict`).

4. Shared and Infra Workflows

    Reusable (a consumer may call these):
        - `copilot-review.yml` — request a Copilot review on a PR. Needs a user
          PAT secret, since the Actions bot silently no-ops otherwise.
        - `cascade-handler.yml` — cross-repo release cascades; computes a
          version bump from commits and dispatches a consumer's release.
        - `bats-e2e.yml` — run a consumer's BATS e2e suite.
        - `tauri-e2e.yml` — run a consumer's Tauri e2e suite.

    Internal to this repo (its own CI; not for consumers):
        - `ci.yml` — this repo's CI; dogfoods `gh-action-ci.yml` and runs the
          lefthook gate.
        - `release.yml` — this repo's own release; dogfoods `gh-action.yml@v2`
          and publishes the `release_core` wheel as a release asset.
        - `copilot-review-self.yml` — applies `copilot-review.yml` to this
          repo's own PRs without leaking the token to forks.
        - The `*-tests.yml` suites — `changelog-tests`, `release-sync-tests`,
          `release-cut-tests`, `release-lex-tests`, `install-release-core-tests`,
          `pip-bootstrap-smoke`, `provision-gate-toolset-tests`, `audit-tests`.
          These verify the tooling here before it is synced out. They run on
          push/PR to this repo and are not called by consumers.

    :: note :: `release.yml` shipping the `release_core` wheel is the supply
    side of the pull-model boot in tooling.lex §4: this repo publishes the
    wheel, and every consumer's `install-release-core` pulls it at session
    start.
