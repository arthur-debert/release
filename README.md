# release

Shared software-development infrastructure for operating ~20 small projects
(Rust libs/CLIs, Electron + Tauri apps, editor extensions, tree-sitter grammars,
Python packages, GitHub Actions) the same way — locally and on CI. One shared
implementation per concern; consumers carry almost nothing.

Terms in **bold** are defined in [GLOSSARY.md](GLOSSARY.md).

## What it provides

1. **Local tools** — lint / format / static checks, tests, changelog, build, run,
   release — all through one `release-core <command>` CLI that knows a repo's
   **Kind** and routes the generic verbs to the real tool (cargo, npm, uv, …).
2. **CI tools** — one **reusable workflow** per Kind that runs the same tools on
   the server, plus the logic that triggers them.
3. **Agent harness** — a small pointer in `CLAUDE.md`, one distributed skill, and
   `release-core how-to`: how an agent orients and asks for help in any repo.
4. **The dev cycle** — the one standardized way of working
   ([docs/dev-cycle.lex](docs/dev-cycle.lex)), enacted by the `gh-pr-review-loop`
   skill.

Internally, release also carries **fleet ops** (`release-core admin …`) — its own
tools to probe, update, verify, and raise issues against consumers.

## The one rule: standardize, or there's no point

A release/ that lets repos keep doing their own thing is just a costly middle
layer while the divergence it was meant to kill survives. So the default is
**standardize the capability upstream — never leave a consumer hand-rolling it.**
"Expensive / risky to standardize correctly" is a reason to do it carefully
(canary per Kind), never a reason to leave it. Three tests keep that honest:

- **Is the capability _ordinary_?** Not "is this file different." Every project
  has the same lifecycle — lint/format → tests → provision → build (incl. docs)
  → release (incl. sign / publish / cross-repo cascade). All of it is ordinary;
  a consumer hand-rolling any of it is a bug. The *interface* is standard even
  when the per-stack impl differs.
- **The fold-in rule.** "Should release support a *new* capability?" is a real
  debate; "we *already have* it working in one of our repos" is not — parametrize
  it and move it upstream.
- **We own both sides.** Only a repo's core product *functionality* is sacred;
  its setup / scripts / paths / layout are ours to restructure to match the
  standard.

Genuinely bespoke product logic (theme-gen from the marketing repo, golden-image
checks) lives in a thin `app-bin/` hook — content bespoke, interface ordinary;
the list is short on purpose. The full model + how we verify it live-fire:
[docs/dev/standardization-model.md](docs/dev/standardization-model.md).

## How it reaches consumers (the pull model)

There is **no push.** A consumer's **footprint** is kept minimal and stable so it
never needs a coordinated edit across 20 repos. A consumer tracks only:

- `.github/**` — **thin callers** pinned at `@vN` + GitHub-forced policy files
  (CODEOWNERS, dependabot, copilot-review).
- The **bootstrap quartet** (real tracked files): `.claude/settings.json`,
  `bin/install-release-core`, `bin/pr-loop-guard`, `bin/delegate-guard` — enough
  for a fresh clone to boot itself.
- Optionally `.release-sync.yaml`, the one per-repo knob.

Everything else arrives at session start and never enters git. The SessionStart
hook runs `install-release-core`, which pulls the pinned `release_core` wheel from
this repo's GitHub releases and runs `release-core init` — which builds the
gitignored, ephemeral `.release/` tree (gate definition + tool configs) and the
untracked working-tree mirrors. It's rebuilt every session, so it can't fall out
of sync.

**Discovery is the CLI, not docs.** An agent landing in a consumer gets a small
managed block in `CLAUDE.md` pointing at `release-core how-to` — the Kind-aware
playbook for *that* repo — and `release-core --help` for the rest. Exactly **one**
skill ships as a file (`gh-pr-review-loop`, the `/`-triggered PR-loop driver); all
other guidance is rendered from the binary, always version-correct.

**Fix once, propagate by pull.** Workflows propagate because `@vN` floats to the
latest non-breaking tag; managed files propagate because the next session pulls the
new wheel. Nothing to edit in 20 repos.

## The two propagation surfaces

```yaml
# A consumer's entire release.yml:
jobs:
  release:
    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v3
    with:
      version: ${{ inputs.version }}
    secrets: inherit   # cross-org consumers list secrets explicitly
```

1. **Reusable workflows** — one release workflow per Kind (`rust-cli`, `rust-lib`,
   `go-cli`, `electron-app`, `tauri-app`, `vscode-ext`, `nvim-plugin`,
   `tree-sitter`, `zed-extension`, `python-pkg`, `gh-action`, plus `mkdocs`/`mdbook`
   docs sites), each paired with a `<kind>-ci.yml` PR gate where one exists. Catalog:
   [docs/tooling.lex](docs/tooling.lex) §6.
