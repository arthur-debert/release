# release/

Reusable GitHub Actions workflows + composite actions for releasing
software across the arthur-debert / lex-fmt ecosystems. One canonical
pipeline per artifact category; consumers call them with a thin `with:`
block.

`README.md` has the consumer-facing overview, category table, and
versioning policy. This file captures the principles and non-obvious
constraints that apply when _working on this repo itself_.

## Working the fleet / consumer repos — start here

Any task that touches consumers — re-syncing, propagating a fix, advancing the
floating major, or diagnosing why a consumer's CI/gate is red — is governed by
the **`release-fleet-ops` skill**. Invoke it first. It encodes the one rule
(_upstream-first_: a consumer failure is a `release/` bug until proven
consumer-specific), the reproduce-once → consult-the-dogfood-oracle → route
loop, and the traps (`lefthook-local.yml` shadows; a consumer `.gitignore`
silently dropping managed `bin/` tools).

- **What's open / next:** the GitHub issue tracker is the task list — epic
  **#348** (self-improving loop: consumer orientation is shipped + propagated to
  all consumers; Phase A3 skill-reach + Phases C/D remain), **#349 / #350**
  (`orc watch` shake-out + cloud transport). `docs/status.lex` is the
  _directional_ roadmap (the why), explicitly not a task tracker.
- **Core fleet loop:** `release-verify-fleet` (hermetic pre-flight sweep) →
  `orc propagate` (re-sync + open a PR per consumer) → `release-advance-major`
  (fast-forward the floating major). Always run `release-verify-fleet` before advancing.
- **The load-bearing gotcha:** mechanical fleet tools run in clones _without_ the
  consumer's toolchain (no `npm install` / `cargo`), so they cannot run the
  consumer gate faithfully — `propagate` commits `--no-verify` and the PR's CI is
  the real gate; a `release-verify-fleet --all-files` FAIL on an npm/frontend repo is
  usually a missing-deps artifact, not real debt. Review threads on synced
  `.release/**` are upstream concerns, not consumer-PR blockers.

## Repo shape

