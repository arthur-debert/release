# release

Reusable infrastructure for releasing software across the
arthur-debert and lex-fmt portfolios — ~20 repos spanning Rust CLIs
and libraries, Tauri / Electron desktop apps, editor extensions
(VS Code, Neovim, Zed), tree-sitter grammars, Python packages,
GitHub Actions, and a Homebrew tap. One canonical pipeline per
category; consumers call into it with a thin `with:` block.

Two properties make the model work:

- **Fix-once-propagate.** Consumers pin `@v1` (floating major), so
  a fix here propagates to every consumer on their next CI run. No
  per-repo edit.
- **Code + configuration, not just code.** Standardization covers
  runtime code (workflows, composite actions, scripts) *and*
  configuration documents that live in each consumer's tree
  (CODEOWNERS, dependabot.yml, branch-protection ruleset, agent
  guidance). Both surfaces need to stay current; the propagation
  model below handles each at the cheapest mechanism that fits.

Working-on-this-repo details: [`CLAUDE.md`](CLAUDE.md).
Long-form vision: [`docs/proposals/agentic-dev-workflow.lex`](docs/proposals/agentic-dev-workflow.lex).

## Vocabulary

Four levels, used throughout the rest of this doc:

- **Stack** — language/runtime profile. One Stack per consumer repo.
  Examples: `rust-cli`, `rust-lib`, `electron-app`, `tauri-app`,
  `vscode-ext`, `nvim-plugin`, `tree-sitter`, `python-pkg`,
  `gh-action`, `brew-tap`.
- **Component** — reusable capability module, orthogonal to Stack.
  Any Stack can compose any Component. Examples: `macos-codesign`,
  `brew-tap-push`, `mkdocs`, `bats`, `playwright`, `precommit-gate`,
  `markdown-lint`, `wasm-pack`, `gh-release`, `changelog`,
  `version-bump`.
- **Task** — atomic verb, provided by exactly one Component or
  Stack. Examples: `lint-markdown`, `test-unit-rust`, `test-e2e-bats`,
  `build-dmg`, `sign-mac`, `notarize-mac`, `publish-cargo`,
  `bump-version`, `roll-changelog`.
- **Flow** — trigger-bound sequence of Tasks. The triggers are
  `pre-commit`, `pr-checks` (push / PR), `release` (tag /
  `workflow_dispatch`), `nightly`. A Flow is what a CI workflow file
  actually maps to.

Mental model: Stack × Component grid says *what's available*;
Task × Stack/Component says *the atomic implementation*; Flow ×
Stack says *what runs when*. Note that "test" alone is a category,
not a Task (`test-unit-rust` is the Task); "check" is a Flow
composed of lint and test Tasks, not a Task itself.

## What every onboarded repo gets

Same regardless of Stack:

- **Branch protection** — main-branch ruleset: PR required, linear
  history, no force-push, no delete. Applied via `bin/apply-ruleset`,
  template at `rulesets/main-protection.json.tmpl`.
