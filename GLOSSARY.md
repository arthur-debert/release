# Glossary

The authoritative vocabulary for release/. One definition per term. If a doc,
skill, help string, issue, or comment uses a term differently, it's wrong — fix
it to match this file.

The bottom section lists **banned terms** and what to say instead. Those words
accreted over many redesigns and now mean different things in different places;
they're being swept out.

---

## Core concepts

**release-core** — The single CLI through which all infrastructure runs:
`release-core <command>`. It is stack-agnostic — it knows a repo's Kind and routes
the generic verbs (`gate`, `test-unit`, `build`, `run`, `cut`, …) to the real
underlying tool (cargo, npm, uv, …). Installed at session start and auto-updated to
the pinned floating major, so every repo has the latest. `release-core --help` is
the map; `release-core how-to` is the task playbook.

**Kind** — A repo's primary type: `rust-cli`, `rust-lib`, `tauri-app`,
`electron-app`, `vscode-ext`, `nvim-plugin`, `go-cli`, `zed-extension`,
`tree-sitter`, `python-pkg`, `gh-action`. The Kind decides how the generic verbs
resolve and which release pipeline runs. Detect it with `release-core detect-kind`.
We either support a Kind or we don't — there is **no per-repo special-casing** (a
single Tauri app is as fully supported as ten Rust CLIs).

**Component** — A sub-stack *inside* a repo that isn't its primary Kind — e.g. an
mkdocs site alongside a Rust CLI. release makes a component's tooling generic
(`build`, `test`, …) just like a Kind's, so the harness and dev cycle stay uniform.
(Replaces the older term "Capability.")

**The gate** — The quality gate: `release-core gate`, a `lefthook`-driven run of the
fast lint / format / static checks. It is **one definition** (the `lefthook` config),
**invoked** everywhere it's needed (pre-commit hook locally; the same
`lefthook run pre-commit --all-files` as a required CI check) — never re-listed or
reimplemented per environment. It is a **hard** gate: a missing tool is a setup
failure, never a skip, and `--no-verify` is never acceptable.

**check-fast** — Vocabulary for the fast tier of quality checks: lint + format +
static analysis. This *is* what `release-core gate` runs. Pre-commit speed.

**check-full** — Vocabulary for the complete tier: check-fast **plus** the unit and
e2e test suites. This is what CI runs as its required checks (`gate`, then
`test-all`). A green gate is necessary but not sufficient for CI green.

> `gate` is the command; `check-fast` / `check-full` are just the names of the two
> tiers. There is no `release-core check-fast` command.

**The dev cycle** (development cycle) — The one standardized way of working, defined
in `docs/dev-cycle.lex` and enacted by the `gh-pr-review-loop` skill: branch →
change → changelog fragment → green gate → **draft** PR → review loop off
`release-core pr status` → guarded `release-core pr ready` flip → human merges. Called
the *development cycle*, never "workflow" (which means a GitHub workflow).

## Distribution

**Pull model** — How release reaches consumers. There is **no push**. Session start
installs OS deps and the `release-core` wheel pinned to the floating major; a bare
`release-core init` rebuilds the managed tree from the wheel. The wheel is the
carrier; consumers self-update. So we can always assume a consumer has the latest.

**Floating major** — The major-version branch a consumer pins (e.g. `@v3`). It
always points at the latest non-breaking release in that line. A cut auto-advances
it.

**Footprint** — What release commits into a consumer repo. Kept **minimal and
stable** so it never needs a coordinated push: the managed orientation block, one
skill, thin workflow files, and the bootstrap quartet. Everything else is ephemeral
(`.release/**`, gitignored, rebuilt every session). A *new* tracked file in a
consumer is a design smell.

**Managed orientation block** — The small (~7-line) block release maintains in a
consumer's `CLAUDE.md`, between `<!-- BEGIN/END release-managed orientation -->`
markers. It only points at `release-core how-to`; it carries no logic, so it never
drifts. (Not "one line," but minimal and stable.)

**Bootstrap quartet** — The four real tracked files a consumer needs before
`release-core` exists: `.claude/settings.json`, `bin/install-release-core`,
`bin/setup-dev-env.sh`, `bin/pr-loop-guard`.

**Distributed skill** — Exactly **one** skill is pushed to every consumer:
`gh-pr-review-loop`. Four others (`lex-primer`, `lex-multirepo`,
`electron-e2e-testing`, `macos-signing-notarization`) are *upgrade-only* — refreshed
only if the consumer already has them. (The "three skills" framing is obsolete; do
not repeat it.)

**Thin caller** — A consumer-side workflow file that only `uses:` a release reusable
workflow, with no embedded logic. (Use "thin caller," not "shim.")

