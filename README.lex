Release

Release is the infrastructure manager for personal projects at arthur-debert and lex-fmt, some 12 projects comprised of 20 repos. It standardizes infrastructure and agentic workflows from beginning to end.

For domain vocabulary see CONTEXT.md. For project status and roadmap see docs/status.lex.

1. Offerings[1]

    1.1. Environment Provisioning: multi env: local, GitHub and Claude Cloud.

        1.1.1. OS level dependencies
        1.1.2. Language tooling (via package managers: npm, crates.io, pip)
        1.1.3. Secrets (via repo secrets or env vars)

    1.2. Software Development Tasks:

        1.2.1. Quality Check:
            1.2.1.1. Linting and Formatting
            1.2.1.2. Unit tests and E2E Tests
            1.2.1.3. Typically run on pre-commit hook and as a CI job.
        1.2.2. Building:
            1.2.2.1. Artifact fetcher: gets core binary or wasm for front end builds
            1.2.2.2. Target build per Kind (crates.io, Electron, Tauri)
        1.2.3. Releasing:
            1.2.3.1. Preparing Changelog
            1.2.3.2. Collecting build artifacts
            1.2.3.3. Signing binaries (i.e. macOS, VSCode)
        1.2.4. Publishing:
            1.2.4.1. Vertical package managers: npm, crates.io
            1.2.4.2. Distribution package managers: Homebrew, APT, Nix

    1.3. Agentic Development Harness:

        1.3.1. Development Lifecycle: implementation, review, approval, merging.
        1.3.2. Information: CLAUDE.md and similar.
        1.3.3. Tooling: skills

    1.4. Platform Policies:

        1.4.1. GH Policies: ensure a consistent ruleset for all repos, in a way that is easy to automate.

2. Goals

    - Provide a single unifying experience, behaviour and feature set across environments, as much as possible.
    - Provide a set of tool primitives that can be composed and used at higher level abstractions all the way through check, release, etc.
    - Provide custom made bespoke tools for agents to speed their work (i.e. working with PRs).
    - Provide a unified development lifecycle that leverages the best of agents and humans.
    - Provide each project with a rich set of capabilities without the effort (building binaries, macOS signing, etc).

3. Environments

    We support three environments with near-perfect parity:

    3.1. Local Workstation

        A macOS machine with Docker containers where needed. Where many agent sessions and humans interact.

    3.2. Claude Code Cloud

        A managed environment that runs our main agentic platform (Claude Code) on cloud instances. This environment has severe limitations:

        - The base image is not published; we know it contains packages for various language toolchains (npm, rust, python, etc) but not specialized ones.
        - The sandbox has limitations and a buggy GitHub MCP that impedes agents from doing things like resolving PR review comments and accessing cross-repo data.
        - No programmatic launching or configuration of instances; secrets and setup scripts are UI-bound.

    3.3. GitHub Actions (CI)

        This is not an environment in which we develop, but rather run jobs (check, release, build, agentic PR reviews, etc).

    For envs 1 and 2, the agent runs in a regular shell, while for GitHub Actions it runs as workflows. Some of these are triggered by events (i.e. a new commit/push) and others on demand (new release). We want the behaviors to match: workflows handle env vars, basic provisioning (checkout, deps), and calling other workflows, but core testing, building, and releasing must all happen via shell scripts the workflow calls, else implementations will drift.

4. Accessing Release Features

    Features are available in two forms:

    4.1. Files In Repo

        All release-managed files live in a build directory (.release/) in the consumer repo, checked into git. Consumer locations (bin/check, lefthook.yml, CLAUDE.md, etc.) are symlinks pointing into .release/. This makes the repo fully self-contained while keeping clear ownership of managed files.

        4.1.1. Executables:
            bin/: symlinks to release-supplied tools for tasks like check, build, release. Never repo or app-specific.
            app-bin/: repo-specific executables, like gen-theme.py (generates editor theme for a plugin), or lifecycle hooks like electron-after-pack.mjs.
        4.1.2. Configuration:
            lefthook.yml: quality check configuration composed from Capability fragments, runs on pre-commit hook.
            CLAUDE.md: instructions for the Claude agent.

    4.2. Shared GitHub Workflows and Actions

        These allow each consumer to use shared logic, with proper auth and env secrets managed by GitHub. They also provide shared actions for the rare case a repo needs a very different task.

        The shared workflows handle provisioning, secrets, and output formatting, but delegate core logic to the tool library, else a parallel implementation would drift.

    4.3. release-sync

        release-sync materializes the resolved set of files (from the consumer's Kind and Capabilities) into the .release/ build directory. It rebuilds from scratch every time: no state tracking, no incremental diffs. Symlinks at expected locations point into .release/; when a file is removed from templates, its symlink breaks and cleanup removes it.

        Consumers can override default Capabilities via a .release-sync.yaml at their repo root. A branch dial allows testing release changes before merging to main.

Notes

    1. This document lays the goal and vision. Not all features listed here are implemented yet, but this is the direction we are going for.
