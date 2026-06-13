# release/

Reusable GitHub Actions workflows + composite actions for releasing
software across the arthur-debert / lex-fmt ecosystems. One shared
pipeline per Kind; consumers call them with a thin `with:` block.

`README.md` has the consumer-facing overview and the four pillars;
[`GLOSSARY.md`](GLOSSARY.md) is the authoritative vocabulary — terms used
here are defined there. This file captures the principles and non-obvious
constraints that apply when *working on this repo itself*.

## Working the fleet / consumer repos — start here

Any task that touches consumers — shipping a fix to the fleet (cut + advance;
pull carries it), migrating a consumer, advancing the floating major, or
diagnosing why a consumer's CI/gate is red — is governed by the
**`release-fleet-ops` skill**. Invoke it first. It encodes the one rule
(*upstream-first*: a consumer failure is a `release/` bug until proven
consumer-specific), the reproduce-once → consult-the-dogfood-oracle → route
loop, and the traps (`lefthook-local.yml` shadows; a consumer `.gitignore`
silently dropping managed `bin/` tools).

- **What's open / next:** the GitHub issue tracker is the task list — epic
  **#348** (self-improving loop: consumer orientation is shipped + propagated to
  all consumers; Phase A3 skill-reach + Phases C/D remain), **#349 / #350**
  (`orc watch` shake-out + cloud transport). The roadmap in `docs/tooling.lex`
  is the *directional* arc (the why), explicitly not a task tracker.
