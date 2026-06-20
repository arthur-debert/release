<!-- generated - do not edit. See CHANGELOG/README.txt -->

# Changelog

## Unreleased

## 3.8.0 - 2026-06-20

- Fix canary run-resolver timing out on Kinds whose release commits a version bump (drop the head_sha==seed tie on the release run; release#810 follow-on)
- Fix setup-sccache action failing to load: removed a live ${{ github.repository }} expression from the key-prefix description prose (the manifest validator evaluates expressions even in descriptions)
- Add a notify-downstreams input to rust-cli.yml and tree-sitter.yml so the reusable workflow fans repository_dispatch out to downstream repos itself; thin callers drop their hand-rolled notify jobs (release#804)
- Add sccache (shared GCS-backed) Rust compiler caching to the Rust and Tauri CI/release lanes for cross-branch cache reuse
- Document tauri build/bundle decoupling + post-hoc dmg signing spike verdict
- Preflight fails fast on empty changelog; Apple-secrets check gated on signing requested
- Slim tauri prepare: drop in-prepare re-gate; assert release base sha is CI-green

## 3.7.0 - 2026-06-20

- setup-rust + tauri-app install a repo-selected fast linker (mold/lld) so a committed .cargo linker config builds

## 3.6.0 - 2026-06-20

- Install mold in the Tauri Linux deps so a committed mold linker config builds in the release lane

## 3.5.0 - 2026-06-20

- arm-gate composite caches release-core venv + gate toolset
- tauri-app.yml: release leg reuses compiled release-profile deps (own tauri-release key), ~2x faster warm releases
- tauri-app.yml: @v3 ref fix + Linux-leg warm-cache reuse
- tauri-ci.yml: wasm/Playwright caching, wasm-pack provisioning, check-command input
- tauri-e2e.yml: Swatinem cache, @v3 ref fix, build-once binary scaffold

## 3.4.1 - 2026-06-18

- Add a github-action kind canary fixture + register the gh-action family so gh-action.yml's cut path is exercised pre-cut
- Fix gh-action-ci.yml gate-toolset provisioning to resolve release_core via arm-gate (the staged release tree) instead of a release-only --from-source .
- Extend workflow-action-major guard to workflow self-refs + --major pins; sweep stale @v1/@v2 internal refs to @v3; remove dead arm-gate probe

## 3.4.0 - 2026-06-18

- canary rust fixture skips the apt-flaky emulated aarch64-linux build
- gh-action.yml arm-gate input materialize→install_tree (matches arm-gate@v3)
- gh-action.yml installs --major v3; release.yml self-cuts via @main (chicken-and-egg fix)
- managed .claude/IMPORTANT-RELEASE.md is prettier-clean (underscore emphasis)
- release.yml dogfoods gh-action.yml@v3 (was stale @v2; broke the cut post asset-fallback removal)
- WS8: remove the transitional bootstrap shims — setup-dev-env.sh, provision-gate-toolset.sh, gate-tool-versions.sh; install-release-core is index-only; arm-gate requires .release.major.txt for vN refs; provisioning + gate-toolset pins live solely in release_core (toolset.py/provision.py)

## 3.3.0 - 2026-06-18

- Drop the now-stale expect-verify-fail annotation for lex-fmt/lexed (its provisioned gate passes)
- release-core admin repos verify now PROVISIONS each consumer's deps (npm/pnpm/cargo/...) before gating, instead of running the gate on a dep-less tree and laundering the failure as an 'expected npm-deps artifact'; only a genuinely-absent toolchain is an honest skip (#772 follow-up)
- arm-gate reads .release.major.txt for the wheel major (action_ref fallback kept)

## 3.2.1 - 2026-06-18

- init commits the one-time CLAUDE.md @import migration (insert), not just create — a consumer on the pre-WS4 block no longer ends up with the @import uncommitted + a dirty tree + the next init re-staging it
- pip index served at the dedicated pypi.magik.works custom domain (escapes the account user-site domain redirect); update install-release-core + pip-index docs (#772)

## 3.2.0 - 2026-06-18

- python-pkg (+ all hand-rolled GH-release steps) now re-assert --prerelease on the release-edit/resume path, so a -release-rc / pre-release-suffixed cut is created with isPrerelease=true even when the release already exists (#726)
- harden arg parsing: pr review reply bodies starting with -/-- and changelog --section names with newlines (#733)
- init seeds .release.major.txt and install-release-core installs from the pip index with asset-path fallback (#760)
- canary seed commit uses --no-verify (harness setup; the canary's CI is the real gate) so a node-family (vscode-ext) seed isn't blocked by the missing-deps gate
- CLAUDE.md uses a one-line @import of managed .claude/IMPORTANT-RELEASE.md (#761)
- pr ready --ack-cycle-cap lands an otherwise-converged but cycle-capped PR through the guarded flip; cap now counts divergent rounds (new finding locations) not raw round count, and status routes converged-but-capped PRs to the ack command
- dev shim release-core: export PYTHONPATH + default RELEASE_HOME so fleet subprocesses and init work from a source checkout (#747, #749)
- install-release-core halts loudly on a failed wheel pull after a bounded transient-retry; optional steps stay best-effort (#763)
- fleet conformance matrix is now generated, not authored: `release-core admin repos audit --markdown` renders `docs/fleet-matrix.md` (live shared-vs-OWN workflows + per-repo findings + conformance %), regenerated weekly by `.github/workflows/fleet-matrix.yml`
- fix two release-core bugs the matrix surfaced: `yamlio` crashed under kislyuk python-yq (now detects the yq flavor and adapts the JSON read; mikefarah yq is installed canonically in `env/setup.sh`), and the `release_token` audit check false-FAILed fleet-wide (it mis-parsed the paginated `/actions/secrets` payload)
- gate: friendly-fail on a bare node checkout (no node_modules) with a `<pm> install` remediation instead of a raw svelte-check: command not found (#718)
- gate: --install-hook writes an untracked root lefthook.yml stub so a stray 'lefthook install' (npm prepare) can't create an empty starter config that silently ungates commits (#714)
- init provisions the dev env (toolset/hooks/caches/cert/submodules, --cloud arm); release-core gate --provision unifies gate-toolset pins into toolset.py (#762)
- `setup-dev-env.sh` now initialises git submodules (`git submodule update --init --recursive`, guarded on a root `.gitmodules`) in BOTH local and cloud sessions, ABOVE the cloud-only gate. The init previously lived in the cloud-only §1 block on the assumption that a local dev always has submodules in place; a fresh checkout (e.g. the live-fire round's fresh clone of `lex-fmt/lex`, which carries a `comms/` submodule) does not, so the gate/tests failed on missing submodule content. It's now a no-op when the repo has no submodules and warns loudly (never aborts the bootstrap) on a transient fetch failure (#706)
- `release-core how-to` now surfaces a fresh-checkout prerequisite note on the verbs section: coverage/test need the repo's deps installed and any git submodules initialised first, or you'll hit errors that look like a tooling bug but are just missing content (e.g. `Cannot find module out/test/unit/index.js` on `lex-fmt/vscode`). SessionStart does both automatically; the note is for the manual/fresh-clone case (#728)
- changelog add: print help for `--help`/`-h` instead of a slug error (#686); add an opt-in `--section <name>` flag to write a `### <name>` heading above the bullet, default stays a bare bullet to match the renderer's flat list (#720)
- The `rust-ci.yml` e2e job and the `bats-e2e.yml` reusable workflow now install the bats helper libs (bats-support/assert/detik/file) to a runner-writable `${{ github.workspace }}/.bats/` dir instead of the action's `/usr/lib/bats-*` default; the old path was written as root on a cache miss but restored as the non-root runner on a cache hit, failing with "Permission denied" on alternating runs and blocking the v3.1.2 canary (#690)
- The npm-quality prettier pre-commit check is now `--check` (was `--write` + `stage_fixed`); under the bare `release-core gate`'s `--all-files` run, `--write` reformatted untouched managed docs across the whole tree and pulled them into the diff. The check still fails non-zero on an unformatted file but no longer silently rewrites files it was only asked to inspect (#713)
- A failing cargo-fmt pre-commit check (rust-quality, zed-extension, tauri-app) now prints a concise summary — the files needing formatting plus `run \`cargo fmt\` to fix` — instead of dumping rustfmt's long colorized unified diff; the check stays check-only (no silent auto-apply) and still exits non-zero (#691)
- `release-core coverage` no longer forwards `--coverage` to a tree-sitter grammar's `tree-sitter test` script (the tree-sitter CLI rejects it with `unexpected argument '--coverage'`); the tree-sitter Kind now falls through to the no-coverage path, consistent with nvim-plugin (#696)
- `release-core coverage` on a Kind with no coverage-capable toolchain (nvim-plugin, tree-sitter) now prints a clear, expected "this Kind has no coverage tool" notice that points at `release-core how-to`, instead of a terse line that read like a crash (behavior unchanged — still exit 1) (#701)
- `release-core coverage` now suppresses the verbose build/test stream by default and prints only the trailing per-module summary table; a `--verbose`/`--raw` flag restores the full live stream, and on failure the whole captured output is shown for diagnosis (#694)
- `release-core cut --help` now documents the reserved `-release-rc` verification suffix. The cut verb's module docstring already described it, but `cut --help` prints the `USAGE` string (not the docstring), which only covered the version grammar + pre-release stripping — so the live-fire-only `-release-rc` reservation was invisible to consumers. Added the pre-release + `-release-rc` reservation paragraphs to `USAGE` (#693)
- `release-core cut` now names the remediation when `gh workflow run` fails with HTTP 422 "Workflow does not have 'workflow_dispatch' trigger". The consumer's `release.yml` (e.g. a docs-site repo that authored its own thin caller) needs an `on: workflow_dispatch:` trigger so `cut` can dispatch it; previously cut just forwarded the raw gh error with no guidance on what to add (#725)
- `release-core how-to` now warns up front (dev-cycle step 1) to branch off `origin/<default-branch>` BEFORE committing, with the reason spelled out: the SessionStart `release-core init` can auto-commit a managed sync onto the local default branch, and branching/committing off that tip drags the auto-commit into the PR diff as an `alien commit` (#685)
- `release-core how-to` describes the CI release pipeline Kind-aware: interpreted Kinds (nvim-plugin, tree-sitter, docs-site) render `prepare → release` (no build/sign), the rest render the full `prepare → build → (sign/notarize) → publish`; the `release-cut --help` `-release-rc` note is annotated the same way (#704)
- `release-core how-to` orientation now flags the managed per-Kind entry point (`bin/check*`, `lib/release_core/`) as EPHEMERAL — installed by `release-core init` and listed in `.git/info/exclude` — so seeing `bin/check` untracked in `git status` is expected, not a problem to fix (#697)
- The `gh-pr-review-loop` skill now enumerates the full reviewer adapter set (`copilot`, `coderabbit`, `gemini`) and every `ReviewLifecycle` state (`not_requested`, `requested`, `in_progress`, `done_clean`, `done_comments`) so the doc matches the `name=lifecycle` pairs `release-core pr status` actually emits (#699)
- The `gh-pr-review-loop` skill documents that `release-core pr wait` is a 4–6 minute blocking call and that the Claude Code harness may push a short-timeout Bash call to the background (printing `Command running in background`); it instructs the agent to invoke `pr wait` with an explicit long Bash `timeout` to keep it foreground, and that a long run / background notice is expected, not a detached wait (#692, #721, #730)
- Friendly-fail runnable verbs on a bare checkout (deps/wasm/tree-sitter not provisioned) + live-fire prompt provisioning + changelog-before-cut steps
- `orc livefire` now tears down the throwaway `-release-rc` tag even when the subordinate agent's session ends before emitting the feedback/verdict block. Teardown was coupled to a successful feedback parse (the rc came from `feedback['rc']`), so a turn-budget/throttle-truncated session left a dangling `-release-rc` release/tag on the consumer. The rc tag is now captured straight from the transcript (`extract_rc_tag`), independent of parsing, and teardown always runs off it (#709)
- `orc livefire` no longer hard-errors and loses a consumer's findings when the feedback block is missing. A missing/malformed block now degrades to a structured `feedback-skipped` finding (carrying the rc tag) filed to the #348 inbox, so the run is recorded rather than discarded (#709)
- The live-fire prompt now bounds the PR review-wait so step 4 (the verification cut) is always reached. A run could end mid review-wait in step 3 and never reach the cut; the agent is now told the cut does not depend on the PR reaching ready, to record a slow review-wait as a finding and move on (#722)
- `pr review show` now labels each thread's ids — the numeric `comment-id` (the handle `pr resolve-thread` and the new `pr review reply` consume) versus the GraphQL `graphql thread id` — with a one-line legend, so it's unambiguous which to feed back (#687)
- `pr ready` softens the post-flip note: a brief `VALIDATING` status after the draft→ready flip now reads as normal/expected with no further action needed, rather than as an error or a mandatory extra wait (#703)
- `pr review reply <comment-id> <body>` posts a threaded reply to a review comment — the rationale / push-back path that previously had no `release-core` verb (agents dropped to raw `gh api .../comments/<id>/replies`) (#695)
- `bin/pr-loop-guard` now `normpath`s the sequential-`cd` accumulator (and the initial payload cwd), so a sequence like `cd a && cd ..` folds back to the original directory instead of leaving a distinct `cwd/a/..` string. Previously the dedup set treated `cwd/a/..` and `cwd` as separate candidates (redundant sentinel checks) and the deny message printed un-normalized `…/a/../.git/pr-loop-armed` paths for the user to `touch`. The OS-level file-existence check always resolved `..` correctly, so this was a dedup + cosmetic fix, not a guard-correctness bug (#674)
- Publish a GitHub Pages pip index for release_core wheels
- pr-loop: tolerate an UNSTABLE merge state from a non-required check re-running on ready_for_review when the rollup is green (#715)
- pr wait auto-performs the guarded draft->ready flip at READY
- review-audit (#740): extract.py --sample N evenly subsamples PRs across history for very large repos; issue-level bot comments now count toward the mechanical 'first feedback' clock (metrics fields renamed first_feedback_wait_min / commits_after_first_feedback) and are read by the stage-5 LLM judge; stage2.workflow.js validates operator-supplied reviewed[] and cross-checks slim reviewers[] against the configured enum (#641)
- denoise fixture suite + SKILL.md output-dir fix (#740)
- Add repo-agnostic review-audit skill: measure whether bot PR reviews (Copilot/Gemini/CodeRabbit) earn their cost
- tauri-ci: opt-in e2e-rust input sets up a Rust toolchain in the e2e job (for consumers whose pre-test compiles wasm from source)
- repos verify self-heals poisoned fleet clones instead of skipping them (#748)
- Stamp release_core wheel version from the release tag (#758)
- Provision pinned mikefarah yq across all gate environments (#755)

## 3.1.2 - 2026-06-16

- Correct audit/model notes: comms docs workflow pin is @v3
- De-jargon release-internal surface (docs, orchestrator, tests, env) — #655 item 2
- orc livefire: require a verdict block + prod the agent once for skipped feedback (#683)
- orc livefire: tolerate agent-fenced feedback YAML + force blocking stdio (verbose EAGAIN)
- Reusable workflows now reference internal composite actions at @v3 (not the frozen @v2), so consumers on @v3 run current action code; a -release-rc verification cut builds + tags + creates the GH release but never publishes to any external registry (crates.io, npm, PyPI, OpenVSX).

## 3.1.1 - 2026-06-16

- de-jargon release_core help strings + docstrings + comments (prose only; code identifiers + schema keys kept)
- Remove release-sync/jargon legacy: delete redundant retired-skill list, rename compose-engine + contract identifiers off canonical/materialize/tombstone/pilot-running, migrate CLAUDE.md marker to release-core
- de-jargon docs/: retire stale release-sync/materialize/canonical/drift vocabulary, keep ADRs + live filenames
- strip lefthook's hardcoded ANSI escapes from release-core gate output when stdout is not a TTY
- Add the canonical standard live-fire verification prompt + structured-feedback schema (#663.2)
- orc livefire: parallel fan-out across N consumers (--all + --concurrency) with an aggregated rollout report (#663.3 phase 2)
- Add orc livefire — single-consumer live-fire verification runner (clone → standard prompt → harvest feedback → file to #348 inbox → teardown rc) (#663.3 phase 1)
- orc livefire: attempt rc teardown even when finding-filing fails, then re-raise (no stranded -release-rc)
- pr ready/status now gate on mergeStateStatus (CLEAN), not just the async-stale mergeable field — a conflicting/behind/uncomputed PR no longer flips to ready
- Reserve the -release-rc pre-release suffix as a no-trace verification cut (tag-only; bump commit not pushed to the branch) for the live-fire harness (#663)
- Add the standardization model + fleet audit docs; surface the standardize-default philosophy in README + GLOSSARY (#656)

## 3.1.0 - 2026-06-14

- Remove run-precommit-gate.sh's pre-WS3 root-lefthook.yml consumer fallback (#569 B4): 0/19 consumers track a root gate config, and release-self self-releases via gh-action.yml so this script never runs there. The managed .release/lefthook.yml path via LEFTHOOK_CONFIG plus the release-core materialize branch cover all live cases; an explicit caller-provided LEFTHOOK_CONFIG is now honored over the managed copy.
- Bump the per-Kind copilot-review.yml caller templates from @v1 to @v3 (#569 B9), clearing the last mixed-major straggler: every managed consumer's copilot-review.yml re-renders to @v3 on its next release-core init (pull), aligning it with the @v3 ci/release/test callers
- Ship the copilot-review.yml workflow template for the docs-site, nvim-plugin, and tree-sitter Kinds (#569 B9), which previously managed no workflows. Brings copilot-review under management fleet-wide: on next pull, init renders the marked @v3 copy and overwrites these consumers' hand-authored markerless @v2 callers, clearing the last mixed-major stragglers (comms/nvim/tree-sitter-lex)
- tree-sitter-ci and nvim-plugin-ci: arm the gate toolset (materialize bin/check) before the umbrella check, matching the other *-ci workflows — fixes exit-127 when a consumer lacks a committed bin/check
- Remove the redundant bin/detect-kind shim and its bats canary (classification is covered by test_core_manifest.py); detect-kind stays available via release-core detect-kind and the pip console-script
- Add a final GATE: OK (N checks) / GATE: FAILED (names) verdict line to release-core gate (#628), derived from lefthook's exit code so a `gate | tail` view can't mask a failure; plus a --quiet mode that prints only the verdict on success (full output on failure)
- retire the repos_migrate fleet seeder and the audit_repo ci_calls_bin_check check — both dead-transitional now that the fleet is fully seeded onto the pull model (0/19 carry pre-WS7 markers); part of #569 / #635
- Add GLOSSARY.md as the authoritative vocabulary, rewrite README around the four pillars, and sweep the legacy terminology listed in GLOSSARY.md's banned-terms table out of docs, skills, and help-strings (prose only; `gate` is kept).
- Point CLAUDE.md's open-work section at the live #569 tracker (the #348/#349/#350 epics are closed) and remove the transient cleanup-agreement working doc

## 3.0.0 - 2026-06-13

- canary run: strip PYTHONPATH and checkout-shim vars from the sandbox env so the round exercises the candidate wheel, not the operator checkout
- testing: provenance markers + ratchet lint for external-surface fixtures
- prstate: CodeRabbit reviewer adapter (requestable, attach-verified), piloted on phos-org repos via per-repo `required_reviewers:` opt-in; default required set stays `[copilot]`, and the engine gates on the full required set against the current head
- Coordinated execution splits the PR loop: implementer stops at PR-open, coordinator owns waits + the ready flip, a fresh shepherd subagent per review round (dev-cycle §2, how-to, gh-pr-review-loop skill)
- release-core how-to now renders the §2 coordinator (complex/multi-PR) discipline; CLAUDE.md stub names it
- Fix pr-loop-guard resolving chained relative cd targets against the original cwd instead of folding left (release#632)
- remove fleet-saturated back-compat: pre-commit-framework gate fallback + legacy single/two-file changelog detection (#569)
- BREAKING: remove deprecated singular wasm-package input from rust-cli.yml; use wasm-packages (#569 B7)
- remove fleet-saturated WS4/WS7 untrack migrations, husky unset, .release-sync-state tombstone + changelog classifier (#569)
- rust-ci.yml: optional wasm-packages companion (PR-time wasm check/build) + sanctioned-bespoke # UNMANAGED marker convention (#630)
- admin repos verify: refresh clones unconditionally; remove --refresh flag

## 2.21.0 - 2026-06-12

- admin repos verify classifies expected toolchain-artifact FAILs (npm-deps mechanically, sibling-checkout cases via expect-verify-fail annotations; exit non-zero only on unexpected, stale annotations flagged) and new admin repos poke — one-command fresh-event consumer verification (empty commit, HEAD-SHA run resolution, classified --watch verdict); the INFRA/PROJECT failing-step classifier is extracted to release_core.classify, shared by canary run / verify / poke (#594, #595)
- managed .markdownlintignore exempts generated release-notes.md (same class as CHANGELOG) — fixes full-tree markdownlint reds in consumers that cut releases (#598)
- admin secrets token|install gain --repos per-repo targeting, validated against managed-repos.yaml (projects + canaries) (#601)
- canary: vscode-ext second family fixture + concurrent multi-family rounds + cert-only Apple signing on the rust canary (#605)
- pr review request now verifies the review_requested edge actually attached — a request GitHub silently drops (service stall / quota) fails loud with exit 1 instead of stalling the loop (#614)
- fix(canary): init registers a new family before the secrets converge — the token install validates against the registry init itself appends (self-deadlock caught on the first live vscode-ext init); registers the vscode-ext canary
- release-core admin canary init: idempotent create/reset of a family's canary repo from its tests/fixtures/<kind>/ seed — public create, fixture seed + from-source boot, fail-closed secrets (RELEASE_TOKEN only, publish trio refused), ruleset, registry append; --reset force-push sanctioned only for registered canaries (#604)
- fix(verify): classify.py parses the real captured lefthook summary glyph (✗, U+2717) — the 🥊 guess made every npm-artifact FAIL read as unexpected
- fix(cut): bump shortcuts (major|minor|patch) resolve the current version from the latest git tag in manifest-less repos — including repos with no detectable Kind, like release itself (#596)
- release-core cut refuses without green canary/<family> commit statuses on the exact HEAD being cut (no skip flag, #606)
- admin policy ruleset: auto-detection resolves reusable-workflow caller jobs to their nested '<caller> / <job>' contexts (gh contents API at the pinned ref) instead of proposing the never-reported bare caller name (#602)

## 2.20.0 - 2026-06-11

- arm-gate: non-vN action refs (canary/* branches, bare SHAs, feature branches) install the release-core wheel --from-source from the action's own staged tree — the wheel always matches the composite's resolved ref (#587 OQ4) — after a published-wheel resolution probe (install-release-core --print-url) that keeps the gh-authenticated resolution path (#535 surface) exercised (#587 slice 1)
- release-core admin canary run --ref: pre-ship canary round — publish canary/<sha12>, seed the canary consumer from source, dispatch CI + prerelease cut as fresh events, classified report + commit status (#587 slice 1)
- orphaned packaged-binary smoke-hook templates retired (dead since WS7; tombstoned so seeded consumers sweep the stale copies) (#590)

## 2.19.0 - 2026-06-11

- fleet-loop doctrine corrected: repos verify runs BEFORE the cut (release.yml auto-advances the major at cut — there is no separate advance step); stale soft-gate and flat-alias claims swept from the skill + fleet-tooling docs
- arm-gate gains toolset:false (materialize-only mode for consumer-authored jobs needing managed bin tools); release-core init warns at seed time when a consumer workflow job invokes an ephemeral mirror without materializing (#581)

## 2.18.0 - 2026-06-11

- consumer-contract manifest generated from sync.py (release-core admin contract dump|check) + assumption lint catching managed-path references in jobs that never materialize (#584, epic #583 WS-A)
- release-core pr wait retries transient gh/network poll failures with backoff instead of dying on the first blip; cut --help no longer shows the retired release-cut flat name (#582)
- orc probe now runs the clone's real SessionStart boot (setup-dev-env.sh) before the agent session, with a fail-loud boot-assert and boot report (#578)
- managed-sync commits and the .release-sync-source marker now carry the resolved release tag (e.g. v2.17.1) instead of the static wheel version (#580)
- WS-B: synthetic-consumer test/CI surfaces audited to the post-WS7 contract; consumer-contract lint baseline drained to zero (#588); stale repo-shape doc claims fixed; rerun-vs-fresh-event trap documented (epic #583)

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

