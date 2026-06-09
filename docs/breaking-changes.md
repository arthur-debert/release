# Breaking changes log

Versioning rules are summarized in `README.md` §Versioning. This file
records every cut tag and what it ships, with breaking changes called
out explicitly so consumers tracking `@v1` can plan ahead.

A release is **breaking** when it forces every existing consumer to edit
their thin caller — required-input rename, default-behavior change, or
removed input. A breaking change ships as a new MAJOR (`v2.0.0`),
coordinated with all consumers before cutting.

> **Note (post `scripts/release` retirement).** Some older entries below
> reference per-repo `scripts/release/*` primitives
> (`get-current-version`, `get-commits-since-release`, `should-release`,
> `update-release`, `trigger-release`). Those primitives were **retired** —
> there are no per-repo release scripts anymore. Release is driven by the
> managed `bin/diff-since-release` (what changed / should-release) tooling
> synced into every consumer, plus the `release-core cut` console-script
> (formerly the `bin/release` shim, retired in #476) which reads the version
> from the Kind manifest and dispatches `release.yml`; CI does the bump +
> CHANGELOG roll + commit + tag + build. The historical
> entries are kept verbatim as a record of what each tag shipped at the time.
> See `docs/lex-release-cascade.md` for the current model.

## Unreleased — WS3: gate configs into `.release/`; the gate runs from the binary (#524)

**Type:** behavior change + reduced synced footprint. Not a caller-breaking change
(no workflow edits), but consumers' tracked files shrink on next `init` and the
git pre-commit hook changes shape.

Epic #501 ("invoke, don't discover"). The gate definition and its tool configs
leave the consumer root and live only in the ephemeral `.release/` build dir:

- **`lefthook.yml` + the lint/format configs** (`.markdownlint.json`,
  `.markdownlintignore`, `.yamllint`, `.shellcheckrc`, `.prettierignore`) are now
  **release-internal**: materialized into `.release/` but no longer mirrored to the
  consumer root. Each tool is handed its config explicitly — `markdownlint
  --config/--ignore-path`, `yamllint -c`, `shellcheck --rcfile`, `prettier
  --ignore-path` — all pointed at `.release/`. **`.editorconfig` stays mirrored**
  (editor-facing root convention, not a gate flag).
- **The git hook runs through the binary.** `release-core gate --install-hook`
  writes `.git/hooks/pre-commit` → `release-core gate --hook` (staged-set lefthook
  run, `--no-auto-install` so lefthook can't reclaim the hook). `setup-dev-env.sh`
  installs it when `.release/lefthook.yml` is present. There is no tracked root
  `lefthook.yml` for a stock `lefthook install` to discover (the WS4-deferred
  symlink drop, folded in here).
- **Migration is automatic.** A pre-WS3 consumer's committed root `lefthook.yml` +
  config symlinks still *resolve* (their `.release/` targets exist), so the
  broken-symlink sweep was generalized to a **mirrored-dest** rule: any
  `.release/`-pointing symlink whose dest is no longer mirrored is swept on next
  `init` and committed — no hand-editing.
- **Gate-hardening rider:** npm `typecheck` dropped `--if-present` — when TS files
  are staged but the project has no `typecheck` script, the gate **fails loud**
  (was a hollow green).

**Consumer impact:** automatic on next `init`. `release-core gate` (full,
`--all-files`) and the commit-time hook both resolve the gate from `.release/`.
The release-cut bot gate (`run-precommit-gate.sh`, rust `prepare-release`) prefers
`.release/lefthook.yml` via `LEFTHOOK_CONFIG`. Note: `release-core init
--config-only` (the legacy escape hatch) still writes the gate files to the root
and is now inconsistent with the gate's `.release/` config paths — slated for
removal in a follow-up.

## Unreleased — WS2: CLAUDE.md → stub; ORIENTATION + most skills no longer synced (#523)

**Type:** behavior change + reduced synced footprint. Not a caller-breaking change
(no workflow edits), but consumers' tracked files shrink on next `init`.

Epic #501 ("invoke, don't discover"). Orientation moves from synced files into the
binary's `release-core how-to`:

- **CLAUDE.md managed block → short stub** pointing at `release-core how-to` /
  `--help` / `gate` (no more `@.release/ORIENTATION.md` import). An existing
  consumer's old block is refreshed in place on the next `init` (markers unchanged
  → recognized, not duplicated). Consumer content below the block is untouched.
- **`ORIENTATION.md` retired** — no longer composed into `.release/`.
- **Synced skills trimmed** to `gh-pr-review-loop` + `release-issue-relay` (the two
  the harness needs a file on disk for). `pr-review-respond` + the 15 dev-cycle
  skills (tdd, review, diagnose, triage, to-issues, handoff, qa, grill-me,
  grill-with-docs, improve-codebase-architecture, request-refactor-plan,
  ubiquitous-language, zoom-out, teach, padz-for-agents) are no longer pushed; the
  dev-cycle guidance lives in `release-core how-to`. A consumer's now-dangling
  symlink to a dropped skill is **auto-swept** on the next `init` (WS4 cleanup) and
  committed — no hand-editing.

**Consumer impact:** automatic on next `init`. Agents orient via `release-core
how-to` (validated in the WS8 round-1 dogfood). The dropped dev-cycle skills remain
available as the agent's own global skills where installed.

## Unreleased — WS4: `.release/` ephemeral + drift/sync subsystem removed (#521)

**Type:** removed commands (breaking for any consumer/script that invoked them
directly) + behavior change (`.release/` no longer committed).

Epic #501 ("invoke, don't discover"). The composed `.release/` build dir is now
**gitignored and ephemeral** — recomposed on demand by `release-core init`
(SessionStart + CI), never committed. With the tree rebuilt from the pinned wheel
every session, drift of the build dir is impossible by construction, so the
drift/sync policing subsystem was retired.

**Removed (no longer on PATH / in the CLI):**

- the `release-sync` and `release-drift-check` console-scripts (and their
  `bin/` shims);
- the `release-core sync [run|drift-check]` CLI group.

**Replacement:** `release-core init` composes the managed tree (the SAME engine
the retired `release-sync` verb wrapped). Consumers self-sync at SessionStart and
in CI — nothing to invoke by hand. A consumer's CI gate materializes `.release/`
via the `arm-gate` composite (new `materialize` input, default on) before running
`bin/check`.

**Consumer migration (automatic, one-time):** the first `release-core init` after
this release untracks a previously-committed `.release/` (`git rm -r --cached`) and
commits the removal alongside the managed mirrors; the self-ignoring
`.release/.gitignore` (`*`) keeps it out thereafter. No manual steps. The
committed surface shrinks to the symlinks + the real-file workflow copies + the
CLAUDE.md managed block.

**Deferred:** dropping the root `lefthook.yml` discovery symlink (routing the gate
entirely via `LEFTHOOK_CONFIG`) is a separate follow-up; the root symlink stays
for now.

## v1.7.6 (2026-05-19) — feat: shared pre-commit gate before bot commits

**Type:** PATCH (additive; no required-input changes; existing
consumers see no behavior change unless they have a lefthook /
pre-commit / husky config).

Bot commits from inside the release workflow have always bypassed
client-side hooks by design — there is no `.git/hooks/pre-commit`
symlink in the CI checkout. Every stack's prepare step rewrites
version files with `jq` / `awk` / `perl`; none of those preserve
consumer formatting. The consumer's regular CI then runs
`prettier --check` / `eslint` / `ruff` on main, sees the bot's
formatting drift, and fails — even though the release workflow
itself succeeded end-to-end. Caught on `phos-app` 0.1.6.

**What was rejected:** an ad-hoc per-stack prettier patch in
tauri-app.yml (closed PR #92). Fragments ops; the whole point of
this repo is to standardize.

**What shipped:** a shared pre-commit gate. New composite action
at `.github/actions/run-precommit-gate@v1` is the canonical
reference, and every bot-committing prepare step inlines the
equivalent gate shell before its `git commit`. The gate:

1. Detects `lefthook.yml` / `.pre-commit-config.yaml` / `.husky/`
   in the consumer.
2. Installs the consumer's deps (auto-detects pnpm/npm/yarn for
   lefthook, pipx for pre-commit framework).
3. Runs the gate scoped to staged files (`--file` per staged
   path for lefthook 2.x; `--files` for pre-commit framework).
4. Re-stages any auto-fixes (prettier --write, eslint --fix,
   lefthook `stage_fixed: true`) so the bot commit captures
   them.
5. No-op when the consumer has no pre-commit framework
   configured.

Coverage (all 6 bot-committing prepare steps):
- `.github/actions/prepare-release` (rust-cli, rust-lib)
- `.github/actions/prepare-release-npm` (electron-app, vscode-ext, tree-sitter)
- `.github/actions/prepare-release-python` (python-pkg)
- `.github/workflows/tauri-app.yml` (tauri-app)
- `.github/workflows/nvim-plugin.yml` (nvim-plugin)
- `.github/workflows/gh-action.yml` (gh-action)

Inlined (not `uses:`) because each prepare step is a single
monolithic `run:` block — splitting step boundaries would
require restructuring stable workflow code. The composite
action exists as the canonical reference + external-caller
surface.

**Escape hatch:** set `SKIP_PRECOMMIT_GATE=true` in the prepare
job env to bypass entirely.

**Why inlined duplication is acceptable:** ~40 lines × 6 files.
The behavior is uniform, the snippet is small, and each location
is locally testable in isolation. If GitHub later supports
composite-action cross-references without ref pinning, we can
refactor to a single `uses:` call.

**Verified locally** against `/tmp/phos-app` (real
arthur-debert/phos-app clone): simulated jq bump, ran the
inlined gate snippet verbatim, observed:
- Prettier reformatted both bumped JSON files with consumer's
  tab style.
- Lefthook's `stage_fixed: true` auto-restaged the fixes.
- Total runtime: ~6 seconds (commands without matching globs
  were skipped naturally — no need for special filtering).
- Final staged content passes `prettier --check`.

## v1.7.5 (2026-05-19) — fix: tauri-app bundle-collect uses find (bash 3.2)

**Type:** PATCH. The bundle-collection step used bash globstar
(`shopt -s globstar` + `**/*.ext`) to walk
`src-tauri/target/release/bundle/`. macOS runners ship bash 3.2
which doesn't support globstar: `shopt: globstar: invalid shell
option name`.

Switched to `find "${bundle_root}" -type f \( -name '*.dmg' -o
… \)`. Portable across bash versions and matches the same set
of files.

Caught after the mac build + sign + notarization actually
succeeded end-to-end — the failure was purely in the post-build
artifact collection.

## v1.7.4 (2026-05-19) — fix: tauri-app build step exposes GH_TOKEN

**Type:** PATCH. Tauri consumers commonly fetch upstream
artifacts (WASM packages, asset bundles) in their
`scripts/build-tauri.sh` convention hook via `gh release
download` or `fetch-artifact`. Without `GH_TOKEN` in the
build step's env, those calls fail with `gh: To use
GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN
environment variable`.

Added `GH_TOKEN: ${{ secrets.RELEASE_TOKEN || github.token }}`
to the `Build` step's env block. Falls back to `GITHUB_TOKEN`
when no `RELEASE_TOKEN` secret is provided. Mirrors the same
pattern already applied to `vscode-ext.yml`'s
`pre-vsce-package` step.

## v1.7.3 (2026-05-19) — fix: tauri-app accepts passwordless .p12

**Type:** PATCH. The preflight job's `APPLE_CERT_PASS_PRESENT`
check rejected empty `APPLE_CERTIFICATE_PASSWORD` values — but
passwordless `.p12`s are valid per PKCS12 spec, and macOS
`security import` (Tauri's underlying tool) handles them.
Forcing a placeholder password would either fail at decrypt
time (wrong password) or require unrelated workflow hackery.

Dropped the `APPLE_CERT_PASS_PRESENT` requirement; updated the
input description noting empty is valid. `APPLE_CERTIFICATE` +
`APPLE_SIGNING_IDENTITY` are still required when `build-mac=true`.

## v1.7.2 (2026-05-19) — fix: nvim-plugin version-file detection (slurp)

**Type:** PATCH. v1.7.1's version-file pre-validation used
`perl -ne 'exit(... ? 0 : 1)'` which evaluates per-line and
exits on the FIRST line that doesn't match — so even when
`M.version` exists on a later line, the check incorrectly
errored with "no M.version declaration to bump".

Caught on the lex-fmt/nvim 0.10.3 verification cut after #61
landed; prepare job failed before tagging.

Fixed by switching both pre- and post-validation checks to
`perl -0777 -ne …; exit 1` — slurp the file as one record so
the regex sees the whole file at once. The substitution path
itself (line-by-line `perl -i -pe`) is unchanged and was
working correctly.

## v1.7.1 (2026-05-19) — fix: nvim-plugin adds version-file + prep-script

**Type:** PATCH. Caught on the lex-fmt/nvim pilot 0.10.2 cut.

Most Lua-based Neovim plugins carry an internal version constant
(typically `M.version = "X.Y.Z"` in `lua/<name>/init.lua`) that
plugin code references at runtime. The pre-migration release flow
relied on a consumer-side `scripts/release/update-release`
primitive to bump it. The slice-1 workflow assumed nvim plugins
have no version constant ("no manifest to bump") — which is true
for some plugins but not for the canonical convention.

Fixed by adding two optional inputs to `nvim-plugin.yml`:

- `version-file` — path to a Lua file containing
  `M.version = "X.Y.Z"`. Workflow bumps the constant via a
  perl substitution that handles either quote style. Refuses to
  silently no-op: if the input is set, the pattern MUST be present.
- `prep-script` — same shape as the input added to tauri-app.yml
  in v1.7.0. Optional script the workflow execs BEFORE the
  version-file bump, with `NEW_VERSION` + `PLUGIN_ROOT` in env.
  Use for: multiple version sources, submodule refreshes,
  upstream-dep pin syncs, etc. Generalized across stacks.

After this fix, lex-fmt/nvim's caller wires
`version-file: lua/lex/init.lua` and the manual release flow
(Mode A) keeps `M.version` in sync with the pushed tag.

## v1.7.0 (2026-05-18) — additive: tauri-app stack workflow (slice 1)

**Type:** MINOR (new category workflow; no consumer breakage).

### What's new

`.github/workflows/tauri-app.yml` — reusable release pipeline
for Tauri 2.x desktop apps. Pilot consumer:
`arthur-debert/phos-app`.

### Slice 1 scope

- Cross-platform `tauri build` matrix (macos-latest, ubuntu-
  latest, windows-latest); each opt-out-able via boolean inputs.
- Three-way version sync — `package.json` (jq), `src-tauri/
  Cargo.toml` `[package].version` (awk, same pattern as
  prepare-release-python), `src-tauri/tauri.conf.json` (jq;
  optional field).
- Optional `prep-script` input for consumer-specific bumps
  (phos-app's `deps.json` upstream pin; future
  Tauri apps with submodules, codegen, additional configs).
- macOS code-signing via Tauri's canonical `APPLE_CERTIFICATE`
  + `APPLE_CERTIFICATE_PASSWORD` + `APPLE_SIGNING_IDENTITY`
  (different bindings from electron-builder's `CSC_LINK`).
- Optional notarization via `APPLE_ID` + `APPLE_PASSWORD` +
  `APPLE_TEAM_ID` (gated by `notarize` input + secret presence).
- Preflight job validates all required Apple secrets BEFORE
  prepare bumps + tags.
- Linux build job installs the Tauri 2.x apt deps (libwebkit2gtk-4.1-dev,
  libgtk-3-dev, libsoup-3.0-dev, libayatana-appindicator3-dev,
  librsvg2-dev, patchelf).
- Bundle collection picks up the per-platform distributables
  only (`.dmg`, `.app.tar.gz`, `.deb`, `.rpm`, `.AppImage`,
  `.msi`, `.exe`) — skips raw `.app` source bundles and
  intermediate build files.
- `scripts/build-tauri.sh` convention hook with both branches
  cd-ing to `TAURI_DIR` for monorepo-layout symmetry.
- Auto-detects npm / pnpm / yarn via lockfile presence; pnpm
  wired via pnpm/action-setup@v4 BEFORE setup-node (order
  matters — setup-node's `cache: 'pnpm'` invokes `pnpm store
  path` at setup time).

### Dual-mode design (documented contract)

- **Mode A** — Standalone. Workflow handles version bump + tag
  + push + build + release. Fired via `gh workflow run
  release.yml -f version=X.Y.Z`.
- **Mode B** — Layer 0 + workflow (lex-fmt cascade pattern). A
  Layer 0 primitive (`scripts/release/update-release`) bumps
  consumer-specific files + tags + pushes locally; workflow
  hits the resume path (tag already exists, package.json
  matches), skips bump entirely, just builds + releases.

### Out of scope (deferred slices)

- Updater signing keys (`TAURI_SIGNING_PRIVATE_KEY`) → slice 2.
- Universal macOS binary (`--target universal-apple-darwin`) →
  slice 2.
- Auto-updater server config (`latest.json` host) → slice 3.
- Linux `.deb` / `.rpm` / `.AppImage` signing → slice 3.

## v1.6.1 (2026-05-18) — fix: tree-sitter setup-node cache optional

**Type:** PATCH. `actions/setup-node@v6` with `cache: 'npm'`
hard-fails when no matching npm-flavored lockfile is present —
but tree-sitter grammar repos commonly use `npm install` rather
than `npm ci` and ship no `package-lock.json`.

Fixed in both the `corpus-test` and `build` jobs by gating
`cache:` on `hashFiles()` of the supported npm lockfile paths:

```yaml
cache: ${{ hashFiles('package-lock.json', 'npm-shrinkwrap.json') != '' && 'npm' || '' }}
```

`cache: ''` is a valid value (means no cache). `yarn.lock` is
deliberately NOT in the list — `cache: 'npm'` doesn't read yarn
lockfiles, so including it would re-trigger the same hard-fail
in yarn-only repos.

Same class of bug + shape of fix as v1.3.1's `setup-uv` cache.

## v1.6.0 (2026-05-18) — additive: tree-sitter stack workflow

**Type:** MINOR (new category workflow; no consumer breakage).

### What's new

`.github/workflows/tree-sitter.yml` — reusable release pipeline
for tree-sitter grammar repos. Pilot consumer:
`lex-fmt/tree-sitter-lex`.

Pipeline:
- `preflight` validates `NPM_TOKEN` if `publish-npm=true`.
- `corpus-test` (gated by `run-corpus-tests`, default true) runs
  `tree-sitter generate` + `tree-sitter test` BEFORE `prepare`.
  A corpus failure thus blocks the tag-push entirely — no
  dangling tags on test failure.
- `prepare` (`prepare-release-npm`) bumps `package.json` + rolls
  changelog + tag + push.
- `build` re-generates the parser, builds WASM, assembles the
  canonical bundle (`<name>.wasm`, `grammar.js`, `package.json`,
  `tree-sitter.json`, `src/parser.c`, `src/scanner.{c,cc}`,
  `src/tree_sitter/*.h`, `queries/*.scm`), runs optional
  `scripts/bundle-extras.sh` hook, tars to `tree-sitter.tar.gz`.
- `publish-npm` (opt-in, default false) — uses `npm view` pre-check
  for idempotency, lets `package.json` `publishConfig.access`
  drive (no hardcoded `--access`).
- `release` — GH release with bundle attached; gated on
  `publish-npm` success-or-skipped.

### Bundle layout is a contract

Downstream consumers (e.g. `lex-fmt/vscode`'s
`pre-vsce-package.sh`) extract specific files from
`tree-sitter.tar.gz` by literal path. Bundle-layout changes are
MAJOR for this stack. Hard errors on missing WASM or empty
`queries/` enforce the contract at release time, not at
downstream-install time.

### Migration

No action required for existing consumers. New optional stack.

## v1.5.0 (2026-05-18) — additive: nvim-plugin stack workflow + dogfood release.yml

**Type:** MINOR (new category workflow + new caller in this repo;
no consumer breakage).

### What's new

- `.github/workflows/nvim-plugin.yml` — reusable release workflow
  for Neovim plugins. Tag + GH release with Keep-a-Changelog
  notes auto-rolled; no manifest bump (Neovim plugins are
  tag-distributed and don't carry a version field). Sanity check
  requires `lua/` or `plugin/` directory at the configured root.
  Smoke testing deliberately deferred to a separate
  `nvim-plugin-test.yml` (PR-gate, not release-gate). Pilot
  consumer: `lex-fmt/nvim`.
- `.github/workflows/release.yml` — dogfood. This repo cuts its
  own tags via its own `gh-action.yml@v1` from this version on.

### Migration

No action required for existing consumers. New optional stack.

## v1.4.1 (2026-05-18) — fix: gh-action skip manifest check for multi-action repos

**Type:** PATCH (input contract is unchanged; new behavior is opt-in
via passing an empty value to an existing input).

`gh-action.yml`'s `action-manifest` input still defaults to
`action.yml` and still fails the prepare job if missing. New
behavior: passing an empty string skips **only the
action-manifest existence check** (the semver-format validation
and other prepare-job checks are unaffected).
For multi-action repos that ship a bundle of composite actions
(`.github/actions/<name>/action.yml`) and/or reusable workflows
(`.github/workflows/<name>.yml`) but have no single `action.yml`
at the repo root — `arthur-debert/release` itself being the
motivating case.

## v1.4.0 (2026-05-18) — additive: gh-action stack workflow

**Type:** MINOR (new category workflow, no consumer breakage).

### What's new

`.github/workflows/gh-action.yml` — reusable release pipeline for
composite GitHub Actions and reusable workflows housed in the same
repo. The canonical consumer is `arthur-debert/release` itself.

Jobs:
- `prepare` — validate semver, sanity-check `action.yml` exists,
  inline awk-based changelog roll (no need to vendor
  `roll-changelog.sh` in caller repos), commit + tag + push.
- `release` — GH release with notes; prerelease flag when semver
  carries a suffix.
- `advance-major` — force-update `v<MAJOR>` branch → new tag.
  Skipped on prereleases so `X.Y.Z-rc.N` doesn't yank `v1`
  forward.

The dist channel for this stack IS the floating-major branch
(`v1`, `v2`, …). GH Actions Marketplace lists from release tags
via a one-time repo-settings checkbox; no publish API to call.

Out of scope (deferred until first consumer needs it):
- JS-action `dist/` build step + `package.json` version bump.
  Pure-composite actions have no build, no manifest.

### Migration

No action required for existing consumers. New optional path for
composite-action repos.

## v1.3.3 (2026-05-18) — fix: python-pkg switches publish from `uv publish` to `twine`

**Type:** PATCH. Caught on the lex-fmt/mkdocs-lex pilot. `uv publish`
(as of uv 0.11.14) trips a `403 Forbidden — Invalid or non-existent
authentication information` from PyPI **specifically in GitHub
Actions runs**, with a known-good project-scoped token: local
`uv publish` of the same CI-built dist files with the same token
succeeded; the CI run 403'd. Could not isolate further without
patching uv.

`twine upload` is the decades-canonical PyPI upload tool — what
the hand-rolled pre-migration workflow used. The new step invokes
it via `uvx twine upload --skip-existing dist/*`, so no extra
`setup-python` step is needed: uv's ephemeral tool env fetches
twine on demand.

`--skip-existing` preserves the resume-on-partial-publish behavior
we relied on with `uv publish`'s implicit idempotence.

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
  `wasm-package` is set. See the `inputs:` block of
  `.github/workflows/rust-cli.yml` for the full WASM input shape.
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
