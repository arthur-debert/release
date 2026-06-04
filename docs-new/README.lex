release/ — Agent & Maintainer Docs

    This is the rebuilt documentation set. It describes how the release
    tooling reaches a consumer repo, what an agent working in that repo sees,
    and how the day-to-day development cycle runs. It is written for two
    readers: an agent doing tasks in a consumer repo, and a maintainer working
    on `release/` itself.

The Documents:
    dev-cycle-task.lex:
        The development life cycle for a simple task — one that fits in a
        single PR. Information gathering, implementation, PR shepherding,
        user validation.

    dev-cycle-large-feature.lex:
        The life cycle for a larger feature — multiple PRs under one
        coordinating agent, branch, and umbrella PR. A composition of the
        single-task cycle.

    tooling.lex:
        What `release-core` is and the handful of commands an agent runs
        through a normal PR cycle, driven by the PR state machine. Also: how
        `release-core` is installed, what a consumer must commit to stay
        current, and what the maintainer-only `orc` orchestrator does.

    injected-files.lex:
        The map of every file `release/` writes into a consumer repo — where
        it comes from, where it lands, whether it is a symlink or a real copy,
        and which files must exist before a session starts.

    skills.lex:
        Which Claude Code skills exist, how they are stored and linted, and
        which ones reach a consumer-repo agent versus which stay in the
        release repo.

    workflows.lex:
        How the reusable GitHub Actions workflows work, the one-canonical-
        pipeline-per-category model, and the catalog of available workflows.

Reading Order:
    - New to the repo: start with tooling.lex, then injected-files.lex.
    - Doing a task in a consumer repo: dev-cycle-task.lex + tooling.lex.
    - Working on `release/` itself: all of the above, plus the ADRs under
      `docs/adr/` for the design rationale.
