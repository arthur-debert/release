# release

This repo contains the infrastructure around my projects (arthur-debert and lex-fmt), ensuring that:

- Consistent workflows — especially an agentic one — across multiple projects.
- Updates and changes benefit all projects at once.
- Less mental overhead from each of the ~16 projects having its own idiosyncratic way to do releases.
- By having automated GH settings, setup, and tooling, new projects can have a mature ecosystem in minutes.

The scope includes:

- The GitHub repo config (main-branch policies, PR review policies, Copilot wiring).
- The agentic workflow for development.
- Provisioning of projects, including Claude Code on the web sessions.
- The ability to run or update all these policies and settings idempotently (safe to re-run).
- Common workflows / actions for CI test and checks, release, and distribution.

## Properties

  Two properties make the above work in practice:

- **Fix-once-propagate.** Consumers pin `@v1` (floating major), so a fix
    here propagates to all of them on their next CI run. No per-repo edit.
    This is what makes the shared infrastructure pay for itself versus
    copy-pasted workflows.
- **Code + configuration, not just code.** Standardization covers two
    surfaces: runtime code that executes in CI (workflows, actions,
    scripts), and configuration documents that live in each consumer
    (CODEOWNERS, dependabot.yml, branch-protection ruleset, agent
    guidance markdown). Both need to be applied to every repo and kept
    in sync.
  
## What every onboarded repo gets

Same regardless of stack. Mechanism in parentheses (legend below):

- **Branch protection** — main-branch ruleset: PR required, linear
  history, no force-push, no delete (R, applied via `bin/apply-ruleset`,
  template at `rulesets/main-protection.json.tmpl`).
- **Copilot review auto-trigger** — `.github/workflows/copilot-review.yml`
  fires once on `pull_request: opened`, reviewing both drafts and
  ready PRs. Draft → ready does not re-trigger (T).
- **Policy files** — CODEOWNERS, `pull_request_template.md`,
  `copilot-instructions.md` (T, swept in via `bin/sweep-github-policy`).
- **Dependabot** — see policy below (T+R).
- **Pre-commit hooks** — thin husky/pre-commit config in the consumer
  that shells out to scripts here, so local hooks call exactly the same
  checks CI calls. No divergence by construction (T+S).
- **Claude Code on the web SessionStart bootstrap** —
  `scripts/setup-dev-env.sh` is the per-session dev-env bootstrap;
  `.claude/settings.json` carries the SessionStart hook that invokes
  it. Both are seeded from `templates/setup-dev-env.sh` and
  `templates/.claude-settings.json` here, and re-synced into consumers
  as the canonical evolves. Detects stack by filesystem signals;
  handles rust/node/ruby/python deps + git hygiene + Chromium NSS
  trust store + venv-CLI PATH exposure (T).
- **Agent guidance** — per-repo `CLAUDE.md` for project-specific notes;
  shared snippets live here and are referenced (G).

### Dependabot policy

Three sub-policies, deliberate:

| Sub-role | Mechanism | Where enabled |
|---|---|---|
| Dependabot **security** updates | R (per-repo API toggle) | every onboarded repo |
| **GitHub Actions version** freshness | T (`dependabot.yml`, `github-actions` ecosystem only) | only `release/` and any repo that holds its own CI workflows |
| **Application dependency** freshness (npm/cargo/etc.) | — | disabled, deliberately |
| **Security → patch release** glue | W + A | 📋 planned per-stack (auto-cut a patch release on security PR merge so users actually receive the fix) |

Rationale: freshness mode at portfolio scale generates dozens of
no-op PRs per day with near-zero value. Major-version sweeps (Tailwind
4 → 5, React 18 → 19, etc.) are evaluation work — picked up
deliberately when we want them, not pushed by a bot. Security is the
one case worth automating, but a security bump that lands in main
without a release leaves users on the vulnerable binary — so the
`security → patch release` glue is the load-bearing piece, not the
bump itself.