- **Auto-review on draft PR** — Copilot and Gemini both review on
  `pull_request: opened`, drafts included. Draft → ready does not
  re-trigger. Cloud's **Auto-fix** then wakes a fresh session per
  review comment (requires the
  [Claude GitHub App](https://github.com/apps/claude) at the org
  level). See "The agentic PR loop" below.
- **Policy files** — CODEOWNERS, `pull_request_template.md`,
  `copilot-instructions.md`, `dependabot.yml`,
  `.github/workflows/copilot-review.yml`. Synced via the propagation
  model below.
- **Pre-commit hooks** — thin husky / pre-commit-framework /
  lefthook config that shells out to release scripts. Local hooks
  call exactly the same checks CI calls; no divergence by
  construction.
- **Session-start bootstrap** — `scripts/setup-dev-env.sh` runs on
  every Claude Code session start (cloud and local). Handles
  submodules, deps, NSS cert import, venv PATH exposure, lefthook
  wiring.
- **Agent guidance** — per-repo `CLAUDE.md` for project-specific
  notes; user-level `CLAUDE.md` for portfolio-wide rules,
  distributed via [`env/CLAUDE.md`](env/CLAUDE.md).

### Dependabot policy

Three sub-policies, deliberate:

| Sub-role | Where enabled |
|---|---|
| Dependabot **security** updates | every onboarded repo (API toggle) |
| GitHub Actions **version** freshness | only `release/` and other CI-holding repos (`dependabot.yml`) |
| **Application dep** freshness (npm/cargo/...) | disabled, deliberately |
| **Security → patch release** glue | planned per-Stack |

Freshness mode at portfolio scale generates dozens of no-op PRs per
day. Major-version sweeps are evaluation work — picked up
deliberately, not pushed by a bot. Security automation is worth it,
but a bump that lands in main without a release leaves users on the
vulnerable binary, so the `security → patch release` glue is the
load-bearing piece.

## Capability matrix — Stack × Component

What release can do today. Rows = Stack; columns = Component bundles
the Stack pulls in. `✅` built and exercised end-to-end; `🚧`
scaffolded but not exercised; `📋` planned; `—` not applicable.

| Stack         | gh-release | changelog | version-bump | macos-codesign | precommit-gate | crate-publish | npm-publish | pypi-publish | brew-tap-push | bats | wasm-pack | mkdocs | commons-lint |
|---------------|:----------:|:---------:|:------------:|:--------------:|:--------------:|:-------------:|:-----------:|:------------:|:-------------:|:----:|:---------:|:------:|:------------:|
| rust-cli      | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅¹ | — | ✅ | ✅ | ✅¹ | 🚧 | 📋 |
| rust-lib      | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | — | — | — | — | 🚧 | 📋 |
| electron-app  | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — | — | — | — | 🚧 | 📋 |
| tauri-app     | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | — | — | — | — | 🚧 | 📋 |
| vscode-ext    | ✅ | ✅ | ✅ | — | ✅ | — | ✅² | — | — | — | — | 🚧 | 📋 |
| nvim-plugin   | ✅ | ✅ | ✅ | — | ✅ | — | — | — | — | 📋 | — | 🚧 | 📋 |
| tree-sitter   | ✅ | ✅ | ✅ | — | ✅ | — | ✅ | — | — | — | — | 🚧 | 📋 |
| python-pkg    | ✅ | ✅ | ✅ | — | ✅ | — | — | ✅ | — | — | — | 🚧 | 📋 |
| gh-action     | ✅ | ✅ | — | — | ✅ | — | — | — | — | 📋 | — | 🚧 | 📋 |
| brew-tap      | — | — | — | — | ✅ | — | — | — | (is tap) | 📋 | — | 🚧 | 📋 |

¹ Opt-in `wasm-package` slot — Rust workspace with a wasm-bindgen
member publishes both the crate and an npm tarball of the wasm build.
Used by `arami-core` and `lex-fmt/lex`.

² VS Code Marketplace + Open VSX (Open VSX off until Eclipse
namespace approval at
[EclipseFdn/open-vsx.org#10424](https://github.com/EclipseFdn/open-vsx.org/issues/10424)).

`mkdocs` is 🚧 across the board: shipped today as a copy-once
template (see [lex-fmt/mkdocs-lex docs deployment](https://github.com/lex-fmt/mkdocs-lex/blob/main/docs/deployment/examples/docs.yml)).
Lifting it to a first-class Component is a target on take-iii.

## Adoption matrix — Stack × Repo

Where each consumer actually sits. Four states:

- **planned** — repo identified for this Stack; no work yet.
- **implemented** — workflow caller wired; release path not
  yet exercised end-to-end on a real cut.
- **pilot-running** — at least one release shipped through the
  canonical path; remaining gaps tracked as issues.
- **fleet-adopted** — repo is the steady-state consumer; the Stack
  is considered the source of truth for its release path.

| Stack         | Repos (state) |
|---------------|---|
| rust-cli      | `dodot`, `lex-fmt/lex`, `padz`, `rustloc`, `burgertocow`, `treex` — **fleet-adopted** |
| rust-lib      | `clapfig`, `standout` — **pilot-running**; `lex-fmt/zed-lex` — **planned** (needs `wasm32-wasip2`) |
| electron-app  | `lex-fmt/lexed` — **pilot-running**; `simple-gal-ui` — **planned** |
| tauri-app     | `arami-app` — **pilot-running** |
| vscode-ext    | `lex-fmt/vscode` — **pilot-running** |
| nvim-plugin   | `lex-fmt/nvim` — **pilot-running** |
| tree-sitter   | `lex-fmt/tree-sitter-lex` — **pilot-running** |
| python-pkg    | (no managed-portfolio repos yet) |
| gh-action     | `release` (self, dogfooded) — **pilot-running**; `simple-gal-action` — **planned** |
| brew-tap      | `homebrew-tools` — **planned** |

A Stack flips to **fleet-adopted** only when ≥80% of its eligible
consumers are on `@v1` *and* have cut at least one release through
it. Today only `rust-cli` clears that bar.

Unclassified-stack repos in the managed portfolio (`arami-core`,
`lex-fmt/comms`, `simple-gal`): pending classification on take-iii.

## The agentic PR loop

Every onboarded repo follows the same review-and-merge flow. The
agent drives steps 1–6; a human gates step 7.

1. Agent opens the PR as a **draft**.
2. Draft state auto-requests **Copilot** review (via policy at
   `.github/workflows/copilot-review.yml`) and **Gemini** review
   (via the Gemini bot installed at the org level).
3. Agent waits for **both** reviews to post — batching over
   per-reviewer catches overlapping comments and produces one
   unified fix instead of two whipsaw fixes.
4. Agent combines feedback, decides what to address and what to
   push back on (see `skills/pr-review-respond` for the canonical
   pushback patterns).
5. For each comment: reply with the fix *or* a pushback reason,
   then resolve the thread. **Unresolved threads remain the signal
   that something is open** — making PR state trivially scannable.
6. Agent flips the PR from **draft → ready**.
7. Human reviews the final state and merges.

The four GitHub events the loop depends on:

- **review requested** — triggers the reviewer bots
- **review submitted** — wakes the agent to address comments
- **PR check results** — wakes the agent to fix failing CI
- **PR state change** — draft → ready, merged, closed

**What handles those events.** On cloud sessions, Anthropic's
**Auto-fix** (managed internally by Claude Code Cloud — *not* a
user-configurable [Routine](https://claude.ai/code/routines)) wakes
a fresh session when review comments or check failures land on a
PR in a repo with the
[Claude GitHub App](https://github.com/apps/claude) installed. The
leaf event-work — address this comment, fix this failing check —
is essentially free once the GitHub App is installed at the org
level.

The local orchestrator (below) does not duplicate this. Its scope
is the cross-repo, human-initiated work that cloud Auto-fix
deliberately doesn't do: rolling a change across N consumers,
driving a multi-PR epic, auditing a feature branch before merge.

Long-form spec: [`docs/proposals/agentic-dev-workflow.lex`](docs/proposals/agentic-dev-workflow.lex)
§2.

## How updates propagate

Two surfaces, two mechanisms.

### Workflows — `uses:` against `@v1`

Reusable workflows live at `.github/workflows/<stack>.yml` here.
Consumers call them with a thin caller:

```yaml
# in the consumer
jobs:
  release:
    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v1
    with:
      ...
```

`v1` is a floating branch that always points at the latest
non-breaking tag. Fix-once-propagate is automatic: patch a workflow
here, every consumer picks it up on their next CI run. This is the
mechanism today and it works.

### Files in the consumer — session-start `release-sync` 🚧

Some files have to live in the consumer's tree because GitHub or
local tooling reads them there: CODEOWNERS, dependabot.yml, the
`copilot-review.yml` workflow itself, lefthook fragments,
`scripts/check-*` helpers, the consumer's own `setup-dev-env.sh`.

**Current state.** Updates land via PR fan-out — `bin/sweep-github-policy`
loops over the portfolio and opens one PR per repo per change. This
is the bottleneck the take-iii branch replaces.

**Target model.** A pull-on-session-start sync with a beta-branch
dial:

1. `scripts/setup-dev-env.sh` runs on every session start (cloud
   and local — Claude Code's SessionStart hook fires the same way
   in both environments).
2. Early in the script, `release-sync` runs:
   - `git fetch --all --prune` against `~/release`
   - selects a branch: `release/beta/<consumer-repo-name>` if it
     exists, else `release/beta/<stack>`, else `main`
   - checks out the selected branch
   - copies managed files into the consumer working tree
3. CI's `pr-checks` Flow runs `release-sync --check`. If the
   working tree diverges from canonical, the build fails. Drift
   becomes a normal CI failure the agent fixes in passing — no
   batch fan-out PRs.

The beta-branch dial is how we test changes without coordinated
fleet rollout:

- Push a change for one repo: commit on `release/beta/dodot`.
- Push a change for one Stack: commit on `release/beta/rust-cli`.
- Ship to fleet: merge to main, delete the beta branch.

The burden lives in release's branch namespace. The cloud env
template and the consumer's setup script never change for routine
work; a new beta branch costs zero cloud-env clicks.

### Branch hygiene

`bin/release-beta-list` reports open `release/beta/*` branches with
age and ahead-of-main count. Stale betas are visible; the convention
is delete-on-merge.

## Local and cloud orchestration

The portfolio runs across two execution environments — Claude Code
on the web (cloud) and Claude Code locally. The infrastructure is
designed to be identical in both.

### Cloud env layer

[`env/setup.sh`](env/setup.sh) is pasted once into the
[claude.ai/code](https://claude.ai/code) **Setup script** field per
environment. It seeds:

- OS tooling: `gh`, `lefthook`, `bats`, `vsce`, `ovsx`, Lua +
  luarocks + busted + vusted, Neovim ≥0.11, `xvfb`,
  `libnss3-tools`, Tauri's GTK system libs.
- `~/.claude/skills/*` — standalone skills cloned from this repo.
- `~/.claude/CLAUDE.md` — user-level instructions from
  [`env/CLAUDE.md`](env/CLAUDE.md).
- `~/release` — a shallow blobless clone of this repo, pinned to
  `main`. Re-fetched on each session start by `release-sync`.

Pasted once per Claude environment; touched only when env-level deps
change. The `# version:` header invalidates the cached snapshot when
bumped.

### Per-session layer

Every onboarded repo carries `scripts/setup-dev-env.sh` +
`.claude/settings.json`, both seeded from
[`templates/setup-dev-env.sh`](templates/setup-dev-env.sh) and
[`templates/.claude-settings.json`](templates/.claude-settings.json).
The settings file registers a SessionStart hook that runs the script
on session start and resume. The script:

- Runs `release-sync` (target model; see propagation above).
- Restores submodules, fetches tags.
- Installs project deps via the right tool for the Stack (cargo
  fetch / npm / yarn / pnpm / bundler / pip into `.venv`,
  self-healing partial venvs).
- Imports the sandbox-egress CA into `~/.pki/nssdb` so Chromium /
  Electron / Playwright don't reject HTTPS with
  `ERR_CERT_AUTHORITY_INVALID`.
- Symlinks `.venv/bin/*` into `~/.local/bin/` so non-interactive
  Bash sessions resolve venv-installed CLIs without an active venv.
- Wires `lefthook install` (or falls back to symlinking
  `scripts/pre-commit`).

### SDK orchestrator harness 🚧

Goal: a local Python harness that drives multi-repo work the same
way a human would, using the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk)
(`claude_agent_sdk`, or `claude -p` for shell glue). One
`ProjectSession` per consumer, each with its own `cwd` and
`setting_sources=["project"]` so the consumer's `.claude/` config is
the source of truth.

Why now: cloud sessions handle single-repo, event-triggered work
well — Auto-fix wakes a fresh cloud session when review comments
or check failures land (see "The agentic PR loop" above). What
cloud cannot do is **cross-repo, human-initiated** work — rolling
a v1.8 change across 6 rust-cli consumers, driving a multi-PR epic
through lex's 3-deep dependency cascade, auditing take-iii against
affected repos before merge. The orchestrator handles exactly that
slice; it does not duplicate the cloud's PR-loop event handling.

Billing: subscription-billed via the local `claude` CLI's OAuth
token, *provided* `ANTHROPIC_API_KEY` is unset in the orchestrator's
environment. From 2026-06-15 onward, Agent SDK / `claude -p` usage
on subscription plans draws from a separate monthly credit pool.

Design TBD; lives at `orchestrator/` in this repo for now —
keeping it single-repo to avoid re-introducing the file-distribution
problem the rest of this doc fights against.

### Cross-compile spike 🚧

Goal: build Linux and Windows artifacts from a macOS host using
container-based cross-compile. Tauri ships official guidance; the
same approach works for plain Rust and Electron. Order:

1. `rust-cli` first — cheapest, biggest payoff. Unlocks full local
   end-to-end release for the Stack with the most consumers.
2. `tauri-app` — Tauri's docs cover this directly.
3. `electron-app` — electron-builder + Wine in container.

Payoff: typo-class bugs caught locally in seconds instead of
through a 30-minute CI loop.

## Versioning

| Bump | Trigger |
|---|---|
| PATCH (`v1.2.3` → `v1.2.4`) | bug fix, no input changes |
| MINOR (`v1.2.x` → `v1.3.0`) | new optional input, new opt-in feature, new category workflow |
| MAJOR (`v1.x.x` → `v2.0.0`) | required-input rename, default behavior change, removed input |

Tags: plain `vX.Y.Z`. Floating major: the `v1` branch always points
at the latest non-breaking tag. Anything that forces every consumer
to edit their thin caller is a MAJOR — coordinate the bump with all
consumers before cutting it.

See [`docs/breaking-changes.md`](docs/breaking-changes.md) for the
per-tag history.

## Layout

```
.github/
  workflows/          reusable workflows (one per Stack)
  actions/            composite actions (Components, called inside workflows)
bin/                  human-runnable tooling, on $PATH via dodot release pack:
                      apply-ruleset, sweep-github-policy,
                      install-release-{secrets,token}, detect-stack,
                      audit-portfolio, the gh-pr-* loop helpers,
                      release-sync 🚧, release-beta-list 🚧
rulesets/             branch-protection JSON templates
scripts/              CI scripts exec'd by composite actions
templates/
  setup-dev-env.sh    canonical per-session bootstrap
  .claude-settings.json   SessionStart hook config
  rust/, commons/     stack-specific and cross-cutting policy files
  homebrew-formula.rb.tmpl
orchestrator/         🚧 Python harness for local multi-repo
                      orchestration via Claude Agent SDK
env/                  cloud-session setup.sh + user-level CLAUDE.md
skills/               standalone Claude Code skills, cloned to
                      ~/.claude/skills/ in cloud sessions
docs/
  per-category/       input shapes per Stack
  proposals/          spec docs + rollout plans
  references/         design notes
tests/
  cloud-env-check/    local Docker harness approximating cloud Ubuntu
  fixtures/           tiny synthetic projects per Stack
examples/             paste-ready consumer release.yml files
```

Path convention is `~/h/release/`. Per-machine portability is not a
goal — this is single-developer infrastructure.

## See also

- [Vision: agentic dev workflow](docs/proposals/agentic-dev-workflow.lex)
- [Phased rollout](docs/proposals/phased-rollout.lex)
- [Per-category input shapes](docs/per-category/)
- [Breaking changes log](docs/breaking-changes.md)
- [Artifacts schema](docs/artifacts-schema.md)
- [Lex release cascade](docs/lex-release-cascade.md)
- [`CLAUDE.md`](CLAUDE.md) — working on this repo itself
