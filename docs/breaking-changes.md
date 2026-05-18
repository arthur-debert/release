# Breaking changes log

Versioning rules are summarized in `README.md` §Versioning. This file
records every cut tag and what it ships, with breaking changes called
out explicitly so consumers tracking `@v1` can plan ahead.

A release is **breaking** when it forces every existing consumer to edit
their thin caller — required-input rename, default-behavior change, or
removed input. A breaking change ships as a new MAJOR (`v2.0.0`),
coordinated with all consumers before cutting.

## v1.3.2 (2026-05-18) — fix: vscode-ext bash-3.2 empty-array under set -u

**Type:** PATCH. Caught on the second-attempt vscode pilot release
run (lex-fmt/vscode 0.10.2) after v1.3.1 unblocked the matrix
parse: macOS runners ship bash 3.2, which errors on
`"${arr[@]}"` when `arr` is an empty array under `set -u`. Linux
and Windows runners pass; only darwin builds (and any future
macos publish step) hit this.

Three sites in `vscode-ext.yml` — Package VSIX, Publish to
Marketplace, Publish to Open VSX — built a `pre_flag` array
guarding `--pre-release`. Switched all three to the canonical
bash-3.2-safe idiom `${arr[@]+"${arr[@]}"}` (expands to nothing
on empty/unset; to the elements otherwise).

## v1.3.1 (2026-05-18) — fix: vscode-ext jq parse + python-pkg uv-cache glob

**Type:** PATCH. Two bugs caught on the first end-to-end pilot release
runs against `lex-fmt/vscode` and `lex-fmt/mkdocs-lex`. No consumer
input changes; consumers tracking `@v1` re-run.

### `vscode-ext.yml` — matrix jq parse failure

The matrix-parse step's jq script used multi-line `if/elif/end` chains
with `#` trailing comments. jq on the GH runner reported `unexpected
if (Unix shell quoting issues?)` on each of the os/rust/platform
blocks (could not reproduce locally — the comments interacted with
the YAML+bash pipeline to leave the parser at `<top-level>` on the
next `if`).

Refactored to a plain lookup-table form (`$map[$t] // error(…)` then
`+ {target, arch}`). Same output shape, no multi-line if/elif, no
`#` comments inside the jq script.

### `python-pkg.yml` — `setup-uv` hard-fail on missing lockfile

`astral-sh/setup-uv@v3` with `enable-cache: true` errors hard when
its default `cache-dependency-glob` (`**/uv.lock`) matches no files.
mkdocs-lex is setuptools-backed and has no uv.lock — killed the
build job before publish.

Added a fall-through glob list: `**/uv.lock` first, then
`**/pyproject.toml`. Either keys the cache; both-absent projects
fall through cleanly.

## v1.3.0 (2026-05-18) — additive: Wave 1 cross-repo plumbing + Wave 2 stack workflows

**Type:** MINOR (additive, no consumer breakage). Existing `rust-cli`
consumers see byte-identical behavior.

### What's new

**Wave 1 — cross-repo plumbing** (consumed by stack workflows, not by
end consumers directly):

- `docs/artifacts-schema.md` — canonical `artifacts.json` schema for
  declaring releasable artifacts per repo (#59 / #62).
- `bin/fetch-artifact` + `.github/actions/fetch-artifact` — resolve
  `artifacts.json` entries to GH-release downloads with
  `{arch}` → rust-target-triple substitution and `binary | tree`
  type detection (#60 / #63).
- `.github/workflows/cascade-handler.yml` — reusable workflow for
  cross-repo `repository_dispatch upstream-released` cascades:
  manifest-vs-tag drift via `max(manifest, latest_tag)`,
  `UNRELEASED.md` seed, stale-release-branch cleanup,
  shell-injection guard (#61 / #64).

**Wave 2 — new stack workflows** (consumers can pin `@v1` and
migrate):

- `.github/workflows/electron-app.yml` (slice 1 + 1.5) — Electron
  builds across mac/linux/windows, native CSC signing via
  electron-builder, optional notarize override, smoke-test
  convention hook, output-dir input (#43 / #65 / #66).
- `.github/workflows/rust-lib.yml` — pure library crates (no CLI).
  3 jobs: prepare + publish-crates + create-release. Strict subset
  of rust-cli — use rust-cli if you publish both a binary and a
  library crate (#44 / #74).
- `.github/workflows/vscode-ext.yml` — VS Code extensions. Matrix
  of platform-specific VSIXes, Marketplace + Open VSX publish,
  prerelease threading, target whitelist, `scripts/pre-vsce-package.sh`
  convention hook for native-binary fetching via `fetch-artifact`,
  preflight secret validation, partial-release-prevention gating
  on the GH release job (#45).
- `.github/workflows/python-pkg.yml` — Python packages.
  `uv build` → sdist + wheel; `uv publish` → PyPI (required) +
  TestPyPI (opt-in). Awk-based pyproject.toml `[project].version`
  bump (zero toolchain deps). Same preflight + gating as
  vscode-ext. Optional `scripts/pre-build.sh` convention hook (#48).

**Composite actions added** (called by the above workflows):

- `prepare-release-npm` — `package.json`-rooted parallel of
  `prepare-release` (used by electron-app, vscode-ext).
- `prepare-release-python` — `pyproject.toml`-rooted parallel
  (used by python-pkg).

### Migration

No action required for existing `rust-cli` consumers. New-stack
consumers (lex-fmt/vscode, lex-fmt/mkdocs-lex, lexed) migrate per
the relevant stack callout in `README.md`.

## v1.2.1 (2026-05-06) — fix: skip homebrew-formula on prereleases

**Type:** PATCH (bug fix). The previous behavior pushed `vX.Y.Z-rc.N`
formulas to the Homebrew tap, replacing the stable formula. `brew
install <tap>/<formula>` users would silently end up on a prerelease
build — almost always not what's wanted. Brew has no native expression
for "this is a prerelease channel," so the right behavior is to skip
the tap push entirely on prereleases and let the stable release rebuild
the formula.

Caught during the v1.2.0 canary verification on lex-fmt/lex#510 (the
`v0.10.4-rc.1` test pushed an rc formula to `arthur-debert/homebrew-tools`
that had to be manually reverted).

If a consumer ever genuinely wants rc-to-brew, lift the gate via a new
opt-in input — don't roll back this default.

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