## Stack matrix

Mechanism legend:

| | |
|---|---|
| **W** | reusable workflow lives here, consumer has a thin caller (`uses: arthur-debert/release/...@v1`); fix-once-propagate works |
| **A** | composite action lives here, called inside a W |
| **S** | standalone script lives here, exec'd by an A or by pre-commit |
| **T** | file is templated *into* the consumer; can drift until the next sweep |
| **R** | applied via API, no file in consumer |
| **G** | markdown read by humans/agents |

Status: ✅ shipped · 🚧 in flight · 📋 planned · — N/A · `(...)` parenthetical context.

| Stack          | format/lint     | unit tests | e2e             | build              | sign       | gh release        | pkg publish                          | dist: brew       | dist: apt | security→patch |
|----------------|-----------------|------------|-----------------|--------------------|------------|-------------------|--------------------------------------|------------------|-----------|----------------|
| rust-lib       | (consumer)      | (consumer) | —               | W ✅ via publish   | —          | W ✅ (notes)      | A ✅ crates.io                       | —                | —         | A 📋           |
| rust-cli       | W ✅            | W ✅       | W ✅ (BATS)     | W ✅ cross         | —          | W ✅              | A ✅ crates.io · A ✅ npm (wasm)¹    | A ✅ shared tap  | A 📋      | A 📋           |
| electron-app   | (consumer)      | (consumer) | W 🚧 smoke conv | W ✅ builder       | A ✅ mac   | W ✅              | 📋 auto-updater   | (cask, future)   | —         | A 📋           |
| tauri-app      | (consumer)      | (consumer) | W 📋 playwright | W 📋 tauri build   | A 📋 mac   | W 📋              | 📋 auto-updater   | (cask, future)   | —         | A 📋           |
| vscode-ext     | (consumer)      | (consumer) | (consumer)      | W ✅ vsce package  | —          | W ✅              | W ✅ marketplace · W ✅ Open VSX | —    | —         | A 📋           |
| nvim-plugin    | W 📋 stylua     | W 📋 busted| W 📋 headless   | (source)           | —          | W ✅ tag + release | —                 | —                | —         | A 📋 (tag)     |
| tree-sitter    | W 📋            | W ✅ corpus³| —               | W ✅ generate      | —          | W ✅              | W ✅ npm (opt-in) | —                | —         | A ✅           |
| python-pkg     | (consumer)      | (consumer) | —               | W ✅ uv build      | —          | W ✅              | W ✅ PyPI · W ✅ TestPyPI (opt-in) | —    | —         | A 📋           |
| gh-action      | W 📋 actionlint | W 📋 bats  | W 📋 nektos/act | (composite)        | —          | W ✅ tag + v1     | (tag-driven²)     | —                | —         | A 📋 (move v1) |
| brew-tap       | W 📋 shellcheck | W 📋 bats  | W 📋 docker     | —                  | —          | (pulled, not released) | —            | (this IS the tap)| —         | (upstream)     |

What the matrix tells you at a glance: rust-cli is the only fully
shipped row. Every other stack is mostly 📋. Mechanism column tells
consumers what to expect when a row ships (mostly W = thin caller,
W+A = thin caller plus stack-specific composite for sign/publish).
`—` cells are deliberate — the role doesn't apply to that stack — so
nobody goes looking for missing pieces.

**Compiled artifacts** in the build/release columns can be native
binaries, wasm, or both — declared per consumer via workflow inputs
rather than being row-specific. Same pipeline shape; different
attached artifacts and publish destinations. ¹ below is the canonical
example.

