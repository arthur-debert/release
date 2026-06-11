# release

Shared infrastructure for operating ~20 small projects (Rust libs/CLIs,
Electron + Tauri apps, editor extensions, tree-sitter grammars, Python
packages, GH Actions) the same way, locally and on CI. One canonical
implementation per concern; consumers carry almost nothing.

## The design

**The binary is the carrier ("invoke, don't discover").** A consumer repo
tracks only an irreducible set of files:

- `.github/**` — thin workflow callers pinned at `@vN` + GitHub-forced
  policy files (CODEOWNERS, dependabot, copilot-review).
- The **bootstrap quartet**, real tracked files: `.claude/settings.json`,
  `bin/install-release-core`, `bin/setup-dev-env.sh`, `bin/pr-loop-guard` —
  enough for a fresh clone to boot itself.
- Optionally `.release-sync.yaml`, the one per-repo knob.

Everything else arrives at session start and never enters git. The
SessionStart hook runs `install-release-core`, which pulls the pinned
`release_core` wheel from this repo's GitHub releases and runs
`release-core init` — composing the gitignored, ephemeral `.release/` build
dir (gate definition + tool configs) and the untracked working-tree mirrors
(`bin/` task verbs, `.editorconfig`, the skill). Recomposed every session,
drift is impossible by construction.

**Discovery is the CLI, not docs.** An agent landing in a consumer repo
gets a two-line stub in `CLAUDE.md` pointing at `release-core how-to` —
the kind-aware playbook for *that* repo (lint/test/build/release + the
draft-first dev cycle) — and `release-core --help` for the rest. One skill
ships as a file (`gh-pr-review-loop`, the `/`-triggered PR-loop driver);
all other guidance is rendered from the binary, version-correct.

**Fix once, propagate by pull.** Workflows propagate because `@vN` floats
to the latest non-breaking tag; managed files propagate because the next
session pulls the new wheel. There is no push mechanism and nothing to
edit in 20 repos.

## The two propagation surfaces

```yaml
# A consumer's entire release.yml:
jobs:
  release:
    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
    with:
      version: ${{ inputs.version }}
    secrets: inherit   # cross-org consumers list secrets explicitly
```

One reusable release workflow per Kind (`rust-cli`, `rust-lib`, `go-cli`,
`electron-app`, `tauri-app`, `vscode-ext`, `nvim-plugin`, `tree-sitter`,
`zed-extension`, `python-pkg`, `gh-action`, plus `mkdocs`/`mdbook` docs
sites), each paired with a `<kind>-ci.yml` PR gate where one exists. The
catalog: [docs/tooling.lex](docs/tooling.lex) §6.

Files a consumer needs locally ride the wheel: `release-core init`
composes them per-Kind from `templates/` (commons → capabilities → kind;
see [docs/references/component-model.md](docs/references/component-model.md)).

## Vocabulary

- **Kind** — the repo's language/runtime profile (`rust-cli`,
  `tauri-app`, …). One per consumer; detected by `detect-kind`.
- **Capability** — a reusable module a Kind composes (`rust-quality`,
  `mkdocs`, `bats`, …). Each ships configs + a lefthook fragment.
- **Consumer** — a repo managed by this one. The authoritative list is
  `managed-repos.yaml` (`release-core admin repos list`); there is no
  auto-discovery.

## The quality gate

`release-core gate` is the ONE quality entry: lefthook over the composed
`.release/lefthook.yml`, identical at session start, on commit (the hook is
`release-core gate --hook`), and in CI (the `arm-gate` composite). It is a
HARD gate — a missing tool fails, never skips — and it is one definition:
to change a check, edit a lefthook fragment in `templates/`, never a CI
job. Lint debt is classified, not whack-a-moled:
[docs/references/lint-debt-model.md](docs/references/lint-debt-model.md).

## The dev cycle

Draft-first, state-machine-driven, human-merged. The model is
[docs/dev-cycle.lex](docs/dev-cycle.lex); `release-core pr status` computes
the lifecycle state + next action; the `gh-pr-review-loop` skill drives it
(a PreToolUse guard enforces going through the skill). Agents stop at
ready-for-review — a human merges.

Every feature/fix PR adds a changelog fragment (`changelog add <slug>
"..."`); a release refuses to cut without one. Cut with `release-core cut
X.Y.Z`; CI builds, publishes the wheel, and advances the floating major.

## Versioning

| Bump | Trigger |
|---|---|
| PATCH | bug fix, no input changes |
| MINOR | new optional input, opt-in feature, new Kind workflow |
| MAJOR | required-input rename, default behavior change, removed input |

Tags `vX.Y.Z`; the floating major branch (`v2` today) always points at the
latest non-breaking tag. Anything forcing every consumer to edit its thin
caller is a MAJOR, coordinated before cutting. History: `CHANGELOG.md` +
git tags.

## Layout

```
.github/workflows/    reusable workflows (one release pipeline per Kind,
                      *-ci.yml PR gates, shared infra)
.github/actions/      composite actions (arm-gate, prepare-release, …)
bin/                  maintainer CLI surface; canonical entry is
                      `release-core <group> <cmd>` (`--help` is the map)
bin-internal/         CI-side scripts exec'd by actions/workflows
templates/            what `release-core init` composes per consumer:
  commons/            universal set (incl. lib/release_core — the Python
                      package, shipped as the wheel)
  components/<c>/     one per Capability
  <kind>/             Kind recipe: manifest.yaml + fragments + files
skills/               canonical home for Claude Code skills; one is
                      distributed (gh-pr-review-loop), rest are
                      maintainer/release-only (docs/harness.lex)
docs/                 four narrative .lex docs + ADRs + references
tests/                bats suites + fixtures (python tests live with the
                      package under templates/commons/lib/release_core)
examples/             paste-ready consumer release.yml files
```

## Docs

- [docs/README.lex](docs/README.lex) — the map and reading order.
- [docs/tooling.lex](docs/tooling.lex) — the CLI, gate, PR state machine,
  pull model, compose engine, workflow catalog.
- [docs/harness.lex](docs/harness.lex) — the agent harness: bootstrap,
  orientation, skills.
- [docs/dev-cycle.lex](docs/dev-cycle.lex) — the development lifecycle.
- [CLAUDE.md](CLAUDE.md) — working on this repo itself.

Per-Kind "how do I build/test this" is `release-core how-to <kind>`
output, not a doc.
