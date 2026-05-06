# Breaking changes log

Versioning rules are summarized in `README.md` §Versioning. This file
records every cut tag and what it ships, with breaking changes called
out explicitly so consumers tracking `@v1` can plan ahead.

A release is **breaking** when it forces every existing consumer to edit
their thin caller — required-input rename, default-behavior change, or
removed input. A breaking change ships as a new MAJOR (`v2.0.0`),
coordinated with all consumers before cutting.

## v1.2.0 (2026-05-06) — additive: WASM/npm publish slot

**Type:** MINOR (additive, no consumer breakage). Existing callers
without `wasm-package` see byte-identical behavior.

### What's new

- `rust-cli.yml` gains three optional inputs: `wasm-package`,
  `wasm-pack-target` (default `bundler`), `wasm-npm-scope`.
- New optional secret: `NPM_TOKEN`.
- Two new jobs (`build-wasm`, `publish-npm`) that activate when
  `wasm-package` is set. See `docs/per-category/rust-cli.md` §WASM.
- Replaces a hand-rolled second workflow file pattern (`release-wasm.yml`)
  that was the lex-fmt/lex workaround prior to this release. That
  pattern reinstalled Rust, used a separate Cargo cache, and recompiled
  the dep tree. The canonical pipeline now does the build once and
  distributes the artifact to both surfaces (GH release + npm).

### Migration (consumers with the `release-wasm.yml` workaround)

1. Add three inputs to `release.yml`'s `with:` block:
   ```yaml
   wasm-package: <member>
   wasm-npm-scope: <scope>      # if scoped publish
   wasm-pack-target: bundler    # or web/nodejs/no-modules
   ```
2. Add `NPM_TOKEN` to the `secrets:` block.
3. Delete `.github/workflows/release-wasm.yml`.

For consumers without WASM needs: no action required.

## v1.1.1 (2026-05-05) — fix: copilot-review needs user PAT

**Type:** PATCH (within the new copilot-review.yml reusable workflow
shipped in v1.1.0; that workflow has no other consumers yet).

The reusable copilot-review workflow's body uses
`gh pr edit --add-reviewer @copilot`, which silently no-ops when invoked
with the default `GITHUB_TOKEN`. v1.1.1 makes the caller pass a user PAT
explicitly via `secrets.gh_token` — the existing `RELEASE_TOKEN` works.

Cross-org consumers must list `gh_token: ${{ secrets.RELEASE_TOKEN }}`
under `secrets:` since `inherit` doesn't propagate cross-org.

## v1.1.0 (2026-05-05) — additive: reusable copilot-review workflow

**Type:** MINOR. Adds `.github/workflows/copilot-review.yml` as a
reusable workflow. Per-repo callers now point at
`arthur-debert/release/.github/workflows/copilot-review.yml@v1` instead
of the previous home in `arthur-debert/gh-dagentic`. Replaces the silent
no-op `requested_reviewers` REST POST with `gh pr edit --add-reviewer
@copilot` (GraphQL) — the only mechanism that actually attaches Copilot.

(v1.1.0 was effectively broken in CI without a PAT pass-through; v1.1.1
ships the fix. `v1` floats forward to v1.1.1.)

## v1.0.x — initial rust-cli pipeline

`v1.0.5` was the floating tip prior to v1.1.0. Covers cross-target
binary builds, macOS sign + notarize, GH release, crates.io publish,
Homebrew formula push to the shared tap.
