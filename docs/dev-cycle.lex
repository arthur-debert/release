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

    On the required reviewer set:

        The default required set is Copilot only. CodeRabbit is a second
        requestable reviewer being PILOTED on the phos-org repos — the only
        place its GitHub App is installed; a pilot repo opts in via the
        override below. Requiring it by default would gate every other
        repo on an app that is not installed there: the request edge
        silently drops (#613-style) and the PR parks at `REVIEWS_PENDING`
        forever.

        When a repo requires several reviewers, they gate in parallel
        (release#622), not as a primary with a fallback. A PR is `REVIEWED`
        only when EVERY required reviewer has a review on the CURRENT head;
        every push stales them all (so `release-core pr review request`
        re-requests them), and `release-core pr ready` requires all present
        + resolved. Dual coverage trades availability — if one required
        reviewer is down, the PR stays at `REVIEWS_PENDING` until it
        recovers, and the engine names the outstanding reviewer so the
        stall is visible, never silent.

        The required set is a CONFIG knob, not code — reviewer pricing and
        availability shift, so changing it is a one-line edit with no engine
        change. The shipped default `[copilot]` lives in
        `reviewers_config.DEFAULT_REQUIRED` (carried by the `release_core`
        wheel). A single repo overrides it with a `required_reviewers:` list
        in its existing optional `.release-sync.yaml` (the same file that
        carries `capabilities:` — no new tracked file):

        Opt a pilot repo into CodeRabbit alongside Copilot:

            required_reviewers:
              - copilot
              - coderabbit

        :: yaml ::

        Each name must map to a registered reviewer adapter (`copilot`,
        `coderabbit`, `gemini`, …); an unknown name fails loud, and an
        empty/absent list falls back to the default. Adding a NEW reviewer
        backend is still an adapter in the registry; flipping which existing
        ones gate is purely this config.

2. Larger features (multiple PRs)

    A larger feature — one comprising multiple PRs — is a composition of the
    single-task cycle ([#1]) under one coordinating agent. There is one
    overarching feature branch (the *epic branch*) and one umbrella PR; the
    feature's execution is a series of single-task PRs that merge into the epic
    branch, and the umbrella PR finally merges the epic branch to `main`. The
    phases below run in order.

    2.1. Information gathering

        A coordinating agent is started and, as in [#1.1], given the task via a
        GitHub issue, a product spec, or a chat with the user. It does the
        general reading/research, cuts the epic branch, and asks the user for
        decisions/clarifications as needed.

    2.2. Delegation, not implementation

        The coordinating agent does NOT implement. Its job is to keep the
        execution of the parts correct, cohesive, and on track. If it
        implements, it spends its own context on the work and either degrades
        as the context window fills or forces compaction; delegating also keeps
        token usage (and cost) down.

        It spins one subagent per part — ideally each scoped by its own GitHub
        issue. The single-task cycle for a part is SPLIT across roles, so no
        one context carries all of it. An implementer that also shepherds its
        own review rounds drags the full implementation context — exploration,
        dead ends, test output — through every round: single agents have
        ballooned past ~700k tokens that way, and a long context is also a
        worse judge of review comments, defending remembered choices instead
        of reading the diff on its merits.

        - The IMPLEMENTER subagent stops at PR-open. It implements its part
          ([#1.2]), runs `release-core gate`, pushes, and opens the draft PR
          targeting the epic branch (not `main`) — with the `## Context`
          handoff note in the PR body (why this approach, what is out of
          scope, what NOT to "fix") written for a stranger, because a
          stranger is exactly who addresses the review rounds. Then it
          reports back and terminates; it never sees a review round.

        - The COORDINATOR owns every wait and every flip. It blocks in-turn
          on `release-core pr wait` (a subagent that yields to wait
          terminates and is never re-woken), and when the engine says READY
          it runs the guarded `release-core pr ready`.

        - A FRESH SHEPHERD subagent handles each ADDRESSING round. When the
          wait returns ADDRESSING, the coordinator spawns a shepherd whose
          brief is just the PR number and the Context note. It triages the
          open threads ([#1.3]'s discipline: fix or reply with rationale,
          resolve as it goes), pushes, re-requests the review, hands the
          wait back to the coordinator, and terminates. One fresh shepherd
          per round: each starts cold on the diff as it exists — a fraction
          of the tokens, and a cleaner read of the reviewer's point.

    2.3. Integration

        The coordinating agent drives the merge — including the go/no-go — of
        each subagent's workstream PR into the epic branch. This is the
        coordinator's call; it does NOT need user approval for these
        intra-epic merges. The user's approval gate is the umbrella PR ([#2.6]),
        not the individual workstreams.

    2.4. Convergence — clearing the fallouts

        Once the initial workstreams are merged into the epic branch, the
        coordinating agent gathers the fallouts: follow-ups filed as GitHub
        issues during execution, plus things that surfaced while implementing.
        It opens one final workstream and assigns a subagent to clear them.

        Why this is a distinct phase:

        - Workstream agents deliberately do NOT side-quest every little thing
          they find — if they did, they would never finish. That restraint is
          correct.
        - But the epic must not merge with a pile of decoupled follow-ups
          trailing behind it. This convergence step is where they get done, so
          nothing is left behind.

        The one caveat is scope. Clear only what belongs to this epic:

        - Surfaced as obviously part of the feature during normal development →
          do it now, in the convergence workstream.
        - A related-but-separate feature → leave it as a filed issue; it is not
          this epic's job.

    2.5. Documentation pass

        When the convergence workstream merges to the epic branch, the
        coordinating agent delegates an exploration agent to find what the
        feature changed in the docs — what needs updating, fixing, or removing —
        and to make those changes on a dedicated PR. This covers both:

        - out-of-code docs under `docs/` (dev, user, and reference); and
        - docstrings, especially module-level ones that capture design,
          trade-offs, pointers, and head-ups.

    2.6. The umbrella PR

        With the work and docs in, the coordinating agent opens the feature's
        umbrella PR. It:

        1. double-checks which issues the PR actually closes (and which it does
           not);
        2. writes a high-level description of the whole epic, pointing to the
           related issues;
        3. drives the PR through the same `gh-pr-review-loop` discipline
           ([#1.3]) under the same split ([#2.2]) — the coordinator waits and
           flips, a fresh shepherd handles each review round — then flips it
           to READY for the user's final merge.

    2.7. Release

        When the user merges the umbrella PR, the coordinating agent cuts the
        release(s) (`release-core cut`), using the realized epic as the guide
        for whether to bump MINOR or PATCH — only the user requests a MAJOR (see
        the versioning contract). If the project has chained dependencies — e.g.
        a CLI that a downstream desktop app bundles — the cascade runs all the
        way through: release each repo in dependency order.

