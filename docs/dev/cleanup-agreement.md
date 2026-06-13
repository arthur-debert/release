# release/ — cleanup working agreement (WORKING DRAFT)

> Transient working doc for the terminology + docs cleanup effort. It consolidates
> what we've agreed so the model stops scattering. Sections here get **promoted**
> into README / GLOSSARY.md / CLAUDE.md / the .lex docs, then this file is deleted.
> Not canon — the promoted docs are.

## 1. What release/ is (the agreed model)

release/ gives ~20 repos across ~13 projects **one** set of software-development
infrastructure, so standardization can actually be baked in. It does four things,
plus an internal fifth:

1. **Local tools** — lint / format / static checks, tests, changelog, build, run,
   release — all through one `release-core <command>` CLI.
2. **CI tools & tasks** — reusable GitHub workflows that run the same tools on the
   server, plus the logic that triggers them.
3. **Agent harness** — the CLAUDE.md pointer, the distributed skill(s), and
   `release-core how-to`: how an agent orients and asks for help in any repo.
4. **Development Cycle** — the one standardized way of working
   (`docs/dev-cycle.lex`). Called "development cycle," never "workflow" (reserved
   for GH workflows).
5. **(internal) Fleet ops** — release's own tools to probe / update / verify
   consumer state and raise issues (`release-core admin …`).

**The release pipeline (grounded against the code).** Your 4-stage model is accurate
at the CI level but needed a Stage 0 and a per-Kind caveat. Everything that *mutates
state* runs in CI (only pure Rust lib crates may `cargo publish` locally):

- **Stage 0 — Pre-flight** (local, `release-core cut`): detect Kind, bump + validate
  version, check the canary gate, dispatch the workflow. **Mutates nothing locally.**
- **Stage 1 — Prepare** (CI): validate version, verify changelog fragment + version
  differs, bump manifest(s), roll changelog, commit + tag + push.
- **Stage 2 — Build** (CI; skipped for pure-source Kinds): artifacts across the matrix.
- **Stage 3 — Sign & Notarize** (CI; macOS binaries only today: rust-cli, go-cli,
  tauri-app, electron-app).
- **Stage 4 — Publish** (CI; per-Kind, 0–N channels): crates.io / PyPI / npm /
  VS Code Marketplace / Open VSX / Homebrew tap / GitHub release. Every Kind makes a
  GH release; `gh-action` also advances the floating major.

Not every Kind hits every stage — see the per-Kind table the grounding produced
(rust-lib doesn't build/sign; nvim-plugin only tags; vscode-ext doesn't sign; etc.).

**How it reaches consumers (pull model):** session-start installs OS deps +
the `release-core` wheel, pinned to the floating major (e.g. `@v3`), and
auto-updates. So we can assume every consumer has the latest. There is **no push**
— the wheel is the carrier. The logic + the changing information live in
`release-core`; consumers carry a near-zero, near-static footprint.

**The consumer footprint (minimal + stable, so it never falls out of sync):**

- a small managed CLAUDE.md block (~7 lines, between
  `<!-- BEGIN/END release-managed orientation -->` markers) that points at
  `release-core how-to` — **not "one line," but minimal and stable.**
- exactly **one** unconditionally-distributed skill: `gh-pr-review-loop`. Four
  others (`lex-primer`, `lex-multirepo`, `electron-e2e-testing`,
  `macos-signing-notarization`) are **upgrade-only** (synced only if already
  present). The "3 skills" framing is **obsolete** — do not repeat it.
- thin workflow files that just `uses:` release's reusable workflows.
- the bootstrap quartet (`.claude/settings.json`, `install-release-core`,
  `setup-dev-env.sh`, `pr-loop-guard`).
- everything else (`.release/**`) is gitignored + recomposed every session.

## 2. Terminology decisions

| Term | Decision |
|---|---|
| **gate** | **KEEP.** Standard CI vocabulary; the `release-core gate` command stays. Define it in the glossary; don't crusade against it. |
| **check tiers** | **Describe-only (decided).** `gate` stays the command — no rename, no code change. `check-fast` and `check-full` are the **names of the two tiers** in docs: **check-fast** = lint/format/static = what `release-core gate` runs (pre-commit); **check-full** = check-fast + unit + e2e = what CI runs = today's `gate` then `test-all`. They are vocabulary, not new commands. |
| **Kind + Component** | **Kind** = a repo's primary type (rust-cli, tauri-app, …). **Component** = a sub-stack inside it (e.g. mkdocs alongside Rust). Retire "Capability" → "Component". |
| **Development Cycle** | The house term for the standardized way of working. Not "workflow." |

**Banned terminology → replacement** (sweep these out of docs/skills/help/inline/issues/memory):

