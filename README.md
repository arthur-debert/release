# release

Shared infrastructure for operating ~22 small projects (rust libs/CLIs,
electron + tauri apps, editor extensions, tree-sitter grammars, Python
packages, GH Actions, a Homebrew tap) the same way, both **locally and
on CI**. One canonical implementation per concern; consumers call into
it with a thin caller or sync canonical files via `release-sync`.

The full long-form rationale is in
[`docs/proposals/agentic-dev-workflow.lex`](docs/proposals/agentic-dev-workflow.lex).
The short version:

- **Before:** each project's lint / test / build / release / publish was
  a slightly different hand-rolled tree. Bug fixes meant grepping 20
  repos, opening 5–15 PRs, and babysitting 2 review bots per PR for
  hours.
- **After:** one canonical `bin/<verb>` per task (per Stack), one
  canonical reusable workflow per Flow. Fix once in `release/`,
  propagates to every consumer on their next CI run (workflows) or next
  session start (in-tree files via `release-sync`).

## Vocabulary

Five levels, used throughout the rest of this doc:

- **Stack** — language/runtime profile. One Stack per consumer repo.
  Examples: `rust-cli`, `rust-lib`, `electron-app`, `tauri-app`,
  `vscode-ext`, `nvim-plugin`, `tree-sitter`, `python-pkg`,
  `gh-action`, `brew-tap`, `zed-extension`.
- **Component** — reusable capability module, orthogonal to Stack. Any
  Stack composes any Components. Examples: `npm-quality`,
  `rust-quality`, `shell-quality`, `mkdocs`, `bats`, `macos-codesign`,
  `brew-tap-push`, `wasm-pack`, `gh-release`, `changelog`,
  `version-bump`.
- **Task** — atomic verb exposed as `bin/<verb>` in the consumer.
  Behavior varies per Stack; interface does not. The canonical task
  set: `bin/check`, `bin/check-fmt`, `bin/check-lint`,
  `bin/check-tests`, `bin/build`, `bin/release`. Stacks that ship docs
  add `bin/check-docs`; stacks with e2e add `bin/check-e2e`.
- **Flow** — user-level goal, implemented as a GH Actions workflow
  triggered by `push`, `pull_request`, `workflow_dispatch`, or schedule.
  The canonical flows: `ci.yml` (PR checks), `release.yml` (cut a
  release), `docs.yml` (publish docs), `on-upstream-released.yml`
  (cascade). Each is a thin caller of a `release/.github/workflows/*`
  reusable.
- **State** — where a (Stack, Repo) combo sits against the contract.
  See next section.

Mental model: **Stack × Component** says *what's available*; **Task**
is the atomic implementation under `bin/`; **Flow** is what triggers
which tasks; **State** is whether the consumer is actually using it.

## States — the contract

A (Stack, Repo) combo is in exactly one state.

| State           | Criterion (all must hold) |
|-----------------|---|
| **planned**     | Repo identified for this Stack; no work landed yet. |
| **implemented** | Thin-caller workflow wired AND `bin/<verb>` set synced AND every applicable verb passes **locally**. |
| **pilot-running** | All of *implemented* AND ≥1 successful end-to-end run **on CI** through the canonical workflow for every applicable Flow (PR checks green, ≥1 release dispatched + completed, docs published if applicable). |
| **fleet-adopted** | All of *pilot-running* AND ≥80% of eligible consumers of this Stack are themselves pilot-running; the canonical is the source of truth. |

**Definition of done:** a (Stack, Repo) is *done* when it is at least
**pilot-running** — i.e., every applicable verb works both locally
(`bin/<verb>`) and on CI (canonical workflow) end-to-end. *Implemented*
is "code is there"; only *pilot-running* is *done*.

A Stack is *done at the fleet level* when it is *fleet-adopted* — the
canonical works across ≥80% of its consumers.

