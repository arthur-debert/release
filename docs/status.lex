Status and Roadmap

Getting here was a long and error-prone path while we experimented and dealt with many communication hurdles. This high level overview provides a directional roadmap; it should not be used as a task tracker.

1. Documentation Overhaul (this effort)

    Provide clear and agreed-on terminology, goals, and vision. Canonical vocabulary lives in CONTEXT.md. Key renames: Stack to Kind, Component to Capability, client repo to Consumer.

2. release-sync Redesign

    The current sync only handles additive changes. Removals and renames linger in consumer repos indefinitely, creating fleet divergence that compounds over time.

    The redesign (see docs/adr/0001-release-sync-build-dir-with-symlinks.md) moves to a build-directory architecture: all managed files live in .release/, rebuilt from scratch every sync. Consumer locations are symlinks into .release/. Removals become broken symlinks, trivially detectable and cleanable. No state file, no removal manifest.

    The Kind rename (detect-stack to detect-kind, templates/rust/ to templates/rust-cli/, etc.) ships as part of this redesign so consumers get the new layout and new names in one migration.

    This supersedes issue 274 (cleanup stale check-fmt). If not fully covered, finish manually.

3. Core Capabilities

    With sync working cleanly, ensure core capabilities (quality check, build, release) work for all consumers by leveraging shared infrastructure. Only genuinely app-specific needs should override them.

    3.1. Executables

        Each consumer should have:
        - bin/ containing only symlinks to release-provided tools via .release/
        - app-bin/ containing repo-specific things (lifecycle hooks, generators), never shadowing core capabilities
        - scripts/ fully removed (legacy ad hoc solutions)

    3.2. Missing Kind Templates

        - bin/release for each Kind (currently bespoke shims in consumers, all identical thin callers of release-cut).
        - fetch-deps: support platform-templated dest paths (issue 271), removing app-bin hacks for artifact fetching.

    3.3. Shared Workflows and Actions

        With executables working, ensure consumers use shared workflows for CI and release rather than reimplementing logic in workflow YAML or calling scripts directly.

    This is the first big milestone: full adoption of release across all consumers.

4. Development Cycle

    The PR flow is unreliable in both local and cloud environments. Agents cannot reliably request reviewers, detect completed reviews, resolve comments, or determine when a PR is mergeable. See docs/foundational-gh-layer.lex for the full analysis.

    4.1. gh-task-status: a single command that tells the agent where it stands in the PR lifecycle. Orchestrates existing primitives (gh-copilot-wait, gh-pr-resolve-thread, gh-pr-checks-wait) rather than replacing them.

    4.2. Event listener: routines or equivalent that processes PR events and notifies the agent, replacing brittle polling.

    This is the second big milestone: reliable, verified tooling and a consistent development cycle across environments.

5. Self-Improving Feedback Loop

    5.1. Consumer CLAUDE.md instructions: release manages infrastructure; in case of a problem, the consumer opens an issue on the release tracker and works around it.

    5.2. Batch processing: runs (first manually triggered) to summarize and prioritize issues from consumer repos, then release fixes and notifies consumers via their trackers.

    5.3. CI failure analysis: tools to gather warnings and failures across the fleet for bug fixing and improving brittle features.

6. Architectural Fine Tuning

    See docs/foundational-arch-review.lex. The current cut of Kinds and Capabilities is less decoupled than it needs to be. Building, packing, signing, and publishing should be more clearly separated and composable.

    Research into existing ecosystem tools (goreleaser-style solutions) for common use cases like cross-platform CLI distribution and desktop app packaging.

7. Later Phases

    7.1. Secret Management: formalize per docs/foundational-secret-management.lex. Doppler integration, automated population of GitHub repo secrets.

    7.2. Missing Features:
        - Windows support for build and release
        - Windows signing
        - macOS signing and notarization
        - Homebrew and other package manager support

    7.3. Claude Cloud Redux: review and test all work in the Claude Cloud environment.