| Banned | Means | Replace with |
|---|---|---|
| materialize / materialized | compose the `.release/` tree from the wheel | **build / set up** the `.release/` tree |
| canonical | the single shared implementation | drop the word — say "the shared X" / "the one X" |
| doctrine | operating principles/rules | **principle / rule** |
| tombstone | a retired file consumers should delete | **retired file** + "cleanup sweep" |
| drift | files diverging from intended state | **out of sync** — and mostly *retire it* (now impossible by construction) |
| shim | (good) thin workflow caller; (dead) old bin/ scripts | keep concept as **thin caller**; the dead bin/ usage just goes |
| "invoke, don't discover" / "the binary is the carrier" | internal slogans | fine internally; **never leak to consumer surfaces** |
| `release-core status` help: "pilot-running gate (done-check)" | release-posture done check | rewrite in plain words |

## 3. Principles (to encode)

1. **We support a Kind or we don't — no special-casing.** Tauri is supported, so
   it's baked in correctly. No folding, no breaking it out, no per-repo
   exceptions. (Recurs on phos-app; stop relitigating it.)
2. **No code in CI workflows.** Workflow files handle *environment* (shell env,
   credentials, OS packages, caching, status, artifacts) and call programs in
   source control. No embedded shell logic — it can't be run/tested locally and
   it forks a YAML copy of functionality the tool already owns.
3. **`bin/` is not a consumer surface.** It holds only what genuinely can't be a
   `release-core` subcommand: the in-checkout dev entry (`bin/release-core`),
   pre-boot scripts (`install-release-core`, run before release_core exists),
   standalone HTTP-fetched stdlib scripts (`fetch-deps`/`fetch-artifact`), and
   fleet-operator tools (`orc`, `clone-lex-*`).
4. **`bin-internal/` CALLS `release-core`; it never reimplements it.** Litmus: if a
   script's body reduces to "set env, call release-core," the capability (or its
   env-handling) belongs *in* `release-core` and CI calls it directly. A
   forwarding-only script is a fossil of the old bin/-script mindset.
5. **"No code in YAML" is satisfied by calling `release-core` directly** — the
   binary IS the locally-runnable, testable artifact. Don't add a wrapper to honor
   the principle.
6. **`scripts/*` and `bin/*` in a *consumer* are smoking guns** (pre-release
   project executables / old release design — both centralized into release-core).
   **`app-bin/` is legitimate** for app-specific hooks / runnables (npm post-build,
   vscode theme generation, phos golden image) — but not for anything that
   duplicates, wraps, or forks release-core functionality.

## 4. Execution checklist (not started)

- [x] **Duplication suspects verified** — far less than feared. `fetch-deps`/
      `fetch-artifact`/`gh-pr-resolve-thread` are `wrap_script()` passthroughs (NOT
      duplication — keep). Only `bin/detect-kind` is a clean delete. Contract wrappers
      keep+document (working-tree + stdlib-only variant). `run-precommit-gate.sh` husky
      detection = possible pre-WS3 legacy → careful code PR. Details in memory
      `project_bin_vs_bin_internal_rule`.
- [ ] **Code PR (separate, behavior-touching):** delete `bin/detect-kind` (+ update
      `release-sync-tests.yml`); add the "why working-tree/stdlib" comment to the two
      contract wrappers; investigate `run-precommit-gate.sh` husky-detection legacy.
- [x] **Workflow embedded-shell audit DONE** — 38/40 thin; only 2 defensible edge
      cases (`cascade-handler.yml` version-decision ~90 lines; `go-cli.yml` build
      matrix ~52 lines). Principle #2 well-honored; both low-priority, optional.
- [~] **Terminology sweep** — CLAUDE.md done (me); README + GLOSSARY done; MEMORY.md
      header done; docs/skills/help-strings in progress (sweep agent); full memory
      sweep + issues still to do.
- [~] **Kill the "3 skills" myth** — in the sweep agent's scope.
- [x] **GLOSSARY.md written** (repo root) — terminology + release pipeline + banned terms.
- [x] **Release-pipeline model grounded** against `release-core cut` + the per-Kind workflows.
- [x] **README rewritten** against GLOSSARY.md (4-pillar framing, pipeline table, banned terms purged, `@v3`).
- [ ] **CLAUDE.md core block** → reconcile the .lex docs (against GLOSSARY.md).
- [ ] **Revisit the cleanup issues** (#348 epic, #569 legacy inventory, #349/#350) and sharpen them against this model.

## 5. Open questions

- *(resolved 2026-06-13)* **gate vs check-fast/check-full naming** → describe-only:
  `gate` stays the command; check-fast/check-full are doc vocabulary for the two
  tiers (check-fast = `gate`; check-full = `gate` + `test-all`). No command rename.

_None open. Next: keep mapping the rest of the model, then promote §1–§3 into
README / GLOSSARY.md / CLAUDE.md._
