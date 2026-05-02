# release

This repo contains the infrastructure around my projects (arthur-debert and lex-fmt), ensuring that:

- All effort poured into them is leveraged by all relevant projects.
- All projects can rely on all features / capacities.
- Less mental overhead from each of the 10 projects by their own idiosyncratic way to do releases.
- By having automated gh settings, setup and tooling, new projects can have a mature ecosystem in mints.

The scope includes:
    - The GitHub repo config (main branch policies, PR review policies / Copilot, )
    - The  workflows / actions  for doing test, checks and release, both in CI and locally when applicable.
    - Ability to run or update all these policy and settings (which should be idempotent, that is, safe)
    - Guidelines and support material (for example AGENT.md and equivalent) for AI agents
    - Distribution when relevant (like brew)

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
  fires on `pull_request: [opened, ready_for_review]` (T).
- **Policy files** — CODEOWNERS, `pull_request_template.md`,
  `copilot-instructions.md` (T, swept in via `bin/sweep-github-policy`).
- **Dependabot** — see policy below (T+R).
- **Pre-commit hooks** — thin husky/pre-commit config in the consumer
  that shells out to scripts here, so local hooks call exactly the same
  checks CI calls. No divergence by construction (T+S).
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

| Stack          | format/lint     | unit tests | e2e             | build              | sign       | gh release        | pkg publish       | dist: brew       | dist: apt | security→patch |
|----------------|-----------------|------------|-----------------|--------------------|------------|-------------------|-------------------|------------------|-----------|----------------|
| rust-lib       | W 📋            | W 📋       | —               | (cargo build)      | —          | (no binaries)     | A ✅ crates.io    | —                | —         | A 📋           |
| rust-cli       | W ✅            | W ✅       | W ✅ (BATS)     | W ✅ cross         | —          | W ✅              | A ✅ crates.io    | A ✅ shared tap  | A 📋      | A 📋           |
| electron-app   | W 📋            | W 📋       | W 📋 playwright | W 📋 builder       | A 📋 mac   | W 📋              | (auto-updater)    | (cask, future)   | —         | A 📋           |
| vscode-ext     | W 📋            | W 📋       | W 📋 ext-host   | W 📋 vsce package  | —          | W 📋              | A 📋 marketplace  | —                | —         | A 📋           |
| nvim-plugin    | W 📋 stylua     | W 📋 busted| W 📋 headless   | (source)           | —          | W 📋 (tag only)   | —                 | —                | —         | A 📋 (tag)     |
| tree-sitter    | W 📋            | W 📋 corpus| —               | W 📋 generate      | —          | W 📋              | A 📋 npm          | —                | —         | A 📋           |
| gh-action      | W 📋 actionlint | W 📋 bats  | W 📋 nektos/act | (composite)        | —          | W 📋 tag + v1     | A 📋 marketplace  | —                | —         | A 📋 (move v1) |
| mdbook-site    | W 📋 link-check | —          | —               | W 📋 mdbook build  | —          | (pages deploy)    | —                 | —                | —         | —              |
| jekyll-site    | W 📋            | —          | —               | W 📋 jekyll build  | —          | (pages deploy)    | —                 | —                | —         | —              |
| brew-tap       | W 📋 shellcheck | W 📋 bats  | W 📋 docker     | —                  | —          | (pulled, not released) | —            | (this IS the tap)| —         | (upstream)     |

What the matrix tells you at a glance: rust-cli is the only fully
shipped row. Every other stack is mostly 📋. Mechanism column tells
consumers what to expect when a row ships (mostly W = thin caller,
W+A = thin caller plus stack-specific composite for sign/publish).
`—` cells are deliberate — the role doesn't apply to that stack — so
nobody goes looking for missing pieces.

## Status & next-up

- ✅ **rust-cli** — 6 consumers on `@v1`, action stable at v1.0.5
  (see `git tag` for the current pin).
- 📋 **electron-app** — next up. Pilot consumer: `lex-fmt/lexed`.
  Follow: `lightable/simple-gal-ui`.
- 📋 then, in some order: rust-lib (clapfig, standout) · vscode-ext
  (lex-fmt/vscode) · nvim-plugin (lex-fmt/nvim) · tree-sitter
  (lex-fmt/tree-sitter-lex) · gh-action (lightable/simple-gal-action) ·
  mdbook-site (standout) · jekyll-site (lex-fmt/comms) · brew-tap
  (arthur-debert/homebrew-tools).

Order is driven by: (a) is there a consumer blocked on it today,
(b) does shipping the row unblock several at once. We complete one
stack vertically (every column on a row) before starting the next.

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
  workflows/         # reusable workflows (one per category, "W")
  actions/           # composite actions, called inside workflows ("A")
bin/                 # human-runnable policy tools (apply-ruleset,
                     # sweep-github-policy, install-release-secrets,
                     # install-release-token, detect-stack)
                     # on PATH via the dodot release pack
rulesets/            # ruleset JSON templates for branch protection
scripts/             # CI scripts exec'd by composite actions ("S")
templates/           # files templated into consumers ("T") +
                     # render templates (e.g. Homebrew formula)
tests/fixtures/      # tiny synthetic projects per category, exercised by _ci.yml
docs/                # consumer guide, secrets, breaking-changes log
examples/            # paste-ready consumer release.yml files
```

The path convention is `~/h/release/`. Per-machine portability is not a
goal — this is single-developer infrastructure, and the path is
recorded in agent memories so tools can find scripts here without
re-discovery.

## See also

- [Secrets and onboarding](docs/secrets.md)
- [Per-category input shapes](docs/per-category/)
- [Breaking changes log](docs/breaking-changes.md)