**Documentation deploy is a cross-cutting feature, not a stack row.**
The portfolio standardised on **mkdocs** (with the lex-fmt/mkdocs-lex
plugin for Lex-format docs) as the single documentation toolchain.
Any consumer — regardless of its main-stack row — can adopt the
canonical deploy workflow by copying
[`lex-fmt/mkdocs-lex/docs/deployment/examples/docs.yml`](https://github.com/lex-fmt/mkdocs-lex/blob/main/docs/deployment/examples/docs.yml)
into its `.github/workflows/`. The workflow is intentionally shipped
as a **copy-once template**, not as a reusable `uses:` caller: it's
short, stable, and barely customisable, so the indirection cost of a
thin-caller mechanism outweighs its propagation benefit. Drift is
acceptable; re-copy if the upstream changes meaningfully. The earlier
`mdbook-site` and `jekyll-site` rows are retired — dodot/lex/standout
mid-port from mdbook, comms moving from jekyll.

² **gh-action — tag-driven Marketplace listing.** The GitHub Actions
Marketplace lists from a repo's release tags directly, gated by a
one-time repo-settings checkbox ("Publish this Action to the
GitHub Marketplace") at first release creation. There is no
publish API for CI to call — the `release` job creating a tag
*is* the publish.

³ **tree-sitter — corpus tests are release-gating.** Corpus tests
run in a dedicated `corpus-test` job that gates the `prepare`
job, so a `grammar.js` change that breaks the corpus blocks the
tag-push entirely — failed releases leave no dangling tags. Set
`run-corpus-tests: false` only when the PR-time test workflow is
authoritative and main is trusted green.

**tree-sitter bundle layout** (contractual with downstream
consumers):

```
tree-sitter.tar.gz
├── tree-sitter-<parser>.wasm  ← required
├── grammar.js                 ← required
├── package.json               ← required
├── tree-sitter.json           ← optional (if present in repo)
├── src/
│   ├── parser.c               ← required, freshly generated
│   ├── scanner.c | scanner.cc ← optional, gated by file existence
│   └── tree_sitter/
│       ├── parser.h           ← required
│       ├── alloc.h            ← optional (newer CLI versions)
│       └── array.h            ← optional (newer CLI versions)
└── queries/
    └── *.scm                  ← optional dir; if queries/ exists
                                 it MUST contain at least one .scm
```

Plus anything `scripts/bundle-extras.sh` adds (e.g.
`shared/embedded-grammars.json`).

¹ **rust-cli + npm (wasm)** — opt-in slot for Rust workspaces with a
wasm-bindgen crate consumed by JS/TS. Set `wasm-package: <member>` on
the caller and the canonical pipeline builds via `wasm-pack` once,
attaches `<member>-wasm.tar.gz` to the GH release, and `npm publish`es
the same artifact (no second Rust install, shares the warm cache).
Used today by `arami-core` (wasm consumed by `arami-app`) and
`lex-fmt/lex` (`lex-wasm` member). See
`docs/per-category/rust-cli.md` §WASM.

## Composition principle

Most repos have **two layers** of concerns: a stack-specific one (rust
build/test/release, electron-app sign+publish, etc.) and a cross-
cutting commons one (Markdown / YAML / shell lint, link-check, hook
wiring). The portfolio handles each at the cheapest mechanism that
fits.

- **Stack-specific concerns** ship as reusable workflows (`W`) +
  composite actions (`A`) here in `release/`, called by consumers via
  thin `uses: arthur-debert/release/.github/workflows/<stack>.yml@v1`
  blocks. Fix-once-propagate; consumers don't edit per-repo when a fix
  lands here. Single source of truth.
- **Cross-cutting commons concerns** ship in one of two forms:
  - As a separate reusable CI workflow (e.g. `commons.yml` —
    markdown / yaml / shell lint) that every consumer `uses:`-es
    alongside its stack workflow. Same `W` mechanism, just a new row
    that isn't "a stack."
  - As **copy-once templates** under `templates/commons/` (e.g.
    `lefthook.fragment.yaml`) when the file lives entirely in the
    consumer's checkout and a thin-caller mechanism wouldn't make
    sense. Drift is acceptable; re-copy when the upstream changes
    meaningfully. The mkdocs deploy workflow is the same pattern.

**Why not a fragment composer / template generator?** At our scale
(~10 stacks, ~16 consumers, single-developer) the maintenance cost of
a composition system (fragment ordering, merge correctness, generator
debugging) exceeds the duplication it would eliminate. If we ever hit
≥30 stacks or measurable drift incidents, the tool to reach for is
[copier](https://copier.readthedocs.io) — purpose-built for
template-with-resync — not ansible (which is fleet config, not
template composition). Until then, documented copy-paste + reusable
workflows cover ~80% of the benefit at ~5% of the cost.

## Status & next-up

All shipped stack workflows ship to `@v1` (floating major); see
`docs/breaking-changes.md` for the per-tag history. Consumers pin
`@v1` and never need to track individual minor/patch tags.

- ✅ **rust-cli** — `@v1`. 6 consumers migrated.
- 🚧 **electron-app** — `@v1`, slice 1 + 1.5. Consumers migrated:
  `lex-fmt/lexed` ✅. `arthur-debert/simple-gal-ui` pending
  migration. Windows builds opt-in and unsigned until slice 3;
  e2e + auto-updater are slice 2. See #43 (closed for slice 1) +
  follow-up issues.
- 🚧 **rust-lib** — `@v1`. Consumers migrated:
  `arthur-debert/clapfig` ✅, `arthur-debert/standout` ✅.
  `lex-fmt/zed-lex` pending (needs `wasm32-wasip2` target — likely
  fits a future `rustup-targets` input or a dedicated
  `zed-extension` micro-stack).
- ✅ **vscode-ext** — `@v1`. Pilot `lex-fmt/vscode` migrated +
  verified end-to-end (0.10.2 → Marketplace + 4 platform VSIXes;
  0.10.3 → Marketplace listing fix for the Eclipse namespace
  claim). Open VSX publish currently off in the caller — pending
  Eclipse Foundation approval of the `lex` namespace claim at
  [EclipseFdn/open-vsx.org#10424](https://github.com/EclipseFdn/open-vsx.org/issues/10424).
  Flip `publish-openvsx: true` in the caller once approved.
  Supports per-target VSIX matrix,
  `scripts/pre-vsce-package.sh` convention hook,
  `--pre-release` threading.
- ✅ **python-pkg** — `@v1`. Pilot `lex-fmt/mkdocs-lex`
  migrated + verified end-to-end (`mkdocs-lex-plugin` 0.2.1 →
  PyPI + GH release). `uv build` → sdist + wheel;
  `uvx twine upload --skip-existing` → PyPI / TestPyPI.
  Awk-based pyproject `[project].version` bump (zero toolchain
  deps). Optional `scripts/pre-build.sh` hook.
- ✅ **gh-action** — `@v1`. Canonical consumer = this repo
  itself; dogfooded end-to-end via the v1.5.0 / v1.6.0 / v1.6.1
  self-cuts. Tag + GH release + auto-advance of the floating-
  major branch (`v1`, `v2`, …) — the dist channel for this
  stack. GH Marketplace listing is tag-driven (one-time
  repo-settings checkbox); nothing for CI to do. JS-action
  `dist/` build slice deferred until the first JS-action
  consumer surfaces.
- ✅ **nvim-plugin** — `@v1`. Pilot `lex-fmt/nvim` pending
  migration. Tag + GH release with auto-rolled Keep-a-Changelog
  notes; no manifest bump (Neovim plugins are tag-distributed,
  no version field). Sanity check requires `lua/` or `plugin/`
  directory. Smoke testing deliberately deferred to a separate
  `nvim-plugin-test.yml` (PR-gate). Optional rockspec publish:
  future slice.
- ✅ **tree-sitter** — `@v1`. Pilot `lex-fmt/tree-sitter-lex`
  migrated + verified end-to-end (0.10.4 → tree-sitter.tar.gz
  on the GH release, bundle layout matches the contract,
  corpus-test gate held, downstream-notify fan-out fired
  successfully to vscode + lexed). nvim handler workflow has
  its own bug — tracked at
  [lex-fmt/nvim#59](https://github.com/lex-fmt/nvim/issues/59).
  Build: `tree-sitter generate` + corpus tests (release-gating)
  + `tree-sitter build --wasm`, assemble `tree-sitter.tar.gz`
  containing the standard parser bundle (layout is contractual
  with downstream callers — see footnote ³). Optional npm
  publish (off by default; most parsers ship via tarball only).
  `scripts/bundle-extras.sh` convention hook. Downstream
  `repository_dispatch` notifications are out of scope of this
  workflow (live in `cascade-handler.yml` + the consumer's
  thin caller).
- 📋 **tauri-app** — not started. Pilot:
  `arthur-debert/arami-app`. Will share ~70% of the
  electron-app shape (prepare-release-npm, smoke convention, GH
  release) with its own build/sign toolchain (`tauri build`,
  Tauri's `APPLE_*` env vars).
- 📋 **brew-tap** — not started. Tricky scope — the tap is the
  destination of pushes from other repos, not a release target
  itself. Likely workflow scope: tap-side validation (formula
  syntax, audit), stale-formula sweep, cross-formula CI.
- ✅ **Cross-repo artifact fetcher** —
  [`bin/fetch-artifact`](bin/fetch-artifact) +
  [`.github/actions/fetch-artifact/`](.github/actions/fetch-artifact/)
  read the canonical `artifacts.json` schema (see
  [`docs/artifacts-schema.md`](docs/artifacts-schema.md)) and install
  pinned cross-repo binaries / source trees from GH releases. Used by
  `scripts/setup-dev-env.sh` locally and by stack reusable workflows
  in CI — single implementation, single bug surface. Unblocks the
  electron-app / vscode-ext / nvim-plugin / python-pkg stack rollouts
  (they all consume upstream artifacts).
- ✅ **Cloud-session bootstrap** — `env/setup.sh` + canonical
  `templates/setup-dev-env.sh` + `templates/.claude-settings.json`
  deployed to all 16 consumer repos. Five iteration rounds folded
  in: apt-package consolidation, Python venv self-heal, Chromium
  NSS DB cert import (both system-bundle and standalone-PEM
  layouts), no-lockfile npm fallback, venv-CLI exposure on the
  agent's PATH. Local Docker harness (`tests/cloud-env-check/`)
  validates changes without burning a cloud session.
- 📋 next-up consumer migrations: `lex-fmt/nvim` (nvim-plugin),
  `arthur-debert/simple-gal-ui` (electron-app),
  `lex-fmt/zed-lex` (rust-lib + wasm32-wasip2),
  `arami-app` (after tauri-app workflow ships),
  `arthur-debert/simple-gal-action` (after gh-action JS-build
  slice ships).

Order is driven by: (a) is there a consumer blocked on it today,
(b) does shipping the row unblock several at once. We complete one
stack vertically (every column on a row) before starting the next.

## Claude Code on the web (cloud session distribution)

Separate from the CI infrastructure above, this repo also provisions
**Claude Code on the web** sessions across the portfolio in two
complementary layers.

### Layer 1 — env (once per Claude environment)

[`env/setup.sh`](env/setup.sh) is pasted into the **Setup script**
field at [claude.ai/code](https://claude.ai/code) → environment
settings, once. It runs as root, gets snapshotted, and seeds:

- OS-level tooling: `gh`, `lefthook`, `bats`, `@vscode/vsce`, `ovsx`,
  `lua5.4` + `luarocks` + `busted` + `vusted` + `luacheck`,
  `neovim` ≥0.11 (the apt package on noble is 0.9.5 which is too old
  for current `nvim-lspconfig`, so we overlay the official stable
  tarball under `/usr/local`), `xvfb`, `libnss3-tools` (provides
  `certutil` for the per-session Chromium NSS cert import), the
  Tauri/GTK system libs (`libgtk-3-dev`, `libwebkit2gtk-4.1-dev`,
  `libsoup-3.0-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`,
  `libjavascriptcoregtk-4.1-dev`), and `uuid-runtime`.
- `~/.claude/skills/*` cloned from this repo (standalone skills picked
  up by Claude Code at session start, no plugin install / trust
  prompt).
- `~/.claude/CLAUDE.md` from [`env/CLAUDE.md`](env/CLAUDE.md) —
  user-level instructions loaded into every cloud session in that
  env.

Per-env config that sits alongside the script:

1. `GH_TOKEN=<fine-grained PAT>` in the env-vars field, scoped
   `Contents/Issues/Pull requests: Read and write` on the related-repo
   group. `gh` reads it automatically.
2. (Optional, recommended) The
   [Claude GitHub App](https://github.com/apps/claude) installed on
   the relevant orgs so **Auto-fix** fires on review comments.

Updates land by pushing here, bumping the `# version:` header in
`env/setup.sh`, and re-pasting in the env UI. The version-header bump
invalidates the cached snapshot; the next session re-clones.

### Layer 2 — per-session (in each consumer repo)

Every onboarded repo carries `scripts/setup-dev-env.sh` and
`.claude/settings.json`, both seeded from
[`templates/setup-dev-env.sh`](templates/setup-dev-env.sh) and
[`templates/.claude-settings.json`](templates/.claude-settings.json).
The settings file registers a SessionStart hook that runs the script
on every session start (and resume); the script:

- Restores submodules + tags (cloud clones are shallow).
- Installs project deps via the right tool for the stack (cargo
  fetch / npm install — with a no-lockfile fallback — / yarn / pnpm
  / bundler / pip into `.venv`, self-healing partial venvs).
- Imports the sandbox-egress TLS-inspecting CA into `~/.pki/nssdb`
  so Chromium / Electron / Playwright don't reject HTTPS resources
  with `ERR_CERT_AUTHORITY_INVALID`. Probes both layouts seen in the
  cloud env (in-bundle and standalone-PEM under
  `/etc/ssl/certs/swp-ca-*.pem`).
- Symlinks every `.venv/bin/*` executable (except python/pip/activate
  family) into `~/.local/bin/` so `subprocess.run(['mkdocs', ...])`
  (and similar test patterns) resolve venv-installed CLIs without
  needing the venv activated. The cloud Bash tool's non-interactive
  shells inherit a fixed PATH and don't source `~/.bashrc`, so this
  symlink dance is the load-bearing PATH-exposure mechanism.
- Wires `lefthook install` (or falls back to symlinking a
  repo-local `scripts/pre-commit`).
- Below an explicit marker, consumers append project-local extras
  (Xvfb daemon start for Electron repos, pinned-binary fetches via
  `shared/lex-deps.json`, etc.).

Re-syncing the canonical: copy `templates/setup-dev-env.sh` verbatim
into the consumer's `scripts/setup-dev-env.sh`, preserve everything
below the marker, commit.
[`tests/cloud-env-check/`](tests/cloud-env-check/) validates the
chain locally with a Docker image that approximates the cloud Ubuntu
base, so template edits don't have to round-trip through a real cloud
session to find regressions.

### Currently shipped skills

- **`pr-review-respond`** — reply to and resolve PR review comments
  using `gh` + `jq`. Cloud-native alternative to the local
  `gh-pr-resolve-thread` script. Handles both Copilot and Gemini
  reviews; encodes pushback patterns for the four recurring wrong
  suggestions.
- **`gh-repo-setup`** — brings a repo up to the canonical release-loop
  setup (branch-protection ruleset, per-stack policy files,
  copilot-review wiring). Idempotent. Used when onboarding a new repo
  or verifying alignment.
- **`release-issue-relay`** — escalates infrastructure friction back
  to `arthur-debert/release`. Invoked when a cloud session hits a
  problem the consumer repo can't fix in place (workflow misbehavior,
  broken policy template, helper-script bug).
- **`lex-primer`** — primer for writing and reading `.lex` documents
  (Lex is not Markdown; the skill teaches the syntax).

### Where this is heading

See [`docs/proposals/agentic-dev-workflow.lex`](docs/proposals/agentic-dev-workflow.lex)
for the broader workflow vision and
[`docs/proposals/phased-rollout.lex`](docs/proposals/phased-rollout.lex)
for the execution plan. Phase 1 (this work) unifies the local-only and
cloud-distribution mechanisms under one main branch.

## Versioning

| Bump | Trigger |
|---|---|
| PATCH (`v1.2.3` → `v1.2.4`) | bug fix in any composite action, no input changes |
| MINOR (`v1.2.x` → `v1.3.0`) | new optional input, new opt-in feature, new category workflow |
| MAJOR (`v1.x.x` → `v2.0.0`) | required-input rename, default behavior change, removed input |

Tags: plain `vX.Y.Z`. Floating major: the `v1` branch always points at
the latest non-breaking tag. Consumers pin `@v1` for floating, `@v1.2.3`
for exact. Anything that forces every consumer to edit their thin
caller is a MAJOR — coordinate the bump with all consumers before
cutting it.

## Layout

```
.github/
  workflows/                  # reusable workflows (one per category, "W")
  actions/                    # composite actions, called inside workflows ("A")
bin/                          # human-runnable policy tools (apply-ruleset,
                              # sweep-github-policy, install-release-secrets,
                              # install-release-token, detect-stack)
                              # on PATH via the dodot release pack
rulesets/                     # ruleset JSON templates for branch protection
scripts/                      # CI scripts exec'd by composite actions ("S")
templates/
  setup-dev-env.sh            # canonical per-session bootstrap copied into
                              # each consumer's scripts/setup-dev-env.sh
  .claude-settings.json       # SessionStart hook config; copied into each
                              # consumer's .claude/settings.json
  rust/                       # stack-specific policy files swept into consumers
  commons/                    # cross-cutting copy-once snippets (markdown/
                              # yaml/shell lefthook fragment, etc.) — paste
                              # into the consumer's stack template, drift
                              # acceptable. See "Composition principle"
                              # section above.
  homebrew-formula.rb.tmpl    # render template
tests/
  cloud-env-check/            # local Docker harness — approximates cloud
                              # Ubuntu base, runs env/setup.sh + a consumer's
                              # setup-dev-env.sh + lefthook + tests; reports
                              # the first failing step
  fixtures/                   # tiny synthetic projects per category,
                              # exercised by _ci.yml
skills/                       # standalone Claude Code skills (one dir per
                              # skill, name = dir; cloned into
                              # ~/.claude/skills/ in cloud sessions by
                              # env/setup.sh)
env/                          # cloud-session env helpers — setup.sh +
                              # CLAUDE.md; paste-into-Cloud-UI script +
                              # user-level prompt
docs/                         # consumer guide, secrets, breaking-changes
                              # log, proposals/ for spec docs and rollout
                              # plans
examples/                     # paste-ready consumer release.yml files
```

The path convention is `~/h/release/`. Per-machine portability is not a
goal — this is single-developer infrastructure, and the path is
recorded in agent memories so tools can find scripts here without
re-discovery.

## See also

- [Secrets and onboarding](docs/secrets.md)
- [Per-category input shapes](docs/per-category/)
- [Breaking changes log](docs/breaking-changes.md)
- [Agentic dev-workflow vision](docs/proposals/agentic-dev-workflow.lex)
- [Phased rollout plan](docs/proposals/phased-rollout.lex)
