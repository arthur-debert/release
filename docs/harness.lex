The Agent Harness

    What an agent sees when it lands in a consumer repo, and how `release/`
    puts it there: the session-start boot, the orientation that tells the agent
    how this repo works, and the skill set it can invoke. This document absorbs
    the former `skills` doc and the agent-facing half of `injected-files`.

    The machinery that materializes these files (the sync engine, the gate, the
    CLI) lives in `tooling.lex`. The dev cycle the agent then follows lives in
    `dev-cycle.lex`.

1. Session-start bootstrap

    `.claude/settings.json` wires a `SessionStart` hook that runs
    `bin/setup-dev-env.sh`. That script is the single definition of how a repo
    boots, for both local dev and cloud sessions. Order:

    - §0 — install the gate toolset (lefthook, ruff, yamllint, shellcheck,
      markdownlint, and language linters as detected). Best-effort to install,
      but the gate itself is HARD: a missing tool fails the commit, never skips.
    - §0.1 — wire the pre-commit hook (`lefthook install`).
    - §0.2 — the pull-model boot (ADR-0003): run `bin/install-release-core`,
      which resolves the `release_core` wheel from a GitHub release, `pip install
      --force-reinstall`s it (deps from PyPI), then runs a bare `release-core
      init`. That init materializes the WHOLE managed tree from the wheel bundle
      (the `.release/` build dir + every working-tree mirror — skills,
      ORIENTATION, configs, the CLAUDE.md block) and auto-commits any managed
      change. This is the pull-model self-sync: the wheel pull carries the whole
      tree and there is no push step. (`release-core init --config-only`
      refreshes just the lint/gate config subset — the escape hatch.)
    - §1+ (cloud only) — submodule/tag restore, dependency cache warm-up, venv
      setup, CA cert import, optional per-repo `app-bin/post-setup-hook.sh`.

    What must exist before a session starts: the bootstrap entry points
    themselves — `bin/setup-dev-env.sh`, `bin/install-release-core`, and
    `.claude/settings.json`. They ship in the committed bootstrap (synced from
    `templates/commons/bin/`), so a freshly cloned consumer can boot before
    `release_core` is installed. Everything else (the gate tools, the
    `release_core` package, the cloud dependency caches) is pulled or installed
    at session start.

    :: note :: The `release_core` Python package is NOT a synced file. It ships
    as a wheel from a GitHub release and is pip-installed at boot. The synced
    surface is the thin bootstrap + configs + workflow callers; the engine
    arrives out-of-band (tooling.lex §4). Its console-scripts — `changelog`,
    `changelog-add`, `changelog-cut`, `changelog-render`, `semver`,
    `detect-kind`, `release-sync`, `release-drift-check`, `gh-task-status`,
    `gh-release-issue` — land on PATH from the wheel; they are no longer synced
    `bin/` shims (release#476).

2. Orientation — how the agent learns this repo

    The agent's single source of "how do I lint / test / build / release / run
    *in this repo*, and what's the dev cycle" is `release-core how-to`. It is
    kind-aware and rendered from the binary, so it is always version-correct and
    cannot drift (release#501, "invoke, don't discover"). It is the one home for
    the dev-cycle text — kept in lockstep with `dev-cycle.lex` and the
    `gh-pr-review-loop` skill.

    Orientation reaches the consumer agent through a small managed block injected
    at the top of the consumer's `CLAUDE.md`:

        <!-- BEGIN release-managed orientation -->
        @.release/ORIENTATION.md
        <!-- END release-managed orientation -->
    :: text ::

    It is a block, not a whole file, because half the fleet already owns a
    `CLAUDE.md` with its own project content; sync injects or refreshes the block
    and leaves the rest untouched. If `CLAUDE.md` is itself a symlink, sync
    leaves it alone. The block `@`-imports `.release/ORIENTATION.md`, which is
    the orientation text (what's managed, how to open an issue, where to
    escalate) and lives only inside `.release/`.

    :: note :: Direction (epic #501, WS2): the synced `ORIENTATION.md` + the
    injected block collapse to a stable 2-line stub pointing at `release-core
    how-to`, so the procedural truth lives in exactly one place — the binary.
    This doc describes the orientation surface as it ships today (the managed
    block + `.release/ORIENTATION.md`); `release-core how-to` is already the
    canonical text both defer to.

3. Skills

    Claude Code skills are markdown playbooks an agent can invoke by name. This
    repo is the single home for the skills used across the fleet: it stores them,
    lints the ones we own, and distributes the consumer-facing ones into consumer
    repos.

    3.1. Where skills live

        Every skill lives in exactly one place: the `skills/` directory at the
        root of this repo. One directory per skill, each with a `SKILL.md`
        (frontmatter `name` + `description`, then the playbook body). There is no
        second copy in `templates/` — one source, no drift.

        Two provenances share that directory, told apart by an `.upstream` marker
        file inside the skill's dir:

        Self-authored:
            No `.upstream` marker. We own these; they MUST pass markdownlint.
            Examples: `gh-pr-review-loop`, `pr-review-respond`,
            `release-fleet-ops`, `release-fleet-triage`, `release-issue-relay`,
            `lex-primer`, `lex-multirepo`, `gh-repo-setup`,
            `migrate-consumer-to-build-dir`, `macos-signing-notarization`,
            `electron-e2e-testing`, `padz-for-agents`.

        Vendored:
            Carry an `.upstream` marker recording the source repo and pinned
            commit (most are from `mattpocock/skills`). Exempt from our lint —
            they are not ours to reformat. Examples: `diagnose`, `tdd`, `review`,
            `triage`, `qa`, `handoff`, `grill-me`, `grill-with-docs`,
            `to-issues`, `request-refactor-plan`,
            `improve-codebase-architecture`, `ubiquitous-language`, `zoom-out`,
            `teach`, `setup-matt-pocock-skills`.

    3.2. Ownership policy

        release/ is the single source of truth for infrastructure and general
        development-cycle skills. Every consumer repo carries release's official
        set, synced (never hand-copied) as symlinks into its `.release/` build
        tree. A consumer owns ONLY its own application-domain skills — anything
        specific to that project's subject matter.

        Why: a hand-copied infra skill drifts. We found a consumer running a
        stale, much-shortened `pr-review-respond` against release's official copy
        because nothing kept the local copy in step. The distribution mechanism
        ([#3.4]) closes that gap — the consumer's copy is a symlink to the synced
        official blob, so it cannot fall behind.

    3.3. The three distribution tiers

        Every skill under `skills/` falls into exactly one tier. The catalogs are
        the literal lists `PUSH_ALL_SKILLS` and `REPLACE_IF_PRESENT_SKILLS` in
        `templates/commons/lib/release_core/release_core/sync.py` — that file is
        the authority; the names below are a convenience snapshot.

        Push-all (synced to EVERY consumer, unconditionally):
            The PR loop, review-response, upstream escalation, and the general
            development-cycle skills an agent needs anywhere. Currently:
            `gh-pr-review-loop`, `pr-review-respond`, `release-issue-relay`,
            `diagnose`, `tdd`, `review`, `triage`, `to-issues`, `handoff`, `qa`,
            `grill-me`, `grill-with-docs`, `improve-codebase-architecture`,
            `request-refactor-plan`, `ubiquitous-language`, `zoom-out`, `teach`,
            `padz-for-agents`.

        Replace-if-present (upgrade-only):
            Synced into a consumer ONLY when that consumer already carries
            `.claude/skills/<name>` (a real dir OR a symlink). The sync upgrades
            the existing copy to release's official; it never ADDS the skill to a
            consumer that lacks it. Currently: `lex-primer`, `lex-multirepo`,
            `electron-e2e-testing`, `macos-signing-notarization`.

        Release-only (NEVER distributed):
            Skills that only make sense while working ON this repo. They stay in
            `skills/` and never reach a consumer: `release-fleet-ops`,
            `release-fleet-triage`, `setup-matt-pocock-skills`, `gh-repo-setup`,
            `migrate-consumer-to-build-dir`.

        :: note :: The maintainer's own machine reaches the full set through the
        repo's `.claude/skills/`. That is a maintainer convenience, not the
        consumer distribution path.

    3.4. How a skill reaches a consumer

        Distribution is whole-directory and rides the same build-dir + symlink
        mechanism as every other injected file (tooling.lex §5). For each
        distributed skill, EVERY file under `skills/<name>/` is materialized — so
        a multi-file skill (e.g. `tdd`, `triage`) arrives complete, not just its
        `SKILL.md`.

        The path, per file under `skills/<name>/`:
            - Source of truth: `skills/<name>/<subpath>` in this repo.
            - `release-sync`, run in the consumer, writes the file as a real blob
              into the consumer's committed `.release/` build tree at
              `.release/.claude/skills/<name>/<subpath>`.
            - It then creates a relative symlink at the discovery path
              `.claude/skills/<name>/<subpath>` pointing back into `.release/`.
            - Claude Code discovers skills under `.claude/skills/`, follows the
              symlink, and reads it.

        If the consumer already has a REAL file or directory at a managed skill
        dest (a hand-copied skill from before this policy), the sync removes it
        and replaces it with the managed symlink — no `--migrate` flag needed.
        Skill dests are release-owned, so a stale hand-copy is always upgraded
        rather than flagged as a conflict.

        One source, one materialized copy, one symlink — no hand-copied skill
        files in the consumer, so nothing can drift out of step with upstream.

    3.5. Linting

        `bin-internal/lint-skills.sh` enforces the provenance split:
        self-authored skills (no `.upstream`) are markdownlinted; vendored skills
        (with `.upstream`) are skipped. The lefthook pre-commit gate and the CI
        `skill-lint` job both run it.

        When authoring a skill: write it, then run `bin-internal/lint-skills.sh`.
        When vendoring a skill: drop an `.upstream` marker (source repo + pinned
        commit) in its dir so the gate skips it.