- `.github/workflows/rust-cli.yml`, `copilot-review.yml` — reusable workflows
- `.github/actions/<name>/` — composite actions, atomic units shared across (future) workflows
- `bin/` — local CLI tools that mutate consumer-repo state or drive the day-to-day PR loop. **Single source of truth for everything on `$PATH`.** Includes:
  - Policy/setup: `apply-ruleset`, `sweep-github-policy`, `install-release-{secrets,token}`, `enable-dependabot-security`, `detect-stack`
  - Release mechanics: `release-advance-major` (fast-forward the floating major branch — auto-detected highest `vN`, currently `v2` — to main after a release-side merge; one command for the old four-step `checkout vN && merge --ff-only main && push` dance)
  - Sync/drift: `release-sync` (build-dir + symlinks materializer), `release-drift-check` (consumer-side drift gate — rebuilds against the revision recorded in `.release/.release-sync-source` so it separates _drift_ from mere _staleness_; see ADR-0002 + `docs/proposals/301-consumer-drift-gate-rollout.md`)
  - Fleet: `managed-repos` (zero-logic accessor over `managed-repos.yaml` — the ONLY fleet source of truth, no discovery; resolves each repo to `$REPOS_ROOT/<path>`), `release-verify-fleet` (hermetic pre-flight lint sweep: clone fleet → `release-sync` from a candidate ref → `lefthook run pre-commit --all-files`; run before `release-advance-major`), `release-inbox` (read-only triage view over `consumer-filed` issues on this repo — the #348 feedback-loop inbox; groups by `[component]`, sorts clusters by recurrence/comment-count, `--json` for the Phase C batch run), `release-notify-source` (close-the-loop: reads a consumer-filed issue, comments the "upstream fix shipped — bump `@vN`, re-run" notice on each source PR it points at; dry-run by default, `--post` to send, `--close` to also close the release issue). The `release-fleet-triage` skill orchestrates `release-inbox` → fix loop → `release-notify-source`. See `docs/dev/fleet-tooling.md`.
  - PR loop: `gh-copilot-{on,off,wait,review}`, `gh-pr-checks-wait`, `gh-pr-resolve-thread`, `gh-release-issue`
- `bin-internal/` — CI-side scripts that composite actions and reusable workflows exec inside GitHub Actions runners (not on `$PATH`, never called locally)
- `templates/` — render templates (e.g. Homebrew formula)
- `tests/fixtures/` — synthetic projects per category, exercised by `_ci.yml`
- `docs/` — consumer guide, secrets, breaking-changes log
- `examples/` — paste-ready consumer release.yml files

### `bin/` is on $PATH via dodot

`~/h/dotfiles/release/profile.zsh` exports `~/h/release/bin` directly onto `$PATH`, sourced at login by dodot's shell handler. **Drop a new executable into `bin/`, mark it `+x`, and it's reachable by name in any new shell** — no per-script symlink mirror needed.

Earlier attempts that _don't_ work and shouldn't be reintroduced:

- `dotfiles/release/bin -> ~/h/release/bin` (a top-level symlink): dodot routes top-level symlinks through the **symlink** handler, not the **path** handler, so the dir never lands on `$PATH`.
- A real `dotfiles/release/bin/` of per-script symlinks: works (path handler kicks in) but creates a maintenance tax — every new script needs a matching symlink. The `profile.zsh` approach is strictly cleaner.

## Versioning contract — do not break

Consumers pin `@v1` (floating major) or `@v1.2.3` (exact). The `v1`
branch always points at the latest non-breaking tag.

| Bump  | Trigger                                                       |
| ----- | ------------------------------------------------------------- |
| PATCH | bug fix in any composite action, no input changes             |
| MINOR | new optional input, opt-in feature, new category workflow     |
| MAJOR | required-input rename, default behavior change, removed input |

Anything that forces every consumer to edit their thin caller is a
MAJOR. Six rust-CLI consumers currently track `@v1`; cutting v2 means
coordinating six PRs.

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
- Canonical shell E2E framework: **BATS** (`bats-core`). Each `@test`
  runs in a fresh subshell — matches the shell-subshell isolation
  pattern used in padz/dodot.
- Docker is for testing the _delivery mechanism_ (`apt install
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
arthur-debert/\* consumers can use `inherit`. Caught empirically in
lex-fmt/lex v0.9.1.

### Docs layout

- `docs/proposals/` (to do) and `docs/proposals/done/` (enforced —
  don't leave finished proposals at top level).
- `docs/references/` for "why" / design.
- `docs/dev/` for tool-base work.
- `docs/users/` for end users.
- mdBook only for large, public-facing doc sets.

## Operational rules

- **The gate is ONE definition, run everywhere — never reimplemented.**
  `lefthook.yml` IS the gate (the WHAT: the set of checks). Every environment
  (the WHERE) _invokes_ it: session start arms it (`setup-dev-env.sh` /
  `release-core init` installs the toolset + `lefthook install`), local commits
  run it, and CI runs the SAME `lefthook run pre-commit --all-files` as a
  required check. It is a HARD gate — a missing tool exits non-zero (never
  skips), and `--no-verify` is never an acceptable workaround (CI re-runs the
  gate on a clean runner where the tools are guaranteed). **To add or change a
  check, edit `lefthook.yml` only; never hand-copy a check into a CI job.** "CI
  is the source of truth" is the bug, not the design: CI is a _place_ the gate
  runs, not a second definition of it. If you're writing the check-list a second
  time, stop — invoke, don't reimplement.
- **Lint debt → the three-case model** (`docs/references/lint-debt-model.md`).
  Before "fixing a lint error," classify the file. **Don't want it linted?
  gitignore it** — the gate only runs on tracked files, so gitignored
  third-party/vendor/build content is never linted or tested (that's the point
  of gitignore). The linter therefore only ever sees files we own, so the only
  responses are _fix_: authored → fix the file once; tool-generated → fix the
  generator (never hand-fix or ignore). The managed `.markdownlintignore` is
  ONLY for committed-but-not-authored conventions (generated `CHANGELOG` /
  `UNRELEASED`, mdbook `SUMMARY`, fixtures) — not a per-repo escape hatch.
  Don't whack-a-mole per file: run `lefthook run pre-commit --all-files` and
  categorize the whole repo once. Release-owned file failing the gate = release
  bug; consumer-owned = fix there.
- **Bug fixes go here, not in consumers.** A bug surfaced by a
  consumer is fixed here, tagged as a PATCH, and the `v1` branch
  advanced. Consumers re-run; nothing for them to edit.
- **`bin-internal/ci-publish-crate.sh` is load-bearing.** It tolerates
  the case where a re-published older RC version exists per the
  crates.io JSON API but `cargo publish` errors. Don't replace
  with a `cargo search VERSION | grep` fallback — that's the
  pattern this script was written to fix.
- **Companion scripts:** `bin/install-release-secrets` propagates the 7
  release secrets to onboarded rust repos. Sister to
  `bin/install-release-token`. If the set of required secrets here
  changes, update that script.
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