- **The CLI is the interface:** all infra/fleet tasks go through the
  `release-core <group> <command>` tree — `release-core --help` is the map,
  `release-core admin --help` is the fleet/meta-release subtree. The flat
  maintainer command names (`release-verify-fleet`, `managed-repos`,
  `release-cut`, …) were RETIRED in the CLI cutover (#468) — they no longer
  exist on PATH; use `release-core <group> <command>` exclusively. The only
  flat names left are consumer-facing aliases (`changelog`, `semver`,
  `detect-kind`). `release-sync` / `release-drift-check` were REMOVED in WS4
  (#521) — `.release/` is now gitignored + composed by `release-core init`, so
  the drift/sync subsystem was retired. The redundant
  `gh-task-status`, `gh-release-issue`, and `release` consumer shims were
  retired in #476 — they reach a consumer's PATH as pip console-scripts
  (`gh-task-status`, `gh-release-issue`) / `release-core cut`, not synced
  `bin/` shims.
- **Core fleet loop (PULL only — `orc propagate` was REMOVED):**
  `release-core admin repos verify` (pre-flight on the candidate main) →
  `release-core admin canary run --ref main` (the pre-ship consumer-life
  round; posts a `canary/<family>` commit status on the candidate sha) → cut a
  release (`release-core cut` **REFUSES without a green `canary/<family>`
  status for every registered family on the exact main HEAD being cut** — no
  skip flag, #606; exact-sha binding means any new commit on main invalidates
  the previous round, so re-run the canary after any push. `release.yml`
  publishes the `release_core` wheel **and
  auto-advances the floating major** — it passes `advance-major: true`, so
  there is NO separate advance step and verify + canary must run BEFORE the
  cut) → fresh-event check on a consumer (`release-core admin repos poke
  <owner/name> --watch` — empty commit to its main + classified verdict, never
  `gh run rerun`; #595). That's it — consumers
  self-update at SessionStart: `install-release-core` pulls the wheel and a bare
  `release-core init` (the DEFAULT full build) re-syncs the whole managed
  tree from the wheel bundle and auto-commits any change. There is **no push
  mechanism** — the wheel is the carrier; pull does the rest. A consumer still on
  a pre-pull seed migrates by running the (fixed) resolver once in that repo and
  opening the resulting managed-sync PR — a one-time seed, not a fleet push.
- **The load-bearing gotcha:** mechanical fleet tools (`release-core admin repos
  verify`) run in clones *without* the consumer's toolchain (no `npm install` /
  `cargo`), so they cannot run the consumer gate faithfully — a
  `release-core admin repos verify --all-files` FAIL on an npm/frontend repo is
  usually a missing-deps artifact, not real debt; the consumer's own PR CI is the
  real gate. Review
  threads on synced `.release/**` are upstream concerns, not consumer-PR blockers.

## Repo shape

- `.github/workflows/rust-cli.yml`, `copilot-review.yml` — reusable workflows
- `.github/actions/<name>/` — composite actions, atomic units shared across (future) workflows
- `bin/` — **not a consumer surface** (the wheel + bootstrap quartet carry everything consumers need). It holds only what genuinely can't be a `release-core` subcommand: the in-checkout dev entry (`bin/release-core`), pre-boot scripts that run before `release_core` exists (`install-release-core`), standalone stdlib fetchers pulled raw over HTTP (`fetch-deps`/`fetch-artifact`), and fleet-operator tools (`orc`, `clone-lex-*`). On release's own `$PATH` via dodot. **The maintainer surface listed below is the `release-core` CLI, NOT `bin/` scripts** — mapped here so you know where each capability lives:
  - Policy/setup (now `release-core admin policy|secrets …`; the `←` names are the RETIRED flat names, gone from PATH after #468): `release-core admin policy ruleset` (← `apply-ruleset`), `… policy sweep` (← `sweep-github-policy`), `… policy dependabot` (← `enable-dependabot-security`); `release-core admin secrets install|token` (← `install-release-{secrets,token}`); plus the kept flat alias `detect-kind`
  - Pull-model boot: `install-release-core` (THE boot resolver, ADR-0003 — resolves the `release_core` wheel from a GitHub release and installs it into its OWN dedicated, isolated venv (`${XDG_DATA_HOME}/release-core/venv`, `--force-reinstall`, deps from PyPI — never the user pip / system site / a project venv), symlinks the console-scripts onto PATH (`~/.local/bin`) + persists `$GITHUB_PATH` under Actions, then runs a bare `release-core init` in the current repo (`--no-init` to skip; init is best-effort). Install from a local checkout instead with `--from-source PATH` (release's own CI). Post-#476, a bare `init` is the DEFAULT FULL build — it builds the whole managed tree from the wheel bundle (the `.release/` build dir + every working-tree mirror: skills, configs, the CLAUDE.md stub block — ORIENTATION.md retired in WS2 #523) and auto-commits managed changes (no `[skip ci]` — that would block a managed-only migration PR under required checks), so the wheel pull carries the full tree and no push is needed. The `--config-only` escape hatch and its `--full` alias were REMOVED in #532 (the full build is the only mode; `--commit`/`--force` remain tolerated no-ops for stale resolvers). `--major vN` pins to the latest release in that major line, default `releases/latest` — the v3-safety filter since the wheel version is static. One command does the whole boot — NOT a pip post-install hook (wheels have none; init is repo-specific). Pure shell, runs before `release_core` is installed. Lives in the SYNCED bootstrap (`templates/commons/bin/install-release-core`, symlinked from repo-root `bin/`) so it reaches consumers — `setup-dev-env.sh` §0.2 invokes it by repo path at SessionStart (local+cloud); CI reaches it via `@vN` action_path. Tests: `tests/install-release-core/`)
  - Release mechanics: `release-core admin release advance-major` (← retired flat `release-advance-major`) — manual/recovery fast-forward of the floating major branch (auto-detected highest `vN`, currently `v3`) to main. Normally NOT part of the loop: `release.yml` auto-advances at cut (`advance-major: true`); reach for the verb only when that job failed or for an out-of-band advance
  - Managed-tree compose: `release-core init` builds the gitignored, ephemeral `.release/` build dir + the committed mirrors (symlinks, real-file workflow copies, CLAUDE.md block) from the pinned wheel. WS4 (#521) made `.release/` gitignored + rebuilt every session/CI, so the build dir can't fall out of sync — the standalone `release-sync` builder and the `release-drift-check` check (+ the `sync` CLI group) were RETIRED. The compose ENGINE lives in `release_core/sync.py` (the `build_plan`/`materialize`/`compute_mirror` functions — code identifiers, left as-is); `release-core init` is its only driver. The `.release/.release-sync-source` provenance marker (ADR-0002) is still written but transient + informational (no reader). CI builds `.release/` via the `arm-gate` composite's `materialize` input (default on) before the gate.
  - Fleet (now `release-core admin repos|inbox …`; the `←` names are the RETIRED flat names, gone from PATH after #468): `release-core admin repos list` (← `managed-repos`) — zero-logic accessor over `managed-repos.yaml`, the ONLY fleet source of truth, no discovery; resolves each repo to `$REPOS_ROOT/<path>`; `release-core admin repos verify` (← `release-verify-fleet`) — hermetic pre-flight lint sweep: clone fleet → `release-core init --no-commit` from a candidate ref → `lefthook run pre-commit --all-files`; run before `release-core cut` (the cut auto-advances `@vN`, so there is no later checkpoint); `release-core admin inbox` (← `release-inbox`) — read-only triage view over `consumer-filed` issues on this repo — the #348 feedback-loop inbox; groups by `[component]`, sorts clusters by recurrence/comment-count, `--json` for the Phase C batch run; `release-core admin inbox notify-source` (← `release-notify-source`) — close-the-loop: reads a consumer-filed issue, comments the "upstream fix shipped — bump `@vN`, re-run" notice on each source PR it points at; dry-run by default, `--post` to send, `--close` to also close the release issue. The `release-fleet-triage` skill orchestrates inbox → fix loop → notify-source. See `docs/dev/fleet-tooling.md`.
  - PR loop: `release-core pr status|wait|ready` (the state machine: one lifecycle state + next action; `pr wait` is the ONE engine-driven in-turn wait — it replaced `pr review wait` + `pr checks-wait` per #503; `pr ready` is the guarded draft→ready flip per #456), `release-core pr review request|cancel|show [--reviewer <name>]` (reviewer-agnostic, dispatches through the adapter registry in `prstate/reviewers.py` — the RETIRED `pr copilot on|off|wait|review` group and the `gh-copilot-*` bin scripts are gone, no aliases, per #555), plus `release-core pr resolve-thread` (bin shim: `gh-pr-resolve-thread`) (the `gh-release-issue` consumer escalation tool is now a pip console-script, retired as a `bin/` shim in #476)
- `bin-internal/` — CI-glue scripts that composite actions and reusable workflows exec inside GitHub Actions runners (not on `$PATH`, never called locally). They handle *environment* and **call `release-core`; they never reimplement it.** A script whose body reduces to "set env, call release-core" is a missing command feature — collapse it. Genuine CI plumbing only (matrix math, artifact bundling, secret validation, pre-bootstrap fetch).
- `templates/` — render templates (e.g. Homebrew formula)
- `tests/` — per-tool BATS suites (one dir per `bin/` / `bin-internal/` tool, run by the matching `.github/workflows/*-tests.yml`). The ONE fixture tree is `tests/fixtures/<kind>/` — the AUTHORED canary seeds (#604): each dir carries a `.canary-family` marker naming its family (rust-cli → `rust`, vscode-ext → `vscode-ext`; optionally a `.canary-secrets` marker declaring the extra secrets `canary init` converges, e.g. rust's cert-only Apple signing pair per #587 OQ3) and holds exactly the consumer-authored content of that family's canary repo (project tree + thin callers; never the init-generated bootstrap quartet / copilot-review.yml / CLAUDE.md). `release-core admin canary init` seeds the canary FROM it, so fixture and canary cannot diverge. Everything else still fabricates synthetic consumers inline (`tests/release-sync/helper.bash` temp repos, the throwaway fixture repos in `pip-bootstrap-smoke.yml`) matching `docs/references/consumer-contract.yaml` (epic #583 WS-B).
- `docs/` — the four narrative .lex docs + ADRs + references
- `examples/` — paste-ready consumer release.yml files

### `bin/` is on $PATH via dodot

`~/h/dotfiles/release/profile.zsh` exports `~/h/release/bin` directly onto `$PATH`, sourced at login by dodot's shell handler. **Drop a new executable into `bin/`, mark it `+x`, and it's reachable by name in any new shell** — no per-script symlink mirror needed.

Earlier attempts that *don't* work and shouldn't be reintroduced:
- `dotfiles/release/bin -> ~/h/release/bin` (a top-level symlink): dodot routes top-level symlinks through the **symlink** handler, not the **path** handler, so the dir never lands on `$PATH`.
- A real `dotfiles/release/bin/` of per-script symlinks: works (path handler kicks in) but creates a maintenance tax — every new script needs a matching symlink. The `profile.zsh` approach is strictly cleaner.

## Versioning contract — do not break

Consumers pin `@v3` (floating major) or `@v3.1.2` (exact). The `v3`
branch always points at the latest non-breaking tag. (v3.0.0 was cut
2026-06-13; v2 is frozen at v2.21.0 and the `@v2`→`@v3` consumer migration
is the open follow-on.)

| Bump | Trigger |
|---|---|
| PATCH | bug fix in any composite action, no input changes |
| MINOR | new optional input, opt-in feature, new Kind workflow |
| MAJOR | required-input rename, default behavior change, removed input |

Anything that forces every consumer to edit their thin caller is a
MAJOR — coordinate the consumer PRs before cutting.

## Design principles

### Releases run in CI, never locally
Runnables (CLIs, GUI apps, extensions) build/sign/publish from GitHub
Actions. The user does not always have a fully provisioned dev machine
and may travel for weeks; "must release locally" can turn a security
fix into a multi-week stall. Pure Rust **library crates** are the one
exception (`cargo publish` from anywhere is fine). Don't suggest
"just publish locally" as a CI workaround — fix the CI.

### CI workflows: caching + artifact splits
1. **Caching is mandatory.** Without it a 2-minute Rust job balloons
   to 10. Use `Swatinem/rust-cache` rather than rolling your own.
2. **Split jobs; pass binaries via artifacts.** Build once in the
   unit job, upload, downstream jobs download. Concrete win: dodot
   E2E went 9m → 50s. Multi-platform release builds are the only
   exception (must build per target).

### E2E reined in; BATS for shell CLI
- Unit tests guard quality (breadth + depth + invariants). E2E
  verifies user perspective and glue. Don't substitute one for the
  other.
- Standard shell E2E framework: **BATS** (`bats-core`). Each `@test`
  runs in a fresh subshell — matches the shell-subshell isolation
  pattern used in padz/dodot.
- Docker is for testing the *delivery mechanism* (`apt install
  ./pkg.deb`, `brew install <tap>/<formula>`), not binary behavior
  — for binary behavior the GH runner is already clean-enough.

### Distribution channels
- Library crates → cargo only.
- CLI binaries → always cargo install; ideally also brew (macOS)
  and apt (linux).
- Use the **shared brew tap** (`arthur-debert/homebrew-tools`), not
  a per-project tap. padz is the reference impl.
- GUI apps (Electron) → GH release artifacts only for now. Don't
  propose homebrew-core, mas, choco, snap unless asked.

### Cross-org consumers need explicit secrets
`secrets: inherit` only propagates within the same owner. lex-fmt
consumers must list every secret explicitly under `secrets:` in
their `uses:` block and may need name mapping (lex-fmt uses
`CARGO_REGISTRY_TOKEN`; this action's input is `CRATES_IO_KEY`).
arthur-debert/* consumers can use `inherit`. Caught empirically in
lex-fmt/lex v0.9.1.

### Docs layout
- `docs/references/` for "why" / design.
- `docs/dev/` for tool-base work.
- `docs/users/` for end users.
- mdBook only for large, public-facing doc sets.

## Operational rules

- **Every PR is driven through the `gh-pr-review-loop` skill — invoke it before
  `gh pr create`, not after.** The skill is the *discipline* (loop
  `release-core pr status` → do the one next action → re-read; wait in-turn via
  `release-core pr wait`, never a background monitor; triage A/B/C → resolve
  threads as you go → flip via the guarded `release-core pr ready`, never raw
  `gh pr ready` → stop at ready); the `release-core pr …` helpers are each
  independently reachable, so hand-composing them silently skips the
  disciplined steps while still producing a green-looking PR. This is now *enforced*: a PreToolUse guard
  (`bin/pr-loop-guard`, wired in `.claude/settings.json`, synced to consumers via
  `templates/commons/`) blocks a bare `gh pr create` unless the loop is armed
  (the skill arms a one-shot `pr-loop-armed` sentinel in `.git/`). If you are
  already in the loop and the guard blocks you, arm it and retry:
  `touch "$(git rev-parse --git-dir)/pr-loop-armed"`. Per release#495 (epic
  #348) — the forcing function exists because optional discipline loses to task
  momentum (caught when an agent hand-rolled #494's loop).
- **The gate is ONE definition, run everywhere — never reimplemented.**
  `lefthook.yml` IS the gate (the WHAT: the set of checks). Every environment
  (the WHERE) *invokes* it: session start arms it (`setup-dev-env.sh` /
  `release-core init` installs the toolset + wires the hook), local commits
  run it, and CI runs the SAME `lefthook run pre-commit --all-files` as a
  required check. **WS3 (#524):** in a *consumer* the gate definition + most tool
  configs live only in the ephemeral `.release/` (no tracked root `lefthook.yml`);
  the wheel carries it — `release-core gate` points lefthook at
  `.release/lefthook.yml` and the git hook is `release-core gate --install-hook` →
  `release-core gate --hook`. (`.editorconfig` is the one root mirror kept —
  editors discover it; `.shellcheckrc` moved gate-internal in #531 F3: the
  toolset ships shellcheck ≥ 0.10 via the shellcheck-py wheel, so the gate
  passes `--rcfile .release/.shellcheckrc`.) (Release's OWN repo keeps a hand-authored root
  `lefthook.yml` with release-only checks — it is the source, not a consumer.) It is a HARD gate — a missing tool exits non-zero (never
  skips), and `--no-verify` is never an acceptable workaround (CI re-runs the
  gate on a clean runner where the tools are guaranteed). **To add or change a
  check, edit `lefthook.yml` only; never hand-copy a check into a CI job.** "CI
  is the source of truth" is the bug, not the design: CI is a *place* the gate
  runs, not a second definition of it. If you're writing the check-list a second
  time, stop — invoke, don't reimplement.
- **Lint debt → the three-case model** (`docs/references/lint-debt-model.md`).
  Before "fixing a lint error," classify the file. **Don't want it linted?
  gitignore it** — the gate only runs on tracked files, so gitignored
  third-party/vendor/build content is never linted or tested (that's the point
  of gitignore). The linter therefore only ever sees files we own, so the only
  responses are *fix*: authored → fix the file once; tool-generated → fix the
  generator (never hand-fix or ignore). The managed `.markdownlintignore` is
  ONLY for committed-but-not-authored conventions (generated `CHANGELOG` /
  `UNRELEASED`, mdbook `SUMMARY`, fixtures) — not a per-repo escape hatch.
  Don't whack-a-mole per file: run `lefthook run pre-commit --all-files` and
  categorize the whole repo once. Release-owned file failing the gate = release
  bug; consumer-owned = fix there.
- **Every feature/fix PR adds a changelog fragment — in the same PR.** Run
  `changelog add <slug> "<one-line summary>"` (writes
  `CHANGELOG/unreleased-<slug>.md`); never hand-edit `CHANGELOG.md` (it is
  rendered at release-cut, not in feature PRs). The release **refuses to cut
  without a fragment** (the prepare gate fails on "No CHANGELOG/unreleased-\*.md
  fragments found"), so a fragment-less merge silently blocks the next release
  until someone backfills it. This applies to PRs against this repo itself, not
  just consumers — the consumer-facing version of this rule is rendered by
  `release-core how-to` (ORIENTATION.md was retired in WS2 #523).
- **Bug fixes go here, not in consumers.** A bug surfaced by a
  consumer is fixed here, tagged as a PATCH, and the `v1` branch
  advanced. Consumers re-run; nothing for them to edit.
- **`bin-internal/ci-publish-crate.sh` is load-bearing.** It tolerates
  the case where a re-published older RC version exists per the
  crates.io JSON API but `cargo publish` errors. Don't replace
  with a `cargo search VERSION | grep` fallback — that's the
  pattern this script was written to fix.
- **Secret propagation:** `release-core admin secrets install` propagates the
  release secret set to onboarded repos; `release-core admin secrets token`
  installs just the release token. If the set of required secrets changes,
  update those verbs (the old `bin/install-release-{secrets,token}` flat names
  were retired in #468).
- **Authoring a skill? Lint it.** Skills under `skills/` split by
  provenance: vendored/third-party ones carry an `.upstream` marker
  (source repo + pinned commit) and are exempt; our own self-authored
  skills have no marker and MUST pass markdownlint. After adding or
  editing a self-authored skill, run `bin-internal/lint-skills.sh`
  (the lefthook pre-commit + the CI `skill-lint` job enforce it). When
  vendoring a new skill, drop an `.upstream` marker in its dir so the
  gate skips it. Per release#321.
- Full design rationale, bug log, and open follow-ups live in
  `~/h/repo-all/tooling.lex` §13.
