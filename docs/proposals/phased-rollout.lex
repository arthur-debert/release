Phased Rollout:
Path from where we are to the full agentic dev workflow

Source location: arthur-debert/release, branch cloud, path docs/proposals/. Companion document: agentic-dev-workflow.lex captures the vision.

1. Scope and shape

    Each phase is a focused, self-contained chunk of work. Each ends in a stable state — the portfolio can pause indefinitely between any two phases without breakage. Phases mostly depend on earlier ones; several are parallel-doable. The ordering reflects priority and prerequisites, not strict serialization.

    Reference to the vision: see agentic-dev-workflow.lex, especially section 5 (open questions and rethinking) which this rollout addresses.

2. Phase 0: Capture and baseline

    No code changes. Intent capture and bookmarking only.

    2.1. Deliverables

        - This phased-rollout.lex document, alongside agentic-dev-workflow.lex
        - lex-primer skill imported into release/cloud/skills/
        - Tag phase-0-baseline on the current main HEAD
        - GitHub issue at release for Phase 1, linked to both proposal docs

    2.2. Why now

        The proposal docs become the source of truth for the next several months of work. The tag provides a return-to-this-state checkpoint if any rollout step needs to be unwound. The Phase 1 issue captures the first epic.

3. Phase 1: Unify and portable repo-setup

    Bring the cloud branch into main, and ship portable versions of the local-only repo-setup tooling.

    3.1. Deliverables

        - Cloud branch merged into main with --allow-unrelated-histories (additive merge; existing infra preserved)
        - gh-repo-setup skill: portable version of release/bin/apply-ruleset and release/bin/sweep-github-policy, runnable from any cloud session
        - release-issue-relay skill: portable version of release/bin/gh-release-issue for the escalation pattern from agentic-dev-workflow.lex section 5.3.1
        - README rewritten to reflect the unified shape
        - Cloud branch deleted or kept as a release channel — open question, see section 9

    3.2. Prerequisites

        - Phase 0 captured
        - PAT scope: each fine-grained PAT should add arthur-debert/release with Issues: Read and write, so release-issue-relay can post comments. Clones do not need auth since release is public.

    3.3. Non-goals

        - Per-stack workflow centralization (deferred to Phase 3)
        - Per-stack environment templates (deferred to Phase 2)

4. Phase 2: Non-rust environment support

    Per-stack SessionStart hooks and setup-dev-env templates, so non-rust repos can be cloud-ready.

    4.1. Stack priority order

        Higher to lower:

            - Electron (arami-app, simple-gal-ui)
            - Web / frontend npm (arami frontend, if separate from arami-app)
            - VS Code extension (lex-fmt/vscode)
            - Neovim plugin (lex-fmt/nvim)
            - Zed extension (lex-fmt/zed-lex)

    4.2. Deliverables

        - One scripts/setup-dev-env.sh template per stack in release/templates/<stack>/
        - One canonical .claude/settings.json snippet per stack (SessionStart hook)
        - sweep-github-policy and gh-repo-setup extended to drop these into a consumer
        - One pilot consumer migrated per stack to validate

    4.3. Pattern

        The cloud env's setup script stays stack-agnostic — gh, skills, CLAUDE.md. Each consumer repo carries a thin SessionStart hook in .claude/settings.json that calls scripts/setup-dev-env.sh inside the repo, which handles project-specific deps: npm install, cargo fetch, luarocks install, and so on. One-time per repo, never touched again unless the stack changes.

5. Phase 3: Reusable workflows

    Centralize per-stack CI workflows in release.

    5.1. Stacks to cover

        - rust-cli (exists today as release/.github/workflows/rust-cli.yml)
        - rust-lib (cargo-publish flow, library crates)
        - electron-app (electron-builder + macOS signing + notarization)
        - vsce-ext (vsce package and publish, ovsx)
        - nvim-plugin (tests via vusted or busted)
        - tree-sitter (grammar build, npm publish)

    5.2. Deliverables

        - One reusable workflow per stack at release/.github/workflows/<stack>.yml
        - One consumer per stack migrated as pilot
        - audit-portfolio extended to detect workflow drift — consumer's CI file diverged from canonical

    5.3. Rollout

        Per stack: build the reusable workflow, validate on one consumer, then roll out to the remaining consumers of that stack.

6. Phase 4: Sustainability loop

    Wire up the escalation and audit patterns from the vision (see agentic-dev-workflow.lex section 5.3).

    6.1. Read-side (4a)

        - PATs updated with release in scope (Issues: Read and write)
        - release-issue-relay skill in active use across the portfolio
        - Consumer skills and CLAUDE.md updated to invoke release-issue-relay when infrastructure friction is hit

    6.2. Write-side (4b)

        - Scheduled routine at release, weekly or twice-weekly cadence
        - Routine logic: list failed actions per repo, cluster by error signature, identify root causes, file issues at release
        - audit-portfolio extended with the routine's clustering logic

7. Phase 5: Merge strategy

    Switch the default from squash to rebase for review-rich PRs.

    7.1. Deliverables

        - Update CLAUDE.md merge guidance
        - Update pr-review-respond and gh-pr-review-loop skills to default to rebase
        - Update standardized pull_request_template.md to mention rebase as default
        - The protected-branch ruleset stays unchanged — linear history covers both squash and rebase

8. Phase 6 and beyond: Iteration

    Open-ended. New skills, new stacks, new workflows, new sustainability heuristics. All slot into the substrate built in phases 0 through 5.

    Examples of likely follow-ups:

        - Diff-size convergence heuristic for review rounds (see agentic-dev-workflow.lex section 2.3)
        - More skills surfaced as the portfolio's needs emerge
        - Per-stack release channels (cloud vs. main, stable vs. edge)
        - Migration tooling for new repos joining the portfolio

9. Sequencing notes

    9.1. What is parallel-doable

        Phases 2, 3, and 4 are mostly independent of each other once Phase 1 lands. Phase 5 can happen alongside any of them. The sequencing in this document reflects priority and dependency-on-Phase-1, not strict ordering.

    9.2. What is strictly sequential

        - Phase 0 → Phase 1: the unify needs the proposal as anchor
        - Phase 1 → all subsequent phases: everything depends on the portable skills landing
        - Within Phase 2: stack ordering can be revised based on real demand

    9.3. Pause-and-resume points

        Each phase ends in a stable state. Pausing at any phase boundary does not leave the portfolio in a half-done state.

    9.4. The cloud-branch open question

        After Phase 1, the cloud branch is redundant — its content lives on main. Two choices:

            - Delete cloud (simplest; main is the only branch)
            - Keep cloud as a stable channel: main churns with active work, cloud only updates on tagged releases, env setup scripts pin to cloud for stability

        Deferred decision: Phase 1 keeps both branches alive; the choice is revisited at the start of Phase 2 with usage data from Phase 1.
