Agentic Development Workflow:
Vision, ideal flows, and topics to rethink

Source location: arthur-debert/release, branch main, path docs/proposals/. Companion document: phased-rollout.lex captures the execution plan.

1. Context

    1.1. Portfolio shape

        Larger projects, expected to grow:

            - lex-fmt (5 repos): CLIs, editor plugins for VS Code, neovim, Zed, and a full desktop app
            - phos (2 repos): Rust backend and web frontend

        Smaller projects, stable in scope:

            - dodot, rustloc, simple-gal, simple-gal-ui
            - lib crates and lib+cli combos: standout, padz, clapfig, burgertocow

        In all cases the work is mostly agentic development with human review.

    1.2. The problem this project exists to solve

        Each repo today has a different layout, bespoke tooling, divergent language-level setup, pre-commit hooks, CI workflows for test and release, GitHub policy, secret names, testing infrastructure, and logging conventions.

        As I work across them, I cannot reliably remember which repo has which variations. As I lean further into agentic development, different skills, prompts, and READMEs compound the drift. When a bug or improvement surfaces in one repo, propagating it requires manual isolation, redescription, and replication in each target repo — and since everything is slightly different, the replication itself breaks things.

        Roughly half my time is plumbing, troubleshooting, and fixing small things that should not recur. That is the bad time investment this project exists to eliminate.

2. Implementation Flow

    The canonical sequence from "agent picks up a task" to "PR is merged."

    2.1. The shape of an implementation

        a. The implementation agent takes a task from a GitHub issue.
        b. If it needs clarification, it asks as a comment on the issue; otherwise it starts.
        c. The agent does the main implementation on a branch, pushes, and opens the PR as a draft.
        d. Via repo policy, the draft PR automatically requests reviews from Copilot and Gemini.
        e. Via Auto-fix, the implementation agent addresses the review comments.
        f. After both reviews have been addressed, the PR is set to ready.
        g. The final review and merge is mostly done by a human.

    2.2. Pushback discipline

        The reviewer agents (Copilot and Gemini) have less project context and memory than the implementer. Outside the strict technical realm — backwards compatibility, intended use cases, project ethos — the implementer should feel free to push back if a comment is not relevant.

        Whether a comment is addressed or pushed back, both require a reply on the thread and a thread resolve. This is what makes following the state of a PR easy: an unresolved thread is by definition a contested or unhandled one.

    2.3. Review rounds and the safety hatch

        Multiple review rounds are valuable, especially on more complex PRs. Balance against diminishing returns: prompts to the reviewer should not push them to always find something — that creates noise.

        A safety hatch is needed in case both agents lose general context and go commando on tangents. A dumb but reasonable cap is 5 rounds.

        Future, higher-signal heuristics: if diff sizes are not shrinking round-over-round, by say 30%+, that is a signal the agents are not converging and human intervention is needed.

    2.4. When to address reviewer comments

        Two viable timings:

            a. Wait for both Copilot and Gemini, then address as a batch.
            b. Address each reviewer as their comments post.

        The batch approach catches overlapping comments (both reviewers often hit the same issue with different framings, allowing one unified fix instead of two whipsaw fixes) and burns less context per round. The per-reviewer approach reduces latency until fixes start landing.

        Default: batch. To be encoded into the pr-review-respond skill as "wait for both reviewers before triaging."

3. Repo Setup

    Repo policy and configuration that every onboarded repo carries uniformly.

    3.1. Branch protection and review policy

        - No commits to main, PR required, linear history required
        - Draft PRs automatically request Copilot and Gemini review
        - Required checks per stack

    3.2. Standardized names

        - Secret names: RELEASE_TOKEN, CARGO_REGISTRY_TOKEN, and the rest of the canonical list
        - Workflow file names per stack

    3.3. Canonical .github/ policy files

        - CODEOWNERS
        - dependabot.yml
        - copilot-instructions.md
        - pull_request_template.md
        - workflows/copilot-review.yml

    3.4. Idempotent application

        Today this is driven by release/bin/apply-ruleset and release/bin/sweep-github-policy, both idempotent — running them on an already-set-up repo is a no-op, not a re-apply. Phase 1 of the rollout ports the same logic into a portable skill (gh-repo-setup) so cloud agents can run it without depending on the local PATH.

