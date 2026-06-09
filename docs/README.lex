release/ — Agent & Maintainer Docs

    The documentation set for the release tooling: how it reaches a consumer
    repo, what an agent working in that repo sees, and how the day-to-day
    development cycle runs. Written for two readers: an agent doing tasks in a
    consumer repo, and a maintainer working on `release/` itself.

    The procedural source of truth for "how do I work in THIS repo" is the
    binary, not these files: `release-core how-to` renders it, version-correct,
    kind-aware (release#501, "invoke, don't discover"). These docs are the
    durable design/mechanism layer behind that.

The four narrative documents:
    dev-cycle.lex:
        The ONE development life cycle, draft-first — a simple task (one PR) and
        a larger feature (multiple PRs under a coordinating agent). The model
        behind `release-core how-to` and the `gh-pr-review-loop` skill.

    tooling.lex:
        The machinery: `release-core` (the CLI an agent drives), the quality
        gate, the PR state machine, the pull-model install, the `release-sync`
        distribution engine (build-dir + symlinks, what lands where), the
        reusable workflows, the maintainer `orc` orchestrator, and the
        directional roadmap.

    harness.lex:
        The agent harness: the SessionStart bootstrap, how orientation reaches
        the agent (the CLAUDE.md managed block → `release-core how-to`), and the
        skill set — where skills live, the distribution tiers, and linting.

    README.lex:
        This file — the map and reading order.

Kept references (the durable "why"):
    adr/:
        Architecture Decision Records — the build-dir + symlinks model (0001),
        the provenance marker (0002), the pip-install bootstrap (0003), the
        installed-package symlinks (0004), the minimal-footprint /
        invoke-don't-discover decision (0005).

    breaking-changes.md:
        The versioning contract's breaking-changes log.

    references/:
        Design "why" notes — the component model, the lint-debt three-case
        model, release-token setup, fleet telemetry via issues.

    dev/:
        Maintainer tool-base docs — fleet tooling and the release-core CLI
        authoring pattern.

Reading order:
    - New to the repo: start with tooling.lex, then harness.lex.
    - Doing a task in a consumer repo: run `release-core how-to`, then read
      dev-cycle.lex.
    - Working on `release/` itself: all four narrative docs, plus the ADRs under
      adr/.

Note on per-Kind docs:
    The former `per-category/`, `per-component/`, and `per-stack/` reference
    docs were removed in the #501 consolidation. Per-Kind "how to build/test
    this" content is now `release-core how-to <kind>` OUTPUT, not a
    hand-maintained file — the same invoke-don't-discover principle applied to
    docs.
