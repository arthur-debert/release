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

Deeper References:
    The documents above are the orientation layer. Below is the durable
    reference material they sit on top of — the "why" and the deep mechanism.

    status.lex:
        The directional roadmap and current project state. Explicitly not a
        task tracker (the GitHub issue tracker is that).

    distribution.lex:
        The deep `release-sync` mechanism — the symlink-vs-copy predicates,
        the canonical-home pattern, the worked changelog-shim example. Where
        injected-files.lex is the map, this is the internals.

    breaking-changes.md:
        The versioning contract's breaking-changes log.

    adr/:
        Architecture Decision Records — the build-dir+symlinks model (0001),
        the provenance marker (0002), the pip-install bootstrap (0003), the
        installed-package symlinks (0004).

    references/:
        Design "why" notes — the component model, the lint-debt three-case
        model, release-token setup, fleet telemetry via issues.

    dev/:
        Maintainer tool-base docs — fleet tooling, fleet-propagate worktrees,
        the release-core CLI authoring pattern.

    per-category/, per-component/, per-stack/:
        Per-artifact input shapes and adoption guides for each Kind and
        Capability. artifacts-schema.md and lex-release-cascade.md round these
        out.

Reading Order:
    - New to the repo: start with tooling.lex, then injected-files.lex.
    - Doing a task in a consumer repo: dev-cycle-task.lex + tooling.lex.
    - Working on `release/` itself: all of the above, plus the ADRs under
      `adr/` and the deep mechanism in distribution.lex.
