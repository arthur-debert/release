release/ — Agent & Maintainer Docs

    The documentation set for the release tooling: how it reaches a consumer
    repo, what an agent working in that repo sees, and how the day-to-day
    development cycle runs. Written for two readers: an agent doing tasks in a
    consumer repo, and a maintainer working on `release/` itself.

    The procedural source of truth for "how do I work in THIS repo" is the
    binary, not these files: `release-core how-to` renders it, version-correct,
    kind-aware (release#501 — discovery is the CLI, not docs). These docs are
    the durable design/mechanism layer behind that.

The four narrative documents:
    dev-cycle.lex:
        The ONE development life cycle, draft-first — a simple task (one PR) and
        a larger feature (multiple PRs under a coordinating agent). The model
        behind `release-core how-to` and the `gh-pr-review-loop` skill.

    tooling.lex:
        The machinery: `release-core` (the CLI an agent drives), the quality
        gate, the PR state machine, the pull-model install, the compose
        engine (`init` → ephemeral `.release/` + mirrors, what lands where),
        the reusable workflows, the maintainer `orc` orchestrator, and the
        directional roadmap.

    harness.lex:
        The agent harness: the SessionStart bootstrap, how orientation reaches
        the agent (the CLAUDE.md managed block → `release-core how-to`), and the
        skill set — where skills live, the distribution tiers, and linting.

    README.lex:
        This file — the map and reading order.

Kept references (the durable "why"):
    adr/:
        Architecture Decision Records — historical decisions, in order; 0005
        (minimal footprint; discovery is the CLI, not docs) is the current
        architecture. Earlier ADRs describe mechanisms 0005 superseded; they
        are records, not current design.

    references/:
        Design "why" notes that exist nowhere else — the component model
        (Kind/Component composition), the lint-debt three-case model, the
        RELEASE_TOKEN setup, and the WS7 self-improving-machinery
        keep/fold/drop decisions.

    artifacts-schema.md / lex-release-cascade.md:
        Live interface contracts — the cross-repo artifact-pin schema
        (read by `fetch-artifact`), and the lex multirepo release cascade.

    dev/:
        Maintainer tool-base docs — fleet tooling and the release-core CLI
        authoring pattern.

    Release history lives in CHANGELOG.md + git tags — there is no separate
    breaking-changes log (pull model: consumers follow `@vN`; a breaking
    change is a new major, coordinated when cut).

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
    hand-maintained file — the same "discovery is the CLI, not docs" principle
    applied to docs.
