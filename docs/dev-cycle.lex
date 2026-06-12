The Development Life Cycle

    The ONE dev/PR lifecycle, draft-first. A simple task is one PR; a larger
    feature is a composition of single-task cycles under a coordinating agent.

    This is the same flow `release-core how-to` renders and the
    `gh-pr-review-loop` skill drives — keep all three in lockstep. The skill is
    the discipline (request review → wait → triage → resolve → stop at ready);
    this document is the model behind it.

1. The simple task (one PR)

    1.1. Information gathering

        Align on what is to be done before touching code.

        - Task description: a GitHub issue, a `/handoff` artifact, or an
          interactive message.
        - Contextualization: read the description and the related code /
          resources.
        - Clarifications: if information is missing or a real decision point
          exists, surface it — and where possible propose your own preferred
          option rather than only asking.

    1.2. Implementation

        Do the task, writing or improving tests where needed. This includes a
        changelog fragment in the SAME PR: `release-core changelog add <slug>
        "<one-line summary>"` (writes `CHANGELOG/unreleased-<slug>.md`; `<slug>`
        is kebab-case). A release refuses to cut without one, so a fragment-less
        merge silently blocks the next release. Never hand-edit `CHANGELOG.md`.

        Run `release-core gate` until green before opening the PR. The gate is
        lint/format/static only — run the repo's `test` verb yourself too, since
        CI runs tests as a separate required check (tooling.lex §2).

        Gate fidelity: a local check that reads ambient local state — a sibling
        `../repo` checkout, a tool only your machine has, an env var CI doesn't
        set — passes locally and lies about CI. CI must provision the resource
        explicitly (fetch it, install it, set it); if a check needs something,
        make CI provide it rather than trusting that local green implies CI
        green.

    1.3. PR shepherding (draft-first, state-machine-driven)

        Drive this through the `gh-pr-review-loop` skill, which arms the
        PreToolUse PR-loop guard and walks the `release-core pr status` state
        machine (tooling.lex §3). The shape:

        - Open the PR as a DRAFT, linking the issue if relevant (`for #<id>` /
          `closes #<id>`).
        - Loop: `release-core pr status` reports one lifecycle state plus the
          single next action — do that action (request reviews via
          `release-core pr review request`, triage threads, fix CI), then
          re-read.
        - Wait with `release-core pr wait` — the one engine-driven wait. It
          blocks in-turn and returns as soon as there is something to do;
          never hand the wait to a detached background process (a subagent
          that yields to wait terminates and is never re-woken).
        - When the engine says READY (reviews addressed, CI green, mergeable),
          flip draft → ready with `release-core pr ready` — the guarded flip
          refuses early, so it can't fire prematurely. That hands it to a
          human; don't auto-merge.

    1.4. User validation

        The user merges or asks for changes/clarifications. If more work is
        needed, flip the PR back to draft (`release-core pr ready --undo`);
        only when the new changes + checks pass flip back to READY for
        re-validation.

    On re-requesting a review:

        After any push, re-request the review — no exception for small
        rounds. The state engine is the arbiter: a review counts only
        against the current head, so any push makes the prior review stale
        and `release-core pr status` advises RE-REQUEST. That next action
        is authoritative; bot re-reviews are cheap, so comply and move on.

2. Larger features (multiple PRs)

    A larger feature — one comprising multiple PRs — is a composition of the
    single-task cycle ([#1]). There is one overarching agent, branch, and
    umbrella PR for the feature; its execution is a series of single-task PRs.

    2.1. Information gathering

        A supervising agent is started and, as in [#1.1], given the task via a
        GitHub issue, a product spec, or a chat with the user. It does general
        reading/research, creates a feature branch for the work, and asks the
        user for decisions/clarifications as needed.

    2.2. Execution

        The coordinating agent does NOT do the implementation. Its role is to
        keep the execution of the smaller parts correct, cohesive, and on track.
        If it implements itself, it spends its context on that and either
        degrades its own performance as the context window fills or forces
        compaction; delegating also keeps token usage (and cost) down.

        The agent spins subagents, assigning each its own part (ideally from a
        GitHub issue for that part). Each subagent runs the full single-task
        cycle for its part — implement, then shepherd its own PR through review
        and CI to READY — and reports back to the coordinating agent, which
        integrates the parts and keeps the overall feature on track.

        The coordinating agent will drive the merging (including the go no-go) of
        each subagent workstream into the epic branch, without user approval.

        When all initial workstreams are merged into the epic branch, the coordinating agent will
        look for fallouts either in gh issues created during execution as well as things come up During the implementation period the coordinating agent will then create a final work stream and assign it to a sub-agent to handle these fallouts. This is critical because during a complex execution often it's the case that we don't perfectly plan or don't realize that things need to be done ahead of time. During the implementation we run into this.

        Now it's actually a good thing that the work stream agents do not side-quest every little thing they find because else they wouldn't get anything done. This is a right thing but it's a bad thing if the epic execution piles up fallouts and follow-ups and things like that that are decoupled from the actual epic. We want to have this final conversion step where we get this done and we don't leave anything behind.

        Now the only obvious caveat for this is these things have to be under the scope. If it's a related but not directly involved feature you don't want to do it but if it's during the normal development it becomes obvious it's part of it and should be done. 

        When that branch merges to the epic branch, the coordinating agent will delegate an exploration agent to check what in the documentation needs updating, fixing, removing and then this agent will do it on a final PR. This includes both our of code docs/ (be it dev, user, reference documents) and doc strings, specially module levels ones that go over the design, trade-offs, pointers, head-ups, etc.

        When that is done, the coordinating agent will create the final pr, double checking which issues it does close or not, write a high level description of the full epic, pointing to the related issues. It will then Sheppard the PR through the same process (manage reviewers, checks, mergeability) and finally flip to ready for the user's final merge.

        When that PR is merged the coordinating agent will cut new releases, using the epic realized as a guide weather to bump minor or patch versions (only users should request major version bumps). If the project has chained deps to be released (i.e. multiple repos, with one being a cli that the next, a desktop app bundles), than this means doing the release cascade all the way. 