2. **The wheel** — the files a consumer needs locally ride the wheel; `release-core
   init` installs them per-Kind from `templates/` (commons → components → Kind; see
   [docs/references/component-model.md](docs/references/component-model.md)).

## The quality gate

`release-core gate` is the **gate**: `lefthook` over the composed `.release/lefthook.yml`
— **check-fast** (lint + format + static). It is one definition, **invoked**
everywhere it's needed — identical at session start, on commit (the hook is
`release-core gate --hook`), and in CI (the `arm-gate` composite) — never re-listed
per environment. It is a **hard** gate: a missing tool fails, never skips. To change
a check, edit a `lefthook` fragment in `templates/`, never a CI job.

CI runs **check-full** — the gate plus the unit + e2e suites (`release-core
test-all`) — as required checks. A green gate is necessary but not sufficient. Lint
debt is classified, not whack-a-moled:
[docs/references/lint-debt-model.md](docs/references/lint-debt-model.md).

## The dev cycle

Draft-first, state-machine-driven, human-merged. The model is
[docs/dev-cycle.lex](docs/dev-cycle.lex); `release-core pr status` computes the
lifecycle state + next action; the `gh-pr-review-loop` skill drives it (a PreToolUse
guard enforces going through the skill). Agents stop at ready-for-review — a human
merges.

Every feature/fix PR adds a changelog fragment (`changelog add <slug> "…"`); a
release refuses to cut without one.

## The release pipeline

Releasing spans one local stage and four CI stages. **Everything that mutates state
runs in CI** — the one exception is pure Rust library crates (`cargo publish` from
anywhere). Per-Kind coverage varies (not every Kind builds, signs, or publishes).

| Stage | Where | What |
|---|---|---|
| **0 · Pre-flight** | local (`release-core cut`) | detect Kind, bump + validate version, check the **canary** gate, dispatch the workflow — mutates nothing locally |
| **1 · Prepare** | CI | validate version, verify changelog fragment + version differs, bump manifest(s), roll changelog, commit + tag + push |
| **2 · Build** | CI | artifacts across the platform/arch matrix (skipped for pure-source Kinds) |
| **3 · Sign & Notarize** | CI | macOS binaries only today (rust-cli, go-cli, tauri-app, electron-app) |
| **4 · Publish** | CI | per-Kind, 0–N channels: crates.io / PyPI / npm / VS Code Marketplace / Open VSX / Homebrew tap / GitHub release |

`release-core cut` auto-advances the **floating major** at the end.

## Versioning

| Bump | Trigger |
|---|---|
| PATCH | bug fix, no input changes |
| MINOR | new optional input, opt-in feature, new Kind workflow |
| MAJOR | required-input rename, default behavior change, removed input |

Tags `vX.Y.Z`; the floating major branch (`v3` today) always points at the latest
non-breaking tag. Anything forcing every consumer to edit its thin caller is a
MAJOR, coordinated before cutting. History: `CHANGELOG.md` + git tags.

## Layout

```text
.github/workflows/    reusable workflows (one release pipeline per Kind,
                      *-ci.yml PR gates, shared infra)
.github/actions/      composite actions (arm-gate, prepare-release, …)
bin/                  NOT a consumer surface — only what can't be a
                      release-core subcommand (dev entry, pre-boot scripts,
                      HTTP-fetched fetchers, fleet-operator tools)
bin-internal/         CI-glue scripts; they CALL release-core, never reimplement it
templates/            what `release-core init` installs per consumer:
  commons/            universal set (incl. lib/release_core — the Python
                      package, shipped as the wheel)
  components/<c>/     one per Component
  <kind>/             Kind recipe: manifest.yaml + fragments + files
skills/               home for Claude Code skills; one is distributed
                      (gh-pr-review-loop), the rest are release-only
docs/                 four narrative .lex docs + ADRs + references
tests/                bats suites + fixtures (python tests live with the
                      package under templates/commons/lib/release_core)
examples/             paste-ready consumer release.yml files
```

## Docs

- [GLOSSARY.md](GLOSSARY.md) — the authoritative vocabulary.
- [docs/README.lex](docs/README.lex) — the map and reading order.
- [docs/tooling.lex](docs/tooling.lex) — the CLI, gate, PR state machine, pull
  model, installer, workflow catalog.
- [docs/harness.lex](docs/harness.lex) — the agent harness: bootstrap, orientation,
  skills.
- [docs/dev-cycle.lex](docs/dev-cycle.lex) — the development lifecycle.
- [CLAUDE.md](CLAUDE.md) — working on this repo itself.

Per-Kind "how do I build/test this" is `release-core how-to <kind>` output, not a
doc.