4. Development Workflow

    The cycle from "I have an idea" to "the feature is in production."

    4.1. Spec

        Significant features and changes start as a spec at docs/proposals/<feature>.lex in the relevant repo. Spec documents are source-controlled because they go through multiple revisions and the history is meaningful.

        I brief the agent on high-level goals, non-goals, and pointers to related docs. We interactively build the proposal until it is signed off.

    4.2. Implementation plan

        Once the spec is signed off, the agent drafts an implementation plan. We iterate over the plan until it is signed off.

    4.3. Epic and phase issues

        Each epic (most features generate exactly one epic) becomes a master GitHub issue with three parts:

            - An intro
            - A pointer to the spec
            - A high-level list, one line per phase, title only

        Each phase becomes its own GitHub issue with detailed scope: what needs to change, acceptance criteria, testing strategy.

        All phase issues are linked to the master epic. That linkage is what makes tracking trivial.

    4.4. Phase development

        When development on a phase starts:

            a. The agent is pointed at the current phase issue.
            b. The agent finds the epic and spec references and reads them for context.
            c. The agent creates a branch, does the work, opens a PR, and drives it through the full review loop from section 2.
            d. The final PR is presented for human review and merge.

    4.5. Branching shape for larger features

        For larger epics, multiple phase PRs land into a feature branch (not main). The feature branch is what merges to main, after a final end-to-end review. Multi-PR features are easy to lose the shape of from inside the minutiae; the feature-branch PR is the "look at the whole thing" checkpoint.

        For multi-epic features, we merge to main after each epic and cut a release (even with no user-facing changes). This keeps merge cost down, decreases staleness, and exercises the code paths.

    4.6. Audit at the end

        When all planned phases of an epic are done, before the feature-branch-to-main PR, we audit by walking from spec to phase issues and identifying divergence.

        Divergence is not automatically a problem — often we found better solutions or hit hard blockers. Per divergence we decide:

            - Update the spec or issue if we like the new direction
            - Schedule a follow-up issue if the divergence is real but acceptable
            - Open a final fixup PR if the divergence must be resolved before merge to main

    4.7. Finishing

        Before releasing:

            - Review user-facing docs, cargo docs, changelog, CLI help strings
            - Cut the release
            - For multi-layer projects (frontend consuming backend), if the work was on the backend: bump frontend versions to match, verify, PR, push, review, then a new release per frontend

5. Open questions and topics to rethink

    5.1. Merge type

        The current default is squash merge. Three problems with squash for review-rich, multi-PR work:

            - Makes features with multiple PRs and parallel development much more prone to merge conflicts
            - Hides how code reviews shaped the product — the per-commit history is collapsed
            - Produces large commits which are painful for bisect

        The protected-branch ruleset requires linear history, which rules out merge commits. Squash and rebase both produce linear history; rebase preserves per-commit history. Worth reconsidering rebase as the default for review-rich PRs. The ruleset does not need to change.

    5.2. CI workflows centralization

        Each repo currently carries its own copies of CI workflows. Problems:

            - Repos diverge from each other locally
            - Bug fixes applied at one consumer are never backported to the others
            - The release repo cannot use the daily experience of running these workflows to guide its own templates
            - The release repo's templates become approximations that drift from reality

        Hypothesis: release should host the workflows as reusable GitHub Actions, one per stack. Consumers pin to a tag — the existing @v1 contract. Fixing or enhancing a workflow happens once and propagates. This presumes the workflows generalize well, which they should: we have a handful of stacks with enough freedom to standardize.

    5.3. Sustainability

        No matter how well the initial work is done, it will have bugs, blind spots, and unmet needs.

        The structural challenge: the knowledge for how things should work lives in the release repo. But the friction (workflow failures, GitHub interaction problems) surfaces at the consumer repos. When that happens, the agent at the consumer will fix things locally to unblock — and the chances that the same problem affects other projects is high. Worse: lacking release-repo context, the local agent will often fix in ways that break other consumers or are non-viable to upstream.

        Two complementary loops are proposed:

        5.3.1. Read-side escalation

            When an agent at a consumer hits infrastructure friction, it must:

                - First unblock locally (apply a workaround)
                - Then file an issue at release describing the failure, desired result, and workaround
                - Before filing, search recent release issues for a matching symptom; if found, comment on that issue rather than filing a new one. The comment count is signal of recurrence.

        5.3.2. Write-side audit

            A scheduled session (cron or routine) at release that:

                - Lists failed actions across the portfolio
                - Clusters by error signature
                - Identifies root causes
                - Files issues at release for the patterns it finds

            Frequency: weekly or twice-weekly is plenty.

6. Where we are

    I started experimenting with Claude Code Cloud — now: Claude Code on the web — and found it materially better than local-only, with one caveat: cloud sessions have a much more restricted workspace model than local Claude Code.

    Recent work on this repo's cloud branch built out a distribution mechanism that handles the cloud constraint:

        - A standalone-skills pattern under ~/.claude/skills/ populated by an environment setup script
        - A user-level CLAUDE.md installed the same way, for portfolio-wide instructions
        - PAT-based gh CLI access for cross-repo work that the cloud session's MCP scope blocks
        - Auto-fix wired up for the webhook-driven review loop

    That model already partially fixes several of the pains listed above. The companion phased-rollout.lex captures the path from here to the full vision.
