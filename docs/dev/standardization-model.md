# How release/ works — the standardization model

> Status: reviewed (2026-06-14) — the basis for rewriting `README.md` +
> `GLOSSARY.md` (Epic #655). Captures the *what* (the model) and the *how we
> verify it* (live-fire). The "common ground" section is distilled from the
> per-repo audit and goes stale as the fleet converges.

## Why this repo exists

To **standardize** development infrastructure and the agent harness across the
portfolio (~20 repos / ~13 projects). These projects grew organically and
re-solved the same needs — lint, test, changelog, build, release, publish —
each with its own bugs, interfaces, and limits. release/ removes that
divergence.

**The load-bearing claim: if it does not standardize, there is no point.** A
release/ that lets repos keep doing their own thing is just a complex middle
layer that adds cost while the divergence it was meant to kill survives. So the
default is: *standardize the capability upstream*, never *leave the consumer
hand-rolling it*. "This is expensive / risky to standardize correctly" is a
reason to do it carefully (canary per Kind), never a reason to leave it.

## What release/ provides (exactly four things)

1. **Local tools** — `release-core <command>`: a stack/component-agnostic CLI
   that routes generic verbs (`check`, `test`, `build`, `release`, …) to the
   real per-stack impl (cargo, node, …). Discoverable via `release-core how-to`
   and `--help`.
2. **CI tools/tasks** — reusable GH workflows for the same verbs + their trigger
   logic. Workflows are **thin orchestrators**: they handle *environment* (shell
   env, credentials, OS packages, caching, status, artifacts) and do real work
   only by **calling out to scripts or `release-core`**. No logic embedded in YAML.
3. **Agent harness** — a minimal, stable `CLAUDE.md` pointer (a few lines →
   `release-core how-to`) + the small stable skill set (the general ones like
   `grill-with-docs`; one dev-cycle skill that delegates to the tool).
4. **Dev cycle** — the standardized way of working
   (`docs/dev-cycle.lex`), enforced/informed by the tooling. (Called "dev cycle"
   to avoid colliding with "GH workflow".)

Plus, for release/ **itself**: tools to probe / update / test / verify consumer
state and raise issues.

## How it works (the pull model)

- **Session start** installs the right OS deps (pinned versions: lefthook,
  shellcheck, …) + `release-core`, and wires the pre-commit hook to
  `release-core`'s quality check.
- `release-core` **auto-updates to the major line the consumer pins** (`@vN` in
  its thin callers; `v3` is current). Consumers *pull* at session start / CI —
  there is no central push — so a consumer is current as of its last session.
- **Minimal injected footprint** — and only this:
  1. the few-line `CLAUDE.md` pointer (never changes — the how-to updates
     instead),
  2. the 2–3 skills (rarely change),
  3. thin workflow callers (rarely change — they only `uses:` the shared
     workflows).

  All logic and all changing information live in `release-core` + the shared
  workflows. Nothing repo-specific, no per-repo edits required.

## The quality check (lefthook)

One definition, run in two moments:

- **pre-commit** (local), and
- **CI** (server — because the local hook is skippable).

Categories: **lint / format / check** (enforce patterns) and **test**
(unit / e2e — ensure behavior).

## Release — the pipeline (stages; per-Kind coverage varies)

`Prep` (verify version, changelog presence, code change) → `Build` (artifacts,
any plat/arch matrix) → `Sign & Notarize` (macOS, VSCode, Windows…) →
`Publish` (crates.io, npm, VSX, App Store, brew, GH release).

## Kinds and Components

A project is **not always a single stack**. Many carry an mkdocs component (its
own python setup) alongside a rust core. release/ makes each stack/component's
tooling **generic** (`build`, `test`, … instead of `cargo test` / `npm run …`)
so the harness and dev cycle are uniform.

Supported Kinds: rust lib, rust cli, npm, mkdocs, Electron app, Tauri app,
VSCode extension, Nvim extension, Go server. **All Kinds use the same toolset.**
We either support a Kind or we don't — there is **no per-repo special-casing**.
(The recurring trap: phos-app is the only Tauri app, so agents keep proposing to
special-case / fold / break out Tauri. Wrong. We support Tauri → it is baked in
correctly, full stop.)

## The universal lifecycle (every software project has this)

Every project in the portfolio — rust, npm, mkdocs, electron, tauri, vscode,
nvim, go — has the **same** lifecycle. This is the litmus backbone:

1. **lint / format** — standards conformance.
2. **automated tests** (unit, e2e, gpu, whatever) — code quality.
3. **provision** — env / deps / toolchain.
4. **build** — *including docs*.
5. **release** — prep, sign/notarize, publish, **and cross-repo cascade**.

release/ helps at every step. Sometimes **vertically** (it provides the whole
thing) and sometimes via **hook points** (it provides the frame + interface and
the repo supplies a thin bespoke step). Both are fine; what's *not* fine is the
repo re-implementing the step itself.

**Cascade is ordinary, and we already own it.** lex-fmt, phos, and simple-gal
all cascade — one repo builds/registers something another pins as a dependency.
release/ *wrote* both the cascade trigger logic and the dep-pinning. So a
consumer's cross-repo release-notify / dep-bump is a standard capability, never
"too specific."

## The falsifiable test: ordinary vs bespoke

The question is **never** "is this file different." It is: **is the capability
ordinary?**

- **ORDINARY** → release/ MUST own it; a consumer hand-rolling it is a **bug**:
  everything in the lifecycle above — env setup, lint, format, check, unit
  test, e2e test, **suite selection (plat/arch/env)**, **result
  gathering/presentation**, changelog, version-verify, build (any matrix),
  **docs (mkdocs/jekyll/mdbook)**, **tarball/packaging**, sign, notarize,
  publish (any target), **cross-repo cascade + dep-pinning**.
  - The **interface** is standard even when the impl differs. Testing is always
    `env setup → select suites → call the test script → gather/present results`;
    that interface is shared, with a per-stack impl underneath (node, electron,
    cargo, …).
- **BESPOKE** → a thin `app-bin/` hook/runnable; legit only for **not-normal
  software-dev tasks** — generating VSCode themes from the shared marketing
  repo, benchmarking results against an open-source project's golden set. The
  *capability* "run a hook/runnable" is ordinary (release owns the interface —
  e.g. a future `release-core run`); only its *content* is bespoke. **This list
  is short on purpose.** Almost nothing qualifies.

## The fold-in rule (the one that ends the debates)

There are two genuinely different questions, and they get conflated constantly:

- *"Should release/ support some **new** capability it has never had?"* — a real
  design debate. Have it.
- *"We **already have** the working code / scripts / workflow for this in one of
  our own projects."* — **not** a debate. It is a **fold-in**: parametrize it
  and move it upstream. Full stop.

So "VSCode is uncommon" is irrelevant: we already have VSCode packaging working,
so it folds in and becomes a parametrizable Kind. The same goes for tauri,
electron, mkdocs, cascade — anything already running in a portfolio repo is, by
definition, a capability we own and must standardize, not special-case.

## We control both sides — so conform the consumer

Standardization isn't only "change the tooling to fit the consumers." **We own
both the tooling and the consumers**, so the adjustment runs both ways — and
usually the consumer is what moves. The only thing we may **not** scrap is a
repo's **core product functionality** (its actual source). Everything *around*
it — how it's set up, the scripts, the paths, the config files, the layout — is
ours to change so the repo matches the standard.

The mental model: a new SWE joining Google does **not** roll their own code
standards, their own linter config, or their own test runner — they adopt the
company-provided setup. A portfolio repo is the same. It does not get to keep an
idiosyncratic layout and force release/ to integrate with it; it adopts the
standard layout/paths/config, and release/ provides the setup. So "release can't
standardize this because the consumer is structured differently" is never a
stopping point — **restructure the consumer.**

## Smoking guns (every time, it's wrong)

- A consumer **`scripts/*`** → the pre-release graveyard; that functionality is
  already centralized.
- A consumer **`bin/*`** → release's old design; replaced by `release-core`.
- A **fat workflow** — embedded shell beyond env/creds/cache/status/artifact.
- `CLAUDE.md` beyond the stable pointer; skills beyond the 2–3; any workflow for
  ordinary checks/release that isn't a thin caller.
- `app-bin/` that **duplicates / wraps / forks** an ordinary capability (the
  legit use is bespoke hooks/runnables only).
- The **jargon** (per the GLOSSARY's banned table) — `canonical`, `materialize`,
  `tombstone`, `doctrine`, `drift`, `shim`. All go. (Prefer: *shared / the one*,
  *build / set up*, *retired file*, *out of sync*, *thin caller*.) `gate` is
  **kept** — it's defined in the GLOSSARY and is the `release-core gate` verb.

## Verification — proving standardization holds (live-fire, not synthetic)

The model above is the *what*. This is *how we know it works* in a real consumer.

Synthetic canary fixtures verify the mechanics deterministically (a good fast
pre-cut gate), but they cost effort to design and often don't exercise the
*minimal real* path an agent actually hits. The complementary check is
**live-fire**: a standard, repo-independent dev task that exercises the whole
loop authentically and leaves merged value behind.

**The standard task — coverage improvement.** Repo-independent, exercises the
full quality half, and is mergeable:

1. Check test coverage; find one module that is both *important* and *poorly
   tested*.
2. Improve its tests (and the code if needed).
3. Commit — the pre-commit quality check must run and give useful output.
4. Open a PR — CI runs the same checks + tests.
5. Drive it through the loop to ready/merge.

In one pass this exercises discovery (figuring out how to run things via
`release-core how-to` / `--help`), code, lint/format, the pre-commit hook, CI,
and the PR loop — and it ships real value, not a throwaway.

**The feedback ask is part of the prompt.** The standard prompt instructs the
agent to report *release feedback*: how it discovered how to do each thing, what
tripped it, anything missing / inaccurate / requiring a workaround. That feedback
is the self-improving loop — friction flows back to release as issues instead of
being lost in the consumer.

**Then the release half.** Cut a throwaway release — an rc / pre-release tag
(e.g. `v0.3.3-release-rc`) — to exercise the second hard path (prep → build →
sign/notarize → publish → cascade) without a real version bump. (Requires the
semver layers to support pre-release tags — verify that holds before relying
on it.)

**Operationalize it as the rollout / consumer check.** Keep ONE standard prompt
(coverage task + release-rc). A consumer check = firing it at a consumer; a
fleet rollout check = firing it at N consumers **in parallel**. One prompt →
broad authentic verification + a feedback harvest, repo-independent.

This sits alongside the synthetic canary (deterministic mechanics gate before a
cut) and `release-core admin repos poke` (fresh-event check): canary proves the
build is sound; live-fire proves an *agent* can actually live the loop and
surfaces what is still rough.

## Common ground / specificity tradeoff

Distilled from the 17-repo audit (full detail + per-repo scorecard:
[`fleet-standardization-audit-2026-06-14.md`](./fleet-standardization-audit-2026-06-14.md)).

**The common ground (ordinary → standardize upstream), by payoff:**

1. **Stale `@v3` re-seed across the fleet** — almost every consumer is pinned
   `@v2`/`@v1` on the `v2.21.0` wheel with retired `release-sync` / `ORIENTATION`
   / `canonical` / `take-iii` jargon baked into managed stubs. One fleet action
   refreshes markers, lefthook shape, and `bin/check` residue for ~14 repos.
   **lex + vscode are still on the retired tracked-`.release/` seed model** (need
   an explicit one-time re-seed, not a pin bump); **comms isn't onboarded at all**
   (broken SessionStart).
2. **rust→wasm test+build** — a `wasm` input on `rust-ci` (lex, phos-core
   hand-roll it today).
3. **Generic "extra release asset" hook** — attach an `app-bin/*` generator's
   output to the cut (phos-core corpus/fixtures, tree-sitter-lex).
4. **Ordinary checks as shared inputs** — `lua-lint`, `cargo-doc`,
   docs-spellcheck (nvim, lex, standout hand-roll these).
5. **New Kinds release/ doesn't ship yet** — a Jekyll docs-site Kind (comms,
   lex), a go-server / Cloud-Run Kind (supage), a standalone cascade fan-out
   callable (comms hand-rolls tar+gh-release+dispatch).

**Pure deletes (zero upstream work):** dead-orphan brew-render pairs (5 repos),
`rust-setup/action.yml` (2), vscode's husky gate, `bin/check-fmt`/`check-lint`
(lex, vscode), infra-duplicate skills (lex ~21→3, vscode 18→3).

**Genuine specificity (legit app-bin — content bespoke, hook ordinary):** theme
generation from the shared marketing/comms source (lexed, vscode, nvim, zed),
phos parity/golden-image guards + OCIO golden gen, tree-sitter grammar-lifecycle,
lex's seccomp/landlock sandbox content, supage's Firestore-emulator tests. The
list is short on purpose; everything else is a fold-in.

**The load-bearing risk:** managed workflow copies overwrite live consumer CI
with **no conflict guard**, and only **rust** + **vscode-ext** have authored
canary families — electron/tauri/nvim/tree-sitter/zed/go ship blind. Author
canary seeds for those Kinds *before* standardizing their workflows.
