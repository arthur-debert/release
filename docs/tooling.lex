release-core

    `release-core` is the one CLI for every infrastructure and dev-cycle task:
    linting, the changelog, syncing managed files, driving a PR, cutting a
    release. It is a `click` command tree, so the help IS the map — an agent
    learns it by discovery, not by memorizing:

    - `release-core --help` — the top-level groups.
    - `release-core <group> --help` — a group's subcommands.
    - `release-core <group> <command> --help` — one command's flags.

    The tree is rich; you are not expected to know it cold. When in doubt,
    `--help` your way down. The flat maintainer command names that used to
    exist (`release-verify-fleet`, `managed-repos`, `release-cut`, …) were
    retired in the CLI cutover — use `release-core <group> <command>`. A few
    flat consumer aliases remain (see §3).

1. The Shape of the Tree

    Consumer-facing (run from inside any repo):
        - `release-core init` — DEFAULT (post-#476): materialize the WHOLE
          managed tree from the wheel bundle (the `.release/` build dir + every
          working-tree mirror — skills, ORIENTATION, configs, the CLAUDE.md
          block) and auto-commit any managed change. This is "release-sync
          sourced from the wheel"; it is what SessionStart runs, carrying the
          full tree so no `orc propagate` push is needed in steady state.
          `--config-only` is the escape hatch — materialize just the config
          subset (lefthook.yml + lint configs), where `--commit` stages and
          commits just those paths. (`--full` is a redundant alias of the
          default.)
        - `release-core sync run` / `sync drift-check` — materialize the
          `.release/` tree (see injected-files.lex) / fail if it has drifted.
        - `release-core changelog add|cut|render` — manage the changelog.
        - `release-core semver validate|get` — validate or read a version part.
        - `release-core detect-kind` — report this repo's release Kind.
        - `release-core cut` — cut a release for this repo.
        - `release-core audit` — audit this repo's release posture.
        - `release-core issue file <component> "<symptom>"` — escalate infra
          friction upstream to `arthur-debert/release`.
        - `release-core pr …` — the PR-loop helpers (see §2).
        - `release-core ci fetch-deps|fetch-artifact` — CI-glue fetch helpers.

    Maintainer-only (run from inside `arthur-debert/release`):
        - `release-core admin repos list|prs|audit|verify` — fleet views and
          the hermetic pre-flight sweep.
        - `release-core admin release advance-major|betas|lex` — release-side
          mechanics; `advance-major` fast-forwards the floating major branch.
        - `release-core admin policy ruleset|sweep|dependabot` — GitHub policy.
        - `release-core admin secrets install|token` — provision release
          secrets onto a repo.
        - `release-core admin inbox [notify-source]` — the consumer-filed
          issue triage inbox and the close-the-loop notifier.

2. The PR Cycle, Driven by the State Machine

    The PR loop is not a checklist an agent eyeballs — it is a state machine.
    `release-core pr status` (flat alias `gh-task-status`) reads the live PR and
    returns a state plus the single next action. It never mutates the PR; the
    agent acts on the next action, then re-reads.

    The states:
        - NO_PR — no PR for this branch; open a draft to start.
        - REVIEWS_PENDING — required reviewer(s) not done; request or wait.
        - ADDRESSING — reviews in, open threads remain; fix-or-reply, then
          resolve.
        - REVIEWED — reviews + threads done; mergeability still computing,
          re-check shortly.
        - VALIDATING — reviewed + mergeable; CI checks running, wait.
        - READY — reviewed + CI green + mergeable; flip draft→ready and page
          the human.
        - BLOCKED — merge conflict, failing CI, or the loop's circuit breaker
          fired; stop and surface to the human.

    The happy-path command sequence for one PR:
        - `release-core pr status` — where am I?
        - `release-core pr copilot on` — request the Copilot review
          (REVIEWS_PENDING).
        - `release-core pr copilot wait` — block in-turn until the review
          lands.
        - `release-core pr status` — now ADDRESSING; triage the threads.
        - `release-core pr resolve-thread` — resolve addressed threads.
        - `release-core pr checks-wait` — if VALIDATING, block until CI is
          green.
        - `release-core pr status` — READY → flip draft→ready, hand to the
          human.

    :: warning :: The wait commands (`pr copilot wait`, `pr checks-wait`) block
    in-turn — they are how an agent waits on CI without yielding. A subagent
    that yields to a background monitor terminates and is never re-woken. Drive
    the loop through `pr status` (state + next action) and block with the wait
    commands; do not hand the wait to a detached background process.

3. The Flat Consumer Aliases

    A short list of flat command names is kept on PATH for consumers, each a
    thin alias into the tree:

        | Alias | Does |
        | changelog | manage the changelog (add/cut/render) |
        | semver | validate or extract a version part |
        | detect-kind | detect this repo's release Kind |
        | release-sync | materialize the .release/ tree + symlinks |
        | release-drift-check | fail if .release/ has drifted from source |
        | gh-task-status | the PR state machine (state + next action) |
        | gh-release-issue | file or comment on a release issue upstream |
        | release | shim → `release-core cut` |
    :: table ::

4. How release-core Is Installed, and What a Consumer Must Commit

    Install is a pull model (ADR-0003), run by `bin/install-release-core` at
    every session start:

    - It resolves exactly one `release_core-*.whl` asset from a GitHub release
      — `releases/latest` by default, or the latest in a pinned major line with
      `--major vN` (the safety filter, since the wheel's own version string is
      static).
    - It installs with `pip install --force-reinstall` (NOT `-U`, which would
      see the static version as already-satisfied and skip). Dependencies
      (e.g. `click`) resolve from PyPI — the wheel declares real deps.
    - It then runs a bare `release-core init` in the repo (best-effort).

    So a consumer gets a NEW `release_core` automatically on its next session
    start (or next CI run) — there is nothing to commit to update the engine.
    The pull model keeps consumers on the always-stable tip without per-repo
    bump PRs.

    What DOES ride in the consumer's git tree is the managed TREE — the
    `.release/` build dir + every working-tree mirror (skills, ORIENTATION,
    configs, the CLAUDE.md block). Since the #476 cutover, a bare `release-core
    init` materializes that whole tree from the wheel bundle and AUTO-COMMITS
    only the managed paths it touched (never `git add -A`), with a deterministic
    message, iff they actually changed — byte-identical → no commit, so churn
    tracks release cadence, not session count. This is the commit-hygiene closer
    for the pull model: the engine is pulled, the whole tree it generates is
    committed — so the wheel pull alone carries every managed change and no
    `orc propagate` push is needed in steady state. `--no-commit` skips the
    commit; `--push` additionally fast-forwards on a clean default branch.
    `--config-only` is the escape hatch (the old behavior): materialize just the
    config subset (`lefthook.yml` + lint configs), where `--commit`/`--force`/
    `--push` keep their opt-in create-if-absent semantics.

5. orc — the Maintainer Orchestrator

    `orc` is a maintainer-only fleet orchestrator that runs FROM
    `arthur-debert/release`. It is not consumer-facing and is never synced to
    consumers; it lives in this repo's `bin/` and resolves its `uv` workspace
    (it depends on `release_gh`). Its jobs:

    - `orc propagate` — run `release-sync` across the fleet and open a re-sync
      PR per consumer. Mechanical, no LLM calls. This is how an upstream fix
      reaches every consumer. Because the clones lack each consumer's
      toolchain, propagate commits `--no-verify` and the PR's own CI is the
      real gate.
    - `orc run <repo> <prompt>` / `resume` — open or continue an LLM session
      against a consumer repo.
    - `orc watch <repo>` — watch fleet / PR state.
    - `orc sessions list|clear` — manage stored sessions.

    The canonical fleet loop: `release-core admin repos verify` (hermetic
    pre-flight) → `orc propagate` (re-sync + PR per consumer) → `release-core
    admin release advance-major` (fast-forward the floating major). Always
    verify before advancing. For the full doctrine and the upstream-vs-consumer
    routing rule, see the `release-fleet-ops` skill.
