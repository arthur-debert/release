<!-- generated - do not edit. See CHANGELOG/README.txt -->

# Changelog

## Unreleased

## 2.17.1 - 2026-06-11

- rust-ci.yml e2e + bats-e2e.yml: materialize the managed tree via arm-gate (post-WS7 bin/check-e2e is an untracked mirror; the stale sparse checkout yielded a missing runner, exit 127 on every bats consumer)

## 2.17.0 - 2026-06-11

- prstate polish (#564): post-flip READY messaging, flip re-run heads-up in pr ready, thread-reading tool named in ADDRESSING, --reviewer named in the too-many-args error, pr wait cadence line
- Add kind-aware `release-core coverage` task verb (cargo llvm-cov / go tool cover / test runner --coverage) with a loud error when no coverage tool exists for the Kind (#568)
- Dev-cycle doctrine: the state engine is the arbiter — every push re-requests review; the minor/substantial re-review nuance is dropped (#565)
- gate pins its lefthook runner to the toolset pin and fails loud on an unmaterialized gate config (#567)
- init: loud branch-from-origin/<default> hint when the managed sync auto-commits on the checked-out default branch; how-to step 1 says branch off origin, not local main (#566)
- Retire .release/ORIENTATION.md for real (tombstone + deterministic CLAUDE.md stub refresh) and complete the WS6 tombstone catalog from re-derived template history (#563)
- Gate: yamllint ignores generated pnpm-lock.yaml (committed-but-not-authored; pnpm's brace style failed the default braces rule)

## 2.16.0 - 2026-06-11

- release-core pr ready: the engine-owned guarded draft->ready flip (refuses unless state is READY; --undo flips back unconditionally)
- pr wait: ONE engine-driven wait replaces pr review wait + pr checks-wait — exits when the state engine calls for agent action; cadence is data (20s poll, 1.5x backoff to 90s, 45m cap; --poll/--timeout override)
- docs: lean doc set — breaking-changes log + dev-era references deleted; tooling/harness/README rewritten to the shipped minimal-footprint model (ephemeral .release/, untracked mirrors, wheel carrier)
- Lockstep trio restored: gh-pr-review-loop rewritten to the reviewer-agnostic, block-in-turn state-machine discipline (pr status/wait/ready); dev-cycle.lex, tooling.lex §3, how-to, and CLAUDE.md swept into agreement; gate-fidelity doctrine note added
- Reviewer-agnostic review surface: ReviewerAdapter grows the act side (request/cancel + instruction-file declaration); 'pr copilot on|off|review' renamed to 'pr review request|cancel|show [--reviewer]' with no aliases (waiting moved to the engine-owned 'pr wait'); gh-copilot-* bin scripts retired
- pr status now sees a pending Copilot request: requested reviewers are sourced from GraphQL reviewRequests (gh pr view --json reviewRequests silently omits Bot-typed reviewers, so copilot=requested was unreachable and status kept demanding a fresh request)
- PR review-loop done-signal and comment reading now driven off GraphQL reviewThreads (all threads, any author) — the partial REST inline-comment fetch and its exact-string Copilot author filter are gone (#515, #455)

## 2.15.2 - 2026-06-11

- install-release-core derives the wheel's major line from the repo's @vN thin callers (highest wins) — a v2 consumer's SessionStart can no longer pull a v3 wheel once v3 exists (#551)

## 2.15.1 - 2026-06-10

- arm-gate's materialize pins the wheel to the action's own major line (derived from github.action_ref) — a v2 workflow can no longer pull a v3 wheel once v3 exists (#541)
- install-release-core --from-source now accepts a release checkout ROOT — descends to the nested templates/commons/lib/release_core package automatically (#516)
- admin repos verify self-spawns nested release-core calls via sys.executable -m release_core — the local pre-flight works from the in-checkout shim again (#534)

## 2.15.0 - 2026-06-10

- init now removes retired release-distributed files (check-fmt/check-lint, changelog shims, bin/release, .release-sync-state.yaml) — provenance-gated tombstones, one managed commit (#527)
- WS7: symlink mirrors (bin tools, .editorconfig, skills) are now ephemeral — untracked, init-materialized, listed in .git/info/exclude; one-time managed commit untracks pre-WS7 seeds; release-issue-relay dropped from skill distribution (escalation is binary-carried) (#528)

## 2.14.0 - 2026-06-10

- gh-action.yml's prepare materialize now skips when a root lefthook.yml exists — unblocks release's own cut (init has no detectable kind on the source repo) (#544)
- Removed release-core init --config-only and --full; bare init (full materialize) is the only mode
- Bootstrap quartet (settings.json, install-release-core, setup-dev-env.sh, pr-loop-guard) materializes as real tracked files so a fresh clone can boot itself

## 2.13.2 - 2026-06-10

- Bot gates on all stacks materialize .release/ and run the real gate via release-core (no more silent skip on post-WS3 consumers)
- Gate globs now match repo-root files (a leading `**/` silently exempted root `Cargo.toml`/`go.mod`/`eslint.config.*`/`*.sh` from the gate)
- shellcheckrc vendored into .release/ via --rcfile; CI workflows use the pinned provisioner instead of apt shellcheck 0.9.0

## 2.13.1 - 2026-06-10

- arm-gate: pass GH_TOKEN to the materialize step so install-release-core's gh call doesn't exit 1 (fixes ci/check fleet-wide)
- Pin and reconcile every gate tool to one version across dev and CI (no more install-if-missing drift)

## 2.13.0 - 2026-06-09

docs: consolidate 9 narrative .lex into 4 (README, dev-cycle, tooling, harness); delete per-Kind reference docs; add ADR-0005 (minimal footprint) (epic #501, #508)
- CLAUDE.md is now a short stub pointing at release-core how-to; ORIENTATION.md and the dev-cycle/infra skills (except gh-pr-review-loop + release-issue-relay) are no longer synced into consumers
- Vendor gate configs into .release/; the git hook + CI bot gate run from the binary (lefthook.yml leaves the consumer); npm typecheck fails loud on an absent runner
- release-core admin repos verify runs the gate via release-core gate (finds the .release/ config); bare lefthook run broke post-WS3
- Make the .release/ build dir ephemeral (gitignored, composed on demand by release-core init) and retire the drift/sync subsystem (release-sync + release-drift-check)

## 2.12.4 - 2026-06-09

- Bump Node-20-pinned GitHub Actions to Node-24-capable majors (checkout@v6, setup-node@v6, setup-python@v6, upload-artifact@v6, download-artifact@v7, cache@v5, setup-go@v6, deploy-pages@v5) ahead of the 2026-06-16 runner cutoff (#518)

## 2.12.3 - 2026-06-09

- fetch-deps: force UTF-8 stdout/stderr + electron-app Windows shim invokes python (not bash) — fixes win32 node builds

## 2.12.2 - 2026-06-09

- how-to/test-unit: surface a CI caller check-command: as the unit suite when no manifest test exists (#507 dogfood F1); changelog add now applies the - bullet convention (F4)
pr-loop-guard: honor the sentinel armed in a cd-target repo (fixes the cross-repo/subagent false-deny)

## 2.12.1 - 2026-06-09

how-to/test-unit: detect the nvim app-bin/test-all runner (not a stale make test guess)

## 2.12.0 - 2026-06-09

align gh-pr-review-loop skill + ORIENTATION to the canonical draft-first dev cycle (draft=WIP, ready=human signal); drop the stale never-draft rule (Copilot reviews drafts)
Fix release-core CLI crash (ModuleNotFoundError: click): the bin/release-core shim now re-execs under the isolated release_core venv to provision click, restoring the entry broken when #487 moved deps out of the user site (#497)
Fix setup-dev-env.sh provisioning actionlint via a non-existent apt package on Linux; use the pinned rhysd downloader like the CI gate provisioner, so the SessionStart gate toolset is actually armed (#497)
how-to/gate: clarify the gate is lint/format only (tests run separately); strip lefthook color noise
Enforce the PR review loop with a PreToolUse guard that gates gh pr create on the gh-pr-review-loop skill (#495)
add release-core gate + how-to: one quality entry and a Kind-aware playbook (epic #501)
release-core shim re-execs under the venv on ANY missing dep (not just click); gate-tool versions single-sourced in gate-tool-versions.sh (shared by both provisioners)
add release-core test-unit/test-e2e/test-all/build/run + component-aware how-to: detect and run THIS repo's real commands per component (node/rust/make/mkdocs), never a per-Kind guess (#507)
add `release-core admin repos migrate` — the pull-model successor to the removed `orc propagate`: for each managed repo it clones, runs a bundle-sourced `release-core init` (full materialize + managed-only auto-commit), and opens one managed-sync PR; --only/--dry-run supported. Used to roll the fleet onto the pull model (#416)
tauri-e2e: bound cache growth with weekly rotation + add cache-key-prefix lane isolation (#491)
tauri-e2e: resolve the private native dep as a git-dep (drop sibling-ref duplicate pin, release#506)

## 2.11.7 - 2026-06-08

init: force-add managed paths in the auto-commit (`git add -f`) so a consumer .gitignore covering a managed path (e.g. `.claude/` shadowing the managed `.claude/skills/`) doesn't silently drop it from the migration commit — without this, 6 fleet consumers staged but never committed their managed tree (#416 fleet rollout)

## 2.11.6 - 2026-06-08

the release_core wheel now bundles the FULL template tree (commons/, components/, every per-kind dir) plus the distributed skill catalog (PUSH_ALL + REPLACE_IF_PRESENT), excluding the package subtree and release-only skills, so a later init can materialize offline (#476)
remove two vestigial dangling tracked symlinks (tests/changelog-check-fixtures/*/bin/changelog-render → retired templates/commons/bin/changelog-render) that broke 'uses: arthur-debert/release@v2' action-staging for every consumer's CI; add a guards-job check that fails on any dangling tracked symlink (#476 bake, carrier)
Review and correct the rebuilt .lex doc set; refresh stale terminology, version pins, and retired-command references in the older docs/ reference material
make the full managed-tree materialize the DEFAULT for `release-core init`: a bare `init` (what SessionStart runs) now materializes the whole managed tree from the wheel bundle (the `.release/` build dir + every working-tree mirror — skills, ORIENTATION, configs, the CLAUDE.md block) and auto-commits managed changes, so consumers self-cut-over to the pull model on the next wheel pull with no `orc propagate`; the old config-subset behavior moves behind `--config-only`, `--full` becomes a redundant alias of the default, and the flag guards re-key off "full mode active" (`--commit`/`--force` are redundant in default mode, `--no-commit` skips the auto-commit) (#476)
`release-core init --full` materializes the WHOLE managed tree offline from the wheel bundle via a new sync source abstraction (GitSource/BundleSource), byte-identical to release-sync, and auto-commits only the managed paths when they change (idempotent, opt-in behind the flag) (#476)
init: content-compare managed real-file copies (.github/workflows/*) so a steady-state sync is a true no-op — fixes the phantom change count + failing auto-commit, and a flip-flop where byte-identical copies were swept as stale then rewritten each run (#476 bake)
init: tolerate (warn, don't reject) --commit/--force in default full mode — the deployed stale SessionStart resolver passes --commit on the first cutover pull; rejecting it stalled the whole fleet's bootstrap-forward (#476 bake, carrier run)
init: drop [skip ci] from the managed auto-commit (it blocked managed-only migration PRs under a required-status-checks ruleset — CI was skipped so required checks never ran); harden the resolver's venv rm -rf guard + symlink glob (#476 bake, first real migration)
exclude .release/.claude/skills/ from the markdownlint gate so distributing the vendored skill set does not fail consumers ci/check on the synced skills non-conforming markdown
- Retire the `changelog`/`changelog-add`/`changelog-cut`/`changelog-render`/`semver` `bin/` shims: release CI now pip-installs `release_core` and calls the console-scripts by name (#476).
- release.yml: stop opting out of changelog handling (`changelog-path: ''` → `CHANGELOG.md`) so each cut rolls the unreleased fragments into `CHANGELOG/<version>.md` + renders `CHANGELOG.md` via the fragment-directory model — fixes the backlog where 14 fragments accumulated unconsumed across v2.10–v2.11.5
remove the orc propagate fleet-push command — the fleet is now PULL-only (cut a release + advance-major; consumers self-update at SessionStart, seeding a pre-pull consumer = one resolver run + a managed-sync PR). Deletes orchestrator/propagate.py + the CLI wiring; rewires CLAUDE.md, the release-fleet-ops skill, docs, and bin/orc; orc keeps watch/probe/run/sessions (#416)
install-release-core: install release_core into its OWN dedicated venv (never the user pip / system site / a project venv) and symlink the console-scripts onto PATH — fixes the silent SessionStart pull failure when `python3` resolves into a venv (`pip --user` is rejected there). The resolver now owns reachability (BIN_DIR on PATH + `$GITHUB_PATH` persistence under Actions), so callers just invoke it — the `gh-action.yml` step drops its hand-rolled PATH wiring + verify loop. Adds `--from-source PATH` (install from a local checkout, same isolated-venv machinery) and collapses `bin-internal/install-release-core-pkg.sh` to a one-line delegation — one install definition, the source is the only thing that varies. Tolerates the deployed caller's `--user`/`--break-system-packages` as no-ops (bootstrap-forward). (#476 bake)
retired the redundant consumer `bin/gh-release-issue`, `bin/gh-task-status`, and `bin/release` shims in favor of the pip-installed `gh-release-issue` / `gh-task-status` console-scripts and `release-core cut`; the cascade handler now dispatches `release.yml` directly and `done-check` probes `release.yml` instead of the retired `bin/release` (#476)
fix the broken-symlink sweep so a consumer `bin/` symlink whose `.release/` target is REMOVED this sync (present in the still-live old tree, absent from the new one) is swept before the `.release/` swap instead of left dangling — the lex `init --full` cutover left 7 committed dangling symlinks (retired changelog/semver shims); also report the real file count of a full-sync auto-commit (`git diff-tree`) instead of the pathspec count (#476)