**Enforcement:** `bin/done-check <consumer>` (🚧 not yet built — see
[#XXX]) reports the state for each Stack the consumer participates in,
listing missing verbs / unverified Flows. Until that lands, the
adoption matrix below is the manual proxy.

### The "really done" rule

> **Nothing that handles releases is really done if it isn't releasing
> things.**

A release workflow that exists but has never shipped a release is
*implemented*, not done. A CI workflow that exists but hasn't actually
gated a PR on its canonical run is *implemented*, not done. A
`bin/<verb>` that runs locally but isn't called from CI is
*implemented*, not done.

Code that hasn't done its job yet is *implemented* at best — never
*pilot-running*, never *done*. The states above codify this; this rule
is the one-line shorthand.

## Why both axes matter

The whole project goal is "same interface locally and on CI." A
consumer at *implemented* — bin/check passes locally but CI doesn't
run it — fails half the promise. Same for the inverse (CI runs
canonical but local dev can't reproduce). Both sides have to be true,
or we've just moved the inconsistency around.

## How work flows through `release/`

The canonical sequence for any new capability (bin/<verb>, Component,
Stack template, reusable workflow):

1. **Local.** Implement the work in `~/h/release/`. Verify each
   applicable verb passes locally — via stub binaries on `$PATH`, a
   scratch fixture repo, or read-through of the code path. **Don't
   dispatch the real side effect against a real consumer.**
2. **Sub-agent exercise.** Spawn a sub-agent with a prompt to drive a
   real end-to-end task using the new code — open a PR, dispatch a
   release, run e2e, whatever the work claims to do. The sub-agent is
   a fresh-eyes proxy for *"does this actually work?"*; it can use
   stub repos / fixtures so the exercise doesn't mutate production.
3. **Iterate.** Tweak the canonical based on what the sub-agent
   surfaces. Re-spawn until the sub-agent reports success.
4. **GH validation.** Trigger the same task on GitHub end-to-end (real
   workflow run, real release dispatch, real CI on a real consumer's
   PR). **The work isn't done until CI does it.** A consumer becomes
   *pilot-running* here, not earlier.
5. **Adoption.** Propagate to remaining consumers one at a time. Each
   adoption is its own PR, its own verification on its repo's CI. Not
   a fleet-wide fan-out. A Stack becomes *fleet-adopted* when ≥80% of
   its consumers clear step 4.

A workstream that stops at step 1 or 2 is *implemented*, not done. A
workstream that stops at step 3 is implemented + locally-verified;
still not done. Only after step 4 is a (Stack, Repo) pair
*pilot-running*; only after step 5 across the fleet is the Stack
*fleet-adopted*.

## Capability matrix — Stack × Component

What `release` can do today. Rows = Stack; columns = Component bundles
the Stack pulls in. `✅` built and exercised end-to-end; `🚧`
scaffolded but not exercised; `📋` planned; `—` not applicable.

| Stack         | gh-release | changelog | version-bump | macos-codesign | precommit-gate | crate-publish | npm-publish | pypi-publish | brew-tap-push | bats | wasm-pack | mkdocs | commons-lint |
|---------------|:----------:|:---------:|:------------:|:--------------:|:--------------:|:-------------:|:-----------:|:------------:|:-------------:|:----:|:---------:|:------:|:------------:|
| rust-cli      | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅¹ | — | ✅ | ✅ | ✅¹ | ✅ | ✅ |
| rust-lib      | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | — | — | — | — | 🚧 | ✅ |
| go-cli        | ✅ | ✅ | —⁴ | 🚧³ | ✅ | — | — | — | ✅⁵ | 🚧 | — | 🚧 | 📋 |
| electron-app  | ✅ | ✅ | ✅ | ✅³ | ✅ | — | ✅ | — | — | — | — | 🚧 | 📋 |
| tauri-app     | ✅ | ✅ | ✅ | 🚧³ | ✅ | — | — | — | — | — | — | 🚧 | 📋 |
| vscode-ext    | ✅ | ✅ | ✅ | — | ✅ | — | ✅² | — | — | — | — | 🚧 | 📋 |
| nvim-plugin   | ✅ | ✅ | ✅ | — | ✅ | — | — | — | — | 🚧 | — | 🚧 | 📋 |
| tree-sitter   | ✅ | ✅ | ✅ | — | ✅ | — | ✅ | — | — | — | — | 🚧 | 📋 |
| zed-extension | 🚧 | 🚧 | 🚧 | — | ✅ | — | — | — | — | — | 🚧 | — | 📋 |
| python-pkg    | ✅ | ✅ | ✅ | — | ✅ | — | — | ✅ | — | — | — | 🚧 | 📋 |
| gh-action     | ✅ | ✅ | — | — | ✅ | — | — | — | — | 🚧 | — | 🚧 | 📋 |
| brew-tap      | — | — | — | — | ✅ | — | — | — | (is tap) | 🚧 | — | 🚧 | 📋 |

¹ Opt-in `wasm-package` slot — Rust workspace with a wasm-bindgen
member publishes both the crate and an npm tarball of the wasm build.
Used by `arami-core` and `lex-fmt/lex`.

² VS Code Marketplace + Open VSX. Marketplace path validated through
v0.10.8 (lex-fmt/vscode). Open VSX `lex` namespace granted
2026-05-19 (EclipseFdn/open-vsx.org#10424); current `OVSX_PAT` fails
`verify-pat`, and first-publish through `ovsx` CLI got a stuck
"Extension Not Found" under the restricted-namespace first-publish
path. Token regen + Open VSX support follow-up pending.

³ Status as of 2026-05-22:

- `electron-app.yml@v1` uses the submit/poll/staple notarize path
  (port of the previously working bespoke `release.yml` from
  `simple-gal-ui`, landed in #176). **Pilot-running** — `lex-fmt/lexed`
  v0.10.6 shipped 2026-05-22
  ([run](https://github.com/lex-fmt/lexed/actions/runs/26315148328)),
  notarized arm64 DMG + Linux AppImage + Windows Setup.exe published
  in ~5.7 min. Closed [#167](https://github.com/arthur-debert/release/issues/167).
- `tauri-app.yml` uses `npx tauri build` with `APPLE_*` env vars
  (Tauri's own signing path, NOT electron-builder). **Pilot-running**
  — `arami-app` v0.1.7 shipped through the canonical 2026-05-19
  ([run](https://github.com/arthur-debert/arami-app/actions/runs/26113270868)),
  Gatekeeper-accepted DMG with stapled notarization ticket.

Tracked at [#122](https://github.com/arthur-debert/release/issues/122).

⁴ **go-cli — version-bump and tag-driven distribution.** Go modules
ship from Git tags directly; the `go release` job creating a tag IS
the publish for any downstream `go get` / `go install` consumer. No
registry-publish step.

⁵ **go-cli brew-tap-push — supports private repos.** Set
`brew-private-repo: true` and the rendered formula inlines a
`GitHubPrivateRepositoryReleaseDownloadStrategy`. End users export
`HOMEBREW_GITHUB_API_TOKEN=$(gh auth token)` before `brew install`.
Used by supage; the same option exists on rust-cli for parity.

## Adoption matrix — Stack × Repo

Where each consumer actually sits against the **States contract**.
Honest about what's verified vs claimed: a consumer is *pilot-running*
only if a real release / real CI run through the canonical can be
pointed at. Everything else is *implemented* at best, regardless of
how complete the local files look.

| Stack         | Repos (state) |
|---------------|---|
| rust-cli      | `padz` — **pilot-running** (canonical release shipped); `dodot`, `lex-fmt/lex`, `rustloc`, `burgertocow`, `simple-gal` — **implemented**¹ |
| rust-lib      | `clapfig` — **pilot-running**; `standout` — **implemented**¹ |
| electron-app  | `lex-fmt/lexed` — **pilot-running** (v0.10.6 shipped 2026-05-22 via `electron-app.yml@v1`, notarized DMG + AppImage + Setup.exe); `simple-gal-ui` — **implemented**¹ |
| tauri-app     | `arami-app` — **pilot-running** (v0.1.7 shipped 2026-05-19 via `tauri-app.yml@v1`, Gatekeeper-accepted DMG) |
| vscode-ext    | `lex-fmt/vscode` — **pilot-running**³ |
| nvim-plugin   | `lex-fmt/nvim` — **implemented**¹ |
| tree-sitter   | `lex-fmt/tree-sitter-lex` — **implemented**¹ |
| zed-extension | `lex-fmt/zed-lex` — **implemented** (template landed; no canonical CI/release workflow yet) |
| go-cli        | `supage` — **pilot-running** (`0.0.1` + `0.0.2` cut through canonical incl. brew-private-repo) |
| gh-action     | `release` (dogfooded), `simple-gal-action` — **planned** |
| brew-tap      | `homebrew-tools` — **planned** |
| python-pkg    | (no managed-portfolio repos yet) |

¹ Canonical wired locally and CI passes `bin/check`, but no real
release-through-canonical has been confirmed in the current quarter.
**Per the "really done" rule, this is *implemented*, not
*pilot-running*** — verify with `bin/done-check` (🚧 pending) before
upgrading the state.

³ Pilot-running for the Marketplace half — `lex-fmt/vscode` v0.10.7 +
v0.10.8 were dispatched through `vscode-ext.yml@v1` and shipped to VS
Code Marketplace. The Open VSX half is broken (OVSX_PAT fails
`verify-pat`, plus first-publish under the restricted-namespace path
left a stuck entry). The canonical workflow itself is proven; the Open
VSX gap is downstream config, not in the canonical.

**No Stack is currently *fleet-adopted***. Each will flip when ≥80%
of its consumers clear step 4 of the "How work flows" sequence.

## Managed repos

Canonical list. 22 repos across 3 multi-repo "projects" (lex, arami,
simple-gal) plus single-repo projects.

| Project | Repo | Local path |
|---|---|---|
| arami | `arthur-debert/arami-app` | `~/h/arami/arami-app` |
| arami | `arthur-debert/arami-core` | `~/h/arami/arami-core` |
| burgertocow | `arthur-debert/burgertocow` | `~/h/burgertocow` |
| clapfig | `arthur-debert/clapfig` | `~/h/clapfig` |
| dodot | `arthur-debert/dodot` | `~/h/dodot` |
| homebrew-tools | `arthur-debert/homebrew-tools` | `~/h/homebrew-tools` |
| lex | `lex-fmt/comms` | `~/h/lex-fmt/comms` |
| lex | `lex-fmt/lex` | `~/h/lex-fmt/lex` |
| lex | `lex-fmt/lexed` | `~/h/lex-fmt/lexed` |
| lex | `lex-fmt/nvim` | `~/h/lex-fmt/nvim` |
| lex | `lex-fmt/tree-sitter-lex` | `~/h/lex-fmt/tree-sitter-lex` |
| lex | `lex-fmt/vscode` | `~/h/lex-fmt/vscode` |
| lex | `lex-fmt/zed-lex` | `~/h/lex-fmt/zed-lex` |
| padz | `arthur-debert/padz` | `~/h/padz` |
| release | `arthur-debert/release` | `~/h/release` |
| rustloc | `arthur-debert/rustloc` | `~/h/rustloc` |
| simple-gal | `arthur-debert/simple-gal` | `~/h/simple-gal/simple-gal` |
| simple-gal | `arthur-debert/simple-gal-action` | `~/h/simple-gal/simple-gal-action` |
| simple-gal | `arthur-debert/simple-gal-ui` | `~/h/simple-gal/simple-gal-ui` |
| standout | `arthur-debert/standout` | `~/h/standout` |
| supage | `arthur-debert/supage` | `~/h/supage` |
| wave-term | `arthur-debert/wave-term` | `~/h/wave-term` |

Repos NOT in this list are out of scope. The `audit-portfolio`
discovery uses the `main-branch-protection` ruleset as a convenience
proxy, but the authoritative set is the table above.

## What every onboarded repo gets

Same baseline regardless of Stack:

- **Branch protection** via `bin/apply-ruleset` (PR required, linear
  history, no force-push). Template at
  `rulesets/main-protection.json.tmpl`.
- **Auto-review on PR open** — Copilot + Gemini review every PR;
  Cloud Auto-fix wakes a fresh session per review comment when the
  [Claude GitHub App](https://github.com/apps/claude) is installed.
- **Policy files** — CODEOWNERS, `pull_request_template.md`,
  `copilot-instructions.md`, `dependabot.yml`,
  `.github/workflows/copilot-review.yml`. Synced via `release-sync`.
- **Pre-commit hooks** — `lefthook.yml` generated from Component
  fragments, shells out to `bin/check-fmt` / `bin/check-lint` /
  `bin/check-tests`. **Local hooks ARE the canonical interface;** CI
  also calls these when the Stack's CI workflow has landed.
- **Session-start bootstrap** — `scripts/setup-dev-env.sh` runs on
  every Claude Code session start (cloud + local). Wires lefthook,
  fetches deps, imports cert store, fixes PATH.
- **Agent guidance** — per-repo `CLAUDE.md` for project-specific
  notes; user-level `CLAUDE.md` from
  [`env/CLAUDE.md`](env/CLAUDE.md) for portfolio-wide rules.

### Dependabot policy

| Sub-role | Where enabled |
|---|---|
| Dependabot **security** updates | every onboarded repo (API toggle) |
| GitHub Actions **version** freshness | only `release/` and other CI-holding repos |
| **Application dep** freshness (npm/cargo/...) | disabled, deliberately |
| **Security → patch release** glue | planned per-Stack |

Freshness mode at portfolio scale generates dozens of no-op PRs per
day. Major-version sweeps are evaluation work — picked up deliberately,
not pushed by a bot. Security automation is worth it, but a bump that
lands in main without a release leaves users on the vulnerable binary,
so the `security → patch release` glue is the load-bearing piece.

## CI workflow reusability gap (unflinching)

**The "fix once, propagate everywhere" model applies to the RELEASE
path universally today.** Every consumer's `release.yml` is a thin
caller of `arthur-debert/release/.github/workflows/<stack>.yml@v1`.

**The CI / PR-time check path is partially reusable.** `rust-ci.yml`,
`electron-ci.yml`, and `tauri-ci.yml` exist and are piloted (dodot,
lexed/simple-gal-ui, arami-app respectively). `go-ci.yml`,
`vscode-ci.yml`, `nvim-ci.yml`, `tree-sitter-ci.yml`, and
`zed-extension-ci.yml` do not exist yet. Remaining consumers hand-roll
`ci.yml` / `test.yml`.

Net effect: a fix to canonical lint flags propagates to consumers that
call `bin/check` from CI. A fix to `setup-rust`, cache config,
toolchain version pin, or the matrix shape propagates **only to
consumers that thin-call `<stack>-ci.yml`** — today that's a partial
set.

Tracked as part of #103 / #107. Until each Stack's `<stack>-ci.yml` is
adopted across its consumers, "fleet-adopted" means "uses release/'s
reusable RELEASE workflow + has Component-model files synced", not
"all of CI flows through release/".

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
    with: ...
```

`v1` is a floating branch that always points at the latest
non-breaking tag. Fix-once-propagate is automatic: patch a workflow
here, every consumer picks it up on their next CI run.

### Files in the consumer — session-start `release-sync`

Some files have to live in the consumer's tree because GitHub or
local tooling reads them there: CODEOWNERS, `dependabot.yml`,
`copilot-review.yml`, lefthook fragments, `bin/check-*` helpers,
`setup-dev-env.sh`.

`scripts/setup-dev-env.sh` runs `release-sync` early in every
session-start. It pulls the canonical files from `~/release` into the
consumer's working tree. CI's `pr-checks` Flow runs
`release-sync --check`; if the working tree diverges, the build fails
— drift becomes a normal CI failure the agent fixes in passing.

Beta-branch dial for testing changes without coordinated fleet
rollout:

- Push a change for one repo: `release/beta/<consumer-repo-name>`.
- Push a change for one Stack: `release/beta/<stack>`.
- Ship to fleet: merge to main, delete the beta branch.

`bin/release-beta-list` reports open beta branches with age and
ahead-of-main count. Stale betas are visible; the convention is
delete-on-merge.

## The agentic PR loop

Every onboarded repo follows the same review-and-merge flow.

1. Agent opens the PR (live, not draft, unless the user explicitly
   asked for draft).
2. Policy auto-requests **Copilot** and **Gemini** reviews.
3. Agent waits for **both** reviews — batching catches overlapping
   comments and produces one unified fix.
4. Agent combines feedback, decides what to address and what to push
   back on (see `skills/pr-review-respond`).
5. For each comment: reply with fix *or* pushback reason, then resolve
   the thread. Unresolved threads = something is open.
6. CI green + threads resolved → PR is mergeable.
7. **Human reviews the final state and merges.** The agent does not
   merge unless given an explicit merge instruction.

Cloud Auto-fix (Anthropic-managed, not user-configurable) wakes a
fresh session per review comment / failing check when the
[Claude GitHub App](https://github.com/apps/claude) is installed at
the org level. The local orchestrator (below) is for cross-repo,
human-initiated work that Cloud Auto-fix deliberately does not do.

Long-form spec:
[`docs/proposals/agentic-dev-workflow.lex`](docs/proposals/agentic-dev-workflow.lex)
§2.

## Local and cloud orchestration

Same infrastructure must work in three environments: local dev, Claude
Code Cloud sessions, and GitHub Actions runners.

### Cloud env layer

[`env/setup.sh`](env/setup.sh) is pasted once into the
[claude.ai/code](https://claude.ai/code) **Setup script** field per
environment. It seeds:

- OS tooling: `gh`, `lefthook`, `bats`, `vsce`, `ovsx`, Lua +
  luarocks + busted + vusted, Neovim ≥0.11, `xvfb`, `libnss3-tools`,
  Tauri's GTK system libs.
- `~/.claude/skills/*` — standalone skills cloned from this repo.
- `~/.claude/CLAUDE.md` — user-level instructions from
  [`env/CLAUDE.md`](env/CLAUDE.md).
- `~/release` — a shallow blobless clone of this repo, pinned to
  `main`. Re-fetched on each session start by `release-sync`.

Pasted once per Claude environment; touched only when env-level deps
change.

### Per-session layer

Every onboarded repo carries `scripts/setup-dev-env.sh` +
`.claude/settings.json`, both seeded from
[`templates/commons/`](templates/commons/). The settings file
registers a SessionStart hook that runs the script. The script:

- Runs `release-sync` (target model).
- Restores submodules, fetches tags.
- Installs project deps via the right tool for the Stack.
- Imports the sandbox-egress CA into `~/.pki/nssdb`.
- Symlinks `.venv/bin/*` into `~/.local/bin/` so non-interactive Bash
  sessions resolve venv-installed CLIs.
- Wires `lefthook install` (or falls back to symlinking
  `scripts/pre-commit`).

### SDK orchestrator harness 🚧

A local Python harness driving multi-repo work via the
[Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk). One
`ProjectSession` per consumer with its own `cwd` and
`setting_sources=["project"]` so the consumer's `.claude/` config is
the source of truth.

Cloud sessions handle single-repo, event-triggered work well; what
cloud cannot do is **cross-repo, human-initiated** work — rolling a
v1.8 change across 6 rust-cli consumers, driving a multi-PR epic,
auditing take-iii against affected repos before merge.

Lives at `orchestrator/` in this repo (single-repo on purpose).

### Cross-compile spike 🚧

Linux + Windows artifacts from a macOS host via container-based
cross-compile. Order:

1. `rust-cli` first — cheapest, biggest payoff.
2. `tauri-app` — Tauri's docs cover this directly.
3. `electron-app` — electron-builder + Wine in container.

## Versioning

| Bump | Trigger |
|---|---|
| PATCH (`v1.2.3` → `v1.2.4`) | bug fix, no input changes |
| MINOR (`v1.2.x` → `v1.3.0`) | new optional input, new opt-in feature, new category workflow |
| MAJOR (`v1.x.x` → `v2.0.0`) | required-input rename, default behavior change, removed input |

Tags: plain `vX.Y.Z`. Floating major: `v1` always points at the latest
non-breaking tag. Anything that forces every consumer to edit their
thin caller is a MAJOR — coordinate with all consumers before cutting.

Per-tag history: [`docs/breaking-changes.md`](docs/breaking-changes.md).

## Layout

```
.github/
  workflows/          reusable workflows (one per Stack + per-stack ci.yml)
  actions/            composite actions (Tasks called inside workflows)
bin/                  human-runnable tooling, on $PATH via dodot:
                      apply-ruleset, sweep-github-policy,
                      install-release-{secrets,token}, detect-stack,
                      audit-portfolio, audit-repo, the gh-pr-* loop
                      helpers, release-sync, release-beta-list,
                      release-cut, fetch-artifact
                      🚧 done-check (planned)
rulesets/             branch-protection JSON templates
scripts/              CI scripts exec'd by composite actions
templates/            path-mirror layout — sync destination = source
                      path minus the commons/, components/<c>/, or
                      <stack>/ prefix
  commons/            synced to every consumer (setup-dev-env.sh,
                      .claude/settings.json)
  components/<c>/     synced to consumers whose Stack declares <c>;
                      each Component ships its config files + a
                      lefthook.fragment.yaml. See
                      docs/references/component-model.md
  <stack>/            Stack recipe: manifest.yaml (default
                      Components) + optional lefthook.fragment.yaml +
                      Stack-specific files (including bin/build for
                      Stacks that ship one)
  render/             render templates (NOT synced — CI uses these
                      to produce per-release artifacts like brew
                      formulae)
orchestrator/         🚧 Python harness for multi-repo work
env/                  cloud-session setup.sh + user-level CLAUDE.md
skills/               canonical portfolio Claude Code skills:
                      - gh-pr-review-loop  (drive a PR through the
                        canonical pipeline)
                      - gh-repo-setup      (onboard a new repo)
                      - pr-review-respond  (Copilot/Gemini triage)
                      - release-issue-relay (file infra bugs against
                        release/ from inside a consumer)
                      - lex-primer         (writing valid .lex)
                      - lex-multirepo      (cross-repo lex work)
                      - padz-for-agents    (notes across sessions)
                      - electron-e2e-testing
                      - macos-signing-notarization
docs/
  per-category/       input shapes per Stack
  per-component/      adoption guides per Component
  per-stack/          adoption guides per Stack
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
- [Per-component adoption guides](docs/per-component/)
- [Per-stack adoption guides](docs/per-stack/)
- [Breaking changes log](docs/breaking-changes.md)
- [Artifacts schema](docs/artifacts-schema.md)
- [Lex release cascade](docs/lex-release-cascade.md)
- [`CLAUDE.md`](CLAUDE.md) — working on this repo itself