**Reusable workflow** — A workflow defined in release's `.github/workflows/` that
consumers call via a thin caller. Holds the CI orchestration for a Kind.

## Fleet (release's internal side)

**Consumer** — A repo that uses release. **Fleet** — all consumers, listed in
`managed-repos.yaml` (the one source of truth; no discovery).

**Fleet ops** — release's own tools to probe / update / verify consumer state and
raise issues, all under `release-core admin …` (`repos`, `release`, `canary`,
`policy`, `secrets`, `inbox`, `contract`). Run from inside arthur-debert/release.

**Canary** — A pre-ship round that exercises a release against real consumer life
before cutting. `release-core admin canary run` posts a `canary/<family>` commit
status on the candidate HEAD; **`release-core cut` refuses without a green canary
status for every family on the exact sha being cut** (no skip flag).

**Upstream-first** — A consumer failure is a release bug until proven
consumer-specific. Fix it in release, cut a patch, let the pull model carry it;
don't patch around it in the consumer.

## The release pipeline

Releasing spans one local stage and four CI stages. **Everything that mutates state
runs in CI** (the one exception: pure Rust library crates may `cargo publish`
anywhere). Per-Kind variability is large — not every Kind builds, signs, or
publishes.

**Stage 0 — Pre-flight** (local, `release-core cut`). Detect Kind, read + bump the
version, validate it strictly, check the canary gate, then dispatch the release
workflow. Mutates nothing locally — no tag, no changelog roll.

**Stage 1 — Prepare** (CI). Validate the version, verify a changelog fragment
exists and the version differs from the manifest, bump the manifest file(s), roll
the changelog, then commit + tag + push.

**Stage 2 — Build** (CI, skipped for pure-source Kinds). Produce artifacts across
the platform/arch matrix (cargo / electron-builder / tauri / vsce / uv / …).

**Stage 3 — Sign & Notarize** (CI, conditional — macOS binaries only today:
rust-cli, go-cli, tauri-app, electron-app). Import the cert, codesign, submit to
Apple notarization, staple.

**Stage 4 — Publish** (CI, per-Kind, zero-or-more channels). crates.io, PyPI, npm,
VS Code Marketplace, Open VSX, the shared Homebrew tap, and/or a GitHub release.
Every Kind produces a GitHub release; `gh-action` also advances the floating major.

## Code layout (release's own repo)

**`bin/`** — **Not a consumer surface.** Holds only what genuinely can't be a
`release-core` subcommand: the in-checkout dev entry (`bin/release-core`), pre-boot
scripts that run before `release-core` exists (`install-release-core`), standalone
stdlib scripts fetched raw over HTTP (`fetch-deps`/`fetch-artifact`), and
fleet-operator tools (`orc`, `clone-lex-*`).

**`bin-internal/`** — CI-glue scripts that workflows exec. They handle *environment*
(secrets, matrix math, artifact bundling, caching) and **call `release-core`** — they
never reimplement it. If a script's body reduces to "set env, call release-core," the
capability belongs *in* `release-core` and CI should call it directly.

**`app-bin/`** (in a consumer) — Legitimate home for app-specific hooks and runnables
(npm post-build, vscode theme generation, a phos golden-image run) — work outside the
generic check/build/test/release verbs. It is **not** a place to duplicate, shim, or
fork release-core functionality.

> In a *consumer*, `scripts/*` and `bin/*` are smoking guns: `scripts/*` held
> pre-release project executables and `bin/*` held release's old per-script design —
> both were centralized into `release-core`.

---

## Banned terms → say this instead

These are being removed from docs, skills, help strings, inline comments, issues,
and memory. Each accreted multiple meanings across redesigns.

| Banned | What it tried to mean | Say instead |
|---|---|---|
| **materialize / materialized** | compose the `.release/` tree from the wheel | **build** / **set up** the `.release/` tree |
| **canonical** | the single shared implementation | drop the word — "the shared X" / "the one X" |
| **doctrine** | an operating rule or principle | **principle** / **rule** |
| **tombstone** | a retired file consumers should delete | **retired file** + "cleanup sweep" |
| **drift** | files diverging from their intended state | **out of sync** — and usually just drop it (the ephemeral tree can't drift) |
| **shim** | a thin caller, or a dead bin/ script | **thin caller** (workflows); the old bin/ usage is simply gone |
| **"invoke, don't discover"**, **"the binary is the carrier"** | internal slogans for the pull model | fine in internal notes; **never** in consumer-facing text |

Kept on purpose: **gate** (standard, defined above), **Kind**, **Component**,
**canary**, **floating major**, **pull model**, **footprint**.
