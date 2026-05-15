Phased Rollout:
Path from where we are to the full agentic dev workflow

Source location: arthur-debert/release, branch main, path docs/proposals/. Companion document: agentic-dev-workflow.lex captures the vision.

1. Scope and shape

    Each phase is a focused, self-contained chunk of work. Each ends in a stable state — the portfolio can pause indefinitely between any two phases without breakage. Phases mostly depend on earlier ones; several are parallel-doable. The ordering reflects priority and prerequisites, not strict serialization.

    Reference to the vision: see agentic-dev-workflow.lex, especially section 5 (open questions and rethinking) which this rollout addresses.

    1.1. Product groups (overlay on top of phases)

        The phases below collectively deliver to the portfolio in five groups. Phases within a group must work back-to-back across all consumers before the next group begins; groups are sequential, phases within a group are mostly parallel-doable.

        Group A: Foundation
            Phases 0, 1, and the env-side half of phase 2.

            Deliverables: env tools (gh, lefthook, stack-specific binaries like bats, vsce, ovsx, lua/luarocks/busted/vusted), auth (PATs scoped to include release with Issues: Read and write), session-start reading (user-level CLAUDE.md, ~/.claude/skills/ population, per-repo SessionStart hook + scripts/setup-dev-env.sh), the basic PR flow (review request, address, resolve, ready, merge).

            Done when: a session in any consumer repo can drive a PR through the full review loop without local-only dependencies. The bones are proven.

        Group B: Rollout-verify
            An explicit pilot pass after Foundation, with no new phase work mixed in.

            Constituent phases: the validation half of Phase 2 (per-repo SessionStart hooks + scripts/setup-dev-env.sh for each pilot consumer), plus the active-use half of Phase 4a (agents at consumer repos invoking release-issue-relay when they hit friction — the skill itself shipped in Phase 1.3 and lives in Group A, but its actual production use is what closes the loop).

            Deliverables: spawn cloud sessions on a representative subset of consumer repos (at minimum: one rust-cli, one electron, one vsce-ext, one nvim, one zed); observe what breaks; close gaps by updating Foundation pieces. Each pilot lands a SessionStart hook PR encoding its discovered setup sequence (npm install, submodule init, language-specific deps, lefthook install). Use release-issue-relay to capture friction without losing context.

            Done when: each onboarded stack has at least one consumer running end-to-end — task picked up from an issue, PR opened, reviewed by Copilot and Gemini, comments addressed, PR ready for human review, merged.

            Why this group exists separately: the original phase ordering didn't make the verify-it-holds-together step explicit. Skipping it would mean shipping more phases on top of an unverified foundation; rolling out CI workflows or release pipelines before the Foundation is portfolio-tested would compound any defects.

        Group C: CI workflows
            Phase 3, test/lint/format reusable workflows per stack.

            Deliverables: one reusable workflow per stack at release/.github/workflows/<stack>.yml for the test + lint + format step; consumers pin to @v1; one consumer per stack migrated as pilot. The audit tooling extends to detect workflow-file drift.

            Done when: a test or lint fix at release propagates to all consumers on their next CI run, no per-repo edit needed.

        Group D: Release workflows
            Latter half of phase 3, release-action per stack.

            Deliverables: per-stack release pipelines (rust-cli is shipped; rust-lib, electron-app, vsce-ext, nvim-plugin, tree-sitter, gh-action, mdbook-site, jekyll-site, brew-tap are planned per the main README's stack matrix); macOS signing and notarization for the relevant stacks; security → patch release glue.

            Done when: a release on any consumer is a one-command operation, executed in CI, with the same shape across stacks.

        Group E: Enhancements
            Phases 4b and 5, plus future work.

            Deliverables: PR review loop convergence heuristics (diff size shrinking round-over-round, reviewer-comment-count not increasing), scheduled portfolio audit routine, merge-strategy switch from squash to rebase, smarter triage in pr-review-respond.

            Done when: the scheduled audit routine runs on cadence (weekly or twice-weekly) and produces actionable issues at release, the PR-review safety hatch uses diff-size-and-comment-count heuristics (not just the 5-round dumb cap), and merge default is rebase across the portfolio with the canonical pr-review-respond / gh-pr-review-loop skills updated to match.

            Deferred because all of these are PR-localizable — they affect one PR at a time, can be iterated incrementally, and don't require coordinated cross-consumer rollout. The cost of doing them early is opportunity cost (Foundation/Verify/Workflows benefit the whole portfolio per fix; enhancements benefit one PR per fix); the cost of doing them late is small (the portfolio still works without them, just with rougher edges on individual PRs).

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

    9.1. Group-level sequencing (the load-bearing constraint)

        Groups (see §1.1) are sequential. A group must work back-to-back across all consumers before the next group begins:

            Foundation (A) → Rollout-verify (B) → CI workflows (C) → Release workflows (D) → Enhancements (E)

        The B → C transition is the most important to respect. Rolling out reusable CI workflows on top of an unverified foundation compounds any defect into N-consumer rework. Group B exists to surface defects before they compound.

    9.2. Phase-level sequencing within a group

        Within a group, phases are mostly parallel-doable. The strict dependencies are:

            - Phase 0 → Phase 1: the unify step needs the proposal as anchor
            - Phase 1 → all subsequent phases: everything depends on the portable skills landing
            - Within Phase 2: stack ordering can be revised based on real demand

        Group B (Rollout-verify) covers Phase 4a's active-use half and the validation half of Phase 2 — these are explicitly the "verify it holds together" steps, not new phase work.

        Group E (Enhancements) is strictly the last group, but its constituent phases (4b audit routine, 5 merge strategy) can run in either order or in parallel.

    9.3. Pause-and-resume points

        Each phase ends in a stable state. Pausing at any phase boundary does not leave the portfolio in a half-done state. Group boundaries are the natural "checkpoint" pause points — between A and B is the most valuable, since it's where the foundation gets stress-tested.

    9.4. The cloud-branch open question

        After Phase 1, the cloud branch is redundant — its content lives on main. Two choices:

            - Delete cloud (simplest; main is the only branch)
            - Keep cloud as a stable channel: main churns with active work, cloud only updates on tagged releases, env setup scripts pin to cloud for stability

        Status as of Phase 1 completion: the cloud branch hasn't been deleted; env/setup.sh pins to main. The decision is no longer urgent — main works as the source, cloud is dormant but harmless. Revisit if a real stability-channel need emerges (e.g. when Group C/D ships breaking changes worth gating behind a slower release channel).
