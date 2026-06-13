release-core, the gate, distribution, and workflows

    The machinery layer of `release/`: one CLI an agent drives, one quality
    gate it runs, one sync engine that builds managed files into a
    consumer, and one set of reusable GitHub Actions workflows. This document
    is the single home for all four — it absorbs the former `tooling`,
    `distribution`, `workflows`, and the mechanism half of `injected-files`,
    plus the directional roadmap.

    The agent-facing surface (orientation, skills, the session boot) lives in
    `harness.lex`. The "why" decisions live under `adr/`.

1. release-core — the one CLI

    `release-core` is the single CLI for every infrastructure and dev-cycle
    task: the quality gate, the changelog, syncing managed files, driving a PR,
    cutting a release. It is a `click` command tree, so the help IS the map —
    an agent learns it by discovery, not by memorizing:

    - `release-core --help` — the top-level groups.
    - `release-core <group> --help` — a group's subcommands.
    - `release-core <group> <command> --help` — one command's flags.

    The tree is rich; you are not expected to know it cold. When in doubt,
    `--help` your way down. The flat maintainer command names that used to
    exist (`release-verify-fleet`, `managed-repos`, `release-cut`, …) were
    retired in the CLI cutover (#468) — use `release-core <group> <command>`.
    A few flat consumer aliases remain (see [#1.3]).

    1.1. Consumer-facing commands

        Run from inside any repo:

        - `release-core how-to` — the kind-aware task playbook (lint / test /
          build / release / run + the draft-first dev cycle). The single source
          of procedural truth — see `harness.lex` and `dev-cycle.lex`.
        - `release-core gate` — run the quality gate (see [#2]).
        - `release-core init` — build the managed tree from the wheel
          bundle: the gitignored, ephemeral `.release/` build dir plus the
          working-tree mirrors ([#5]). What SessionStart runs; the only mode
          (the `--config-only` escape hatch was removed in #532). Auto-commits
          a managed change when one exists — consumers self-update by pull,
          there is no push step.
        - `release-core build` / `run` / `test-all` / `test-e2e` — the repo's
          kind-aware task verbs (single app-root commands).
        - `release-core changelog add|cut|render` — manage the changelog.
        - `release-core semver validate|get` — validate or read a version part.
        - `release-core detect-kind` — report this repo's release Kind.
        - `release-core cut` — cut a release for this repo.
        - `release-core audit` / `status` — release posture / the
          pilot-running done-check.
        - `release-core issue file <component> "<symptom>"` — escalate infra
          friction upstream to `arthur-debert/release`.
        - `release-core pr …` — the PR-loop helpers ([#3]).
        - `release-core ci fetch-deps|fetch-artifact` — CI-glue fetch helpers.

    1.2. Maintainer-only commands

        Run from inside `arthur-debert/release`:

        - `release-core admin repos list|prs|audit|verify` — fleet views and
          the hermetic pre-flight sweep.
        - `release-core admin release advance-major|betas|lex` — release-side
          mechanics; `advance-major` fast-forwards the floating major branch.
        - `release-core admin policy ruleset|sweep|dependabot` — GitHub policy.
        - `release-core admin secrets install|token` — provision release
          secrets onto a repo.
        - `release-core admin inbox [notify-source]` — the consumer-filed
          issue triage inbox and the close-the-loop notifier.

    1.3. The flat consumer aliases

        A short list of flat command names is kept on PATH for consumers, each a
        thin alias into the tree:

            | Alias | Does |
            | changelog | manage the changelog (add/cut/render) |
            | semver | validate or extract a version part |
            | detect-kind | detect this repo's release Kind |
            | gh-task-status | the PR state machine (state + next action) |
            | gh-release-issue | file or comment on a release issue upstream |
        :: table ::

        These reach a consumer's PATH as `release_core` pip console-scripts
        (from the installed wheel), not as synced `bin/` scripts. The old
        `release` caller was retired in #476 — cut a release with `release-core
        cut`.

2. The quality gate — release-core gate

    `release-core gate` is the ONE quality entry. It wraps `lefthook run
    pre-commit --all-files`, so green here == green in CI, with no false-green
    on unstaged files. It points lefthook at `.release/lefthook.yml` via
    `LEFTHOOK_CONFIG` — there is no tracked root gate config in a consumer
    (WS3, #524); the gate definition lives only in the ephemeral build dir.

    It is a HARD gate: a missing tool exits non-zero (a setup failure, never a
    skip), and `--no-verify` is never an acceptable workaround — CI re-runs the
    same gate on a clean runner where the tools are guaranteed.

    The gate is the fast lint / format / static set, *not* tests. A green gate
    is necessary but not sufficient for CI green: CI runs the test suite as a
    separate required check, so run the repo's `test` verb yourself before
    pushing.

    The gate is ONE definition (`lefthook.yml`, composed from fragments —
    [#5.2]) run everywhere: session start arms it, local commits run it, CI runs
    the same `lefthook run pre-commit --all-files`. To add or change a check,
    edit a fragment — never hand-copy a check into a CI job. See the "ONE gate,
    run everywhere" rule in CLAUDE.md.

3. The PR cycle, driven by the state machine

    The PR loop is not a checklist an agent eyeballs — it is a state machine.
    `release-core pr status` (flat alias `gh-task-status`) reads the live PR and
    returns a state plus the single next action. It never mutates the PR; the
    agent acts on the next action, then re-reads. The surface is
    reviewer-agnostic: reviews are handled identically whether the reviewer is
    a human or any bot — bot names and mechanics live only in the adapter
    registry behind `pr review`. The done-signal for a review round is zero
    unresolved review threads, never a per-bot comment count.

    The states:
        - NO_PR — no PR for this branch; open a draft to start.
        - REVIEWS_PENDING — required reviewer(s) not done; request or wait.
        - ADDRESSING — reviews in, open threads remain; fix-or-reply, then
          resolve.
        - REVIEWED — reviews + threads done; mergeability still computing,
          re-check shortly.
        - VALIDATING — reviewed + mergeable; CI checks running, wait.
        - READY — reviewed + CI green + mergeable; flip draft→ready
          (`release-core pr ready`) and page the human.
        - BLOCKED — merge conflict, failing CI, or the loop's circuit breaker
          fired; stop and surface to the human.

    The happy-path command sequence for one PR:
        - `release-core pr status` — where am I?
        - `release-core pr review request` — request the required review(s)
          (REVIEWS_PENDING). Reviewer-agnostic: it dispatches through the
          adapter registry, defaulting to all required reviewers
          (`--reviewer <name>` selects one). It verifies the attach: exit 0
          means the review_requested edge exists (or a fresh review already
          consumed it); a request GitHub silently drops fails loud.
        - `release-core pr wait` — the ONE engine-driven wait (it replaced
          both `pr review wait` and `pr checks-wait`). Blocks in-turn, polls
          the engine with adaptive cadence, and returns as soon as the agent
          has something to do (exit 0 = action available, 2 = timeout;
          `--poll` / `--timeout` tune it).
        - `release-core pr status` — now ADDRESSING; triage the threads.
        - `release-core pr resolve-thread` — resolve addressed threads.
        - `release-core pr wait` — if VALIDATING, block until CI resolves.
        - `release-core pr ready` — at READY, the guarded flip: it refuses
          (exit 1, printing state + next action) unless the engine says READY,
          and `--undo` flips back to draft for re-work. The only sanctioned
          way to flip the flag — never raw `gh pr ready`. Then hand to the
          human.

    This whole loop is the `gh-pr-review-loop` skill's discipline; drive it
    through the skill, not by hand-composing the helpers (a PreToolUse guard
    enforces this — see CLAUDE.md and `dev-cycle.lex`).

    :: warning :: `pr wait` blocks in-turn — it is how an agent waits on
    reviews or CI without yielding. A subagent that yields to a background
    monitor terminates and is never re-woken. Drive the loop through
    `pr status` (state + next action) and block with `pr wait`; do not hand
    the wait to a detached background process.

4. Install — the pull model

    Install is a pull model (ADR-0003), run by `bin/install-release-core` at
    every session start:

    - It resolves exactly one `release_core-*.whl` asset from a GitHub release —
      `releases/latest` by default, or the latest in a pinned major line with
      `--major vN` (the safety filter, since the wheel's own version string is
      static).
    - It installs with `pip install --force-reinstall` (NOT `-U`, which would
      see the static version as already-satisfied and skip). Dependencies (e.g.
      `click`) resolve from PyPI — the wheel declares real deps.
    - It then runs a bare `release-core init` in the repo (best-effort).

    So a consumer gets a NEW `release_core` automatically on its next session
    start (or next CI run) — there is nothing to commit to update the engine.
    The pull model keeps consumers on the always-stable tip without per-repo
    bump PRs.

    Almost nothing the wheel carries is committed in the consumer (epic #501,
    ADR-0005 — shipped through WS7, #528). What a consumer's git tree holds is
    only the irreducible set:

    - `.github/**` — the thin `@vN` workflow callers + GitHub-forced policy
      files (real copies; GitHub reads them from the tree).
    - The bootstrap quartet, as REAL tracked files (WS5, #526):
      `.claude/settings.json`, `bin/install-release-core`,
      `bin/setup-dev-env.sh`, `bin/pr-loop-guard` — the boot chain can't
      depend on what it boots.
    - Optionally `.release-sync.yaml` — the one per-repo knob (component
      override).

    Everything else is EPHEMERAL, recomposed by every `init`: the `.release/`
    build dir is gitignored (WS4, #521), and the working-tree mirrors (the
    `bin/` task symlinks, `.editorconfig`, the distributed skill) are
    untracked, listed in a managed `.git/info/exclude` block (WS7, #528).
    Nothing can fall out of sync by construction — there is nothing tracked to
    fall out of sync.

    The managed AUTO-COMMIT therefore fires only for real-file changes (the
    quartet, workflow copies, the CLAUDE.md managed block) and for one-time
    migrations (untracking what an older seed committed; removing retired
    files, #527). Pathspec-scoped, never `git add -A`, byte-identical → no
    commit. `--no-commit` skips it (CI); `--push` additionally fast-forwards
    on a clean default branch.

5. Distribution — the compose engine (init → ephemeral tree + mirrors)

    `release-core init` is the one builder (the standalone `release-sync`
    verb and the `release-drift-check` subsystem were retired in WS4, #521 —
    with the tree ephemeral there is nothing to fall out of sync). The engine
    lives in `release_core/sync.py` (`build_plan` / `materialize` /
    `compute_mirror`);
    init is its only driver. Read this before adding anything consumers should
    receive.

    The guiding rule: a consumer repo contains as little release-owned state
    as possible, and nothing mixed-ownership. The wheel carries it; files
    appear at session start and never enter git.

    5.1. What init composes

        Three template subtrees, low to high precedence (last write wins):

        - `templates/commons/` — the universal set; every consumer.
        - `templates/components/<component>/` — one per Component the
          consumer declares (Kind manifest default, or a `.release-sync.yaml`
          override).
        - `templates/<kind>/` — the consumer's Kind subtree.

        A file's destination is its source path minus the subtree prefix:
        `templates/commons/bin/check-shell` → `.release/bin/check-shell`.
        `lefthook.yml` is the one composed file — deep-merged from each
        subtree's `lefthook.fragment.yaml` in precedence order; to change a
        check, edit a fragment, never a consumer's gate. Skills ride the same
        mechanism, whole-directory (catalogs in `harness.lex`).

        The source is the wheel's offline bundle (no network, no clone); a
        `$RELEASE_HOME` checkout overrides it for release-dev.

    5.2. Where things land

        Four placement classes, decided by the engine:

        Ephemeral build dir (`.release/`):
            The whole composed tree, gitignored via a self-ignoring
            `.release/.gitignore`, recomposed every init. The gate config
            (`lefthook.yml` + lint/format configs) lives ONLY here; tools get
            it via explicit `--config`/`--rcfile`/`-c` paths (WS3, #524).

        Ephemeral mirrors (untracked symlinks):
            Working-tree symlinks into `.release/` for files tools expect at a
            location — the `bin/` task verbs, `.editorconfig`, the distributed
            skill. Never tracked; init lists them in a managed block in
            `.git/info/exclude` so `git status` stays clean (WS7, #528). A
            conflicting REAL file at a mirror dest is surfaced, not excluded.

        Real tracked copies (`needs_real_file`):
            `.github/workflows/*` (GitHub reads the tree directly; a symlink
            blob is the literal target string) and the bootstrap quartet
            (WS5, #526 — must exist on a fresh clone, before any init ran).
            Copies carry a managed-marker header so stale ones are swept.

        The CLAUDE.md stub:
            A marker-delimited block injected at the top of the consumer's
            CLAUDE.md, pointing at `release-core how-to` (`harness.lex` §2).

    5.3. Cleanup is part of the compose

        Every init also removes what no longer belongs:

        - Broken/de-mirrored symlinks: any `.release/`-pointing symlink whose
          dest this compose does not mirror is swept (and its emptied skill
          dir pruned).
        - Stale managed copies: marker-carrying workflow files absent from the
          plan.
        - Retired files (WS6, #527): real files release once
          distributed and has since retired (`bin/check-fmt`,
          `bin/changelog*`, the old `bin/release` caller,
          `.release-sync-state.yaml`), removed only under provenance (exact
          historical blob, managed marker, or verbatim header) so consumer-
          authored work is never touched.
        - One-time untracking migrations: a pre-WS4 committed `.release/` or
          pre-WS7 committed mirrors are untracked in one managed commit, with
          the ephemeral content kept live on disk.

    5.4. The single-home pattern for tools

        A tool that ships to consumers has its single source of truth inside
        `templates/commons/bin/` (or the relevant subtree) — never repo-root
        `bin/`. The maintainer gets it on `$PATH` because repo-root
        `bin/<tool>` is a symlink into the template. A real file at repo-root
        `bin/` is by definition maintainer-only.

        Python tooling (`changelog`, `semver`, `detect-kind`,
        `gh-task-status`, `gh-release-issue`, and `release-core` itself)
        ships as pip console-scripts from the wheel — never synced scripts
        (#476).

    :: note :: To make something reach consumers: put its single source copy under
    a template subtree, decide its placement class (prefer binary output >
    ephemeral > tracked), and add a `tests/release-sync/` case plus a
    `test_core_sync.py` lock.

6. Reusable workflows

    `release/` ships reusable GitHub Actions workflows: one shared pipeline
    per artifact category. A consumer doesn't copy a pipeline — it calls one
    with a thin `with:` block. The logic stays here; the consumer's workflow
    file is a few lines.

    6.1. How a consumer uses them

        A consumer's `.github/workflows/*.yml` is a thin caller (synced as a
        real copy — [#5.4]). It references a reusable workflow here by version:

            jobs:
              release:
                uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
                with:
                  version: ${{ inputs.version }}
                secrets: inherit
        :: yaml ::

        The pin is a floating major: `@v2` always points at the latest
        non-breaking tag in that line, so patch and minor fixes here reach every
        consumer on their next run with nothing to edit. An exact pin (`@v2.1.3`)
        is available when a consumer needs to freeze. A breaking change cuts a
        new major and every consumer's thin caller must be bumped deliberately —
        the cost the versioning contract is designed to make rare (see CLAUDE.md
        and the README versioning table).

        :: note :: Cross-org consumers (lex-fmt/*) cannot use `secrets: inherit`
        — that only propagates within one owner. They must list each secret
        explicitly and may need name mapping. `arthur-debert/*` consumers
        inherit.

    6.2. CI gate vs release pipeline

        Most categories come as a pair:

        - `<category>-ci.yml` — the PR/push gate. Runs the test/lint/build
          checks (almost always by invoking the repo's gate, the same lefthook
          gate). This is the required check on a PR.
        - `<category>.yml` — the full release pipeline. Build + sign + publish +
          tag, run on a release trigger, not on every push.

        Examples of the pairing: `rust-ci.yml` / `rust-cli.yml`, `go-ci.yml` /
        `go-cli.yml`, `tauri-ci.yml` / `tauri-app.yml`, `electron-ci.yml` /
        `electron-app.yml`.

    6.3. The catalog

        Runnable CLIs (build, sign, publish binaries):
            - `rust-cli.yml` — Rust CLI binaries across macOS/Linux/Windows;
              crates.io publish + brew formula.
            - `go-cli.yml` — Go CLI binaries, cross-compiled; ships from git
              tags (no registry), optional brew.

        Libraries (registry only, no binaries):
            - `rust-lib.yml` — publish library crates to crates.io,
              workspace-aware in topological order.
            - `python-pkg.yml` — build with `uv` and publish to PyPI / TestPyPI.

        GUI apps (release artifacts, signing):
            - `electron-app.yml` — electron-builder; macOS sign + notarize; GH
              release with .dmg / .AppImage.
            - `tauri-app.yml` — Tauri 2.x across the platform matrix; sign +
              optional notarize; bumps the three version files atomically.

        Editor extensions:
            - `vscode-ext.yml` — vsce package; Marketplace + optional Open VSX.
            - `zed-extension.yml` — wasm32-wasip2 build; bundles wasm + sources.
            - `nvim-plugin.yml` — source-only; validate, changelog, tag, release
              (the tag IS the version).

        Other stacks:
            - `tree-sitter.yml` — bundle .wasm + grammar + queries; optional npm
              publish.
            - `gh-action.yml` — composite actions + reusable workflows; validate,
              changelog, tag, advance the floating-major branch.

        Docs sites (deploy to GitHub Pages on push to main):
            - `mdbook.yml` — build an mdBook site.
            - `mkdocs.yml` — build a MkDocs site (`--strict`).

    6.4. Shared and infra workflows

        Reusable (a consumer may call these):
            - `copilot-review.yml` — request a Copilot review on a PR. Needs a
              user PAT secret, since the Actions bot silently no-ops otherwise.
            - `cascade-handler.yml` — cross-repo release cascades; computes a
              version bump from commits and dispatches a consumer's release.
            - `bats-e2e.yml` — run a consumer's BATS e2e suite.
            - `tauri-e2e.yml` — run a consumer's Tauri e2e suite.

        Internal to this repo (its own CI; not for consumers):
            - `ci.yml` — this repo's CI; dogfoods `gh-action-ci.yml` and runs the
              lefthook gate.
            - `release.yml` — this repo's own release; dogfoods `gh-action.yml@v2`
              and publishes the `release_core` wheel as a release asset. This is
              the supply side of the pull-model boot ([#4]): this repo publishes
              the wheel, and every consumer's `install-release-core` pulls it.
            - `copilot-review-self.yml` — applies `copilot-review.yml` to this
              repo's own PRs without leaking the token to forks.
            - The `*-tests.yml` suites — `changelog-tests`, `release-sync-tests`,
              `release-cut-tests`, `release-lex-tests`,
              `install-release-core-tests`, `pip-bootstrap-smoke`,
              `provision-gate-toolset-tests`, `audit-tests`. They verify the
              tooling here before it is synced out, and are not called by
              consumers.

7. orc — the maintainer orchestrator

    `orc` is a maintainer-only fleet orchestrator that runs FROM
    `arthur-debert/release`. It is not consumer-facing and is never synced; it
    lives in this repo's `bin/` and resolves its `uv` workspace (it depends on
    `release_core`). Its jobs:

    - `orc run <repo> <prompt>` / `resume` — open or continue an LLM session
      against a consumer repo.
    - `orc probe <repo> <prompt>` — spin a fresh subordinate agent to evaluate a
      (throwaway) consumer clone.
    - `orc watch <pr>` — poll PR lifecycle state and act on transitions.
    - `orc sessions list|clear` — manage stored sessions.

    The fleet loop is PULL-only: cut a release (`release.yml` publishes
    the `release_core` wheel) → `release-core admin release advance-major`
    (fast-forward the floating major). Run `release-core admin repos verify`
    (hermetic pre-flight) before advancing. Consumers self-update at their next
    SessionStart — `install-release-core` pulls the wheel and a bare
    `release-core init` rebuilds the whole managed tree — so there is NO
    push step. (Seeding a pre-pull consumer is a one-time `bash
    bin/install-release-core` run in that repo, then open the resulting
    managed-sync PR — one repo at a time.) For the full principle and the
    upstream-vs-consumer routing rule, see the `release-fleet-ops` skill.

8. Roadmap (directional, not a task tracker)

    The GitHub issue tracker is the task list. This is the directional arc — the
    why, not the what's-next.

    Done:
        - Vocabulary + sync redesign: settled on Kind, Component, and Consumer
          (Stack and client were the older names); the build-dir + symlinks
          architecture (ADR-0001/0002) that makes removals and renames
          detectable instead of lingering.
        - Reliable dev cycle (epic #332): the reviewer-agnostic `gh-task-status`
          state engine (`release_core.prstate`) + `orc watch`, distributed and
          review-hardened. Residual in #349 (orc watch live shake-out), #350
          (cloud transport).
        - Pull-model distribution (#416): the wheel is the carrier; consumers
          self-update at SessionStart. No push mechanism.

    Done (continued):
        - Self-improving feedback loop (epic #348, closed): consumer
          orientation via the CLAUDE.md stub + `how-to`; the escalation
          contract (`release-core issue file`) and the maintainer inbox
          (`admin inbox` / `notify-source`).
        - Minimal footprint (epic #501, ADR-0005, closed 2026-06): the wheel
          is the sole carrier. `.release/` ephemeral (WS4), bootstrap quartet
          as real files (WS5), retired-file cleanup (WS6), ephemeral
          mirrors + the machinery keep/fold/drop (WS7,
          `docs/references/self-improving-machinery.md`). The old sync and
          out-of-sync-check subsystem is gone; a consumer tracks only the
          irreducible set.

    In progress:
        - Dev-cycle fine-tuning (epic #547): thread-state-driven done-signal,
          state-engine-owned waits and draft↔ready, then fresh-agent
          validation rounds on the minimal footprint.

    Later:
        - Architectural fine-tuning: cleaner separation of build / pack / sign /
          publish; research into goreleaser-style tooling.
        - Secret management (Doppler), Windows build/sign, broader package-
          manager support, Claude Cloud hardening.
