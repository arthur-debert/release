The Development Life Cycle

    The ONE dev/PR lifecycle, draft-first, and ALWAYS delegated. The agent the
    human addresses is a coordinating agent: it never implements. Regardless of
    task size it spawns subagents to do the work — there is no "small enough to
    just do it myself" path. A single-PR task and a multi-PR feature differ ONLY
    in branch/merge topology (one PR to `main`, vs. workstream PRs into an epic
    branch under an umbrella PR); they do NOT differ in whether delegation
    happens.

    Why unconditional: deciding whether a task is "simple enough to do directly"
    itself burns the coordinator's context, and the agent usually starts
    executing before it finishes deciding. A fixed always-delegate rule removes
    that failure mode and keeps the coordinator's context light for long,
    multi-PR efforts — the same reason the implementation of each PR is split
    across fresh subagents (see below).

    This is the same flow `release-core how-to` renders and the
    `gh-pr-review-loop` skill drives — keep all three in lockstep. The skill is
    the discipline (request review → wait → triage → resolve → stop at ready);
    this document is the model behind it.

1. The single-task cycle (one PR)

    The unit of work, applied identically whether the task ships as one PR to
    `main` or as one workstream of an epic ([#2]). The coordinator delegates it;
    it does not run the steps itself. The cycle is SPLIT across roles so no one
    context carries all of it:

      - an IMPLEMENTER subagent does the implementation ([#1.2]) and STOPS AT
        PR-OPEN ([#1.3]);
      - the COORDINATOR owns every wait and the flip;
      - a FRESH SHEPHERD subagent handles each review-addressing round.

    The role split and its rationale are spelled out in [#1.3] and apply to
    every task. The phases:

    1.1. Information gathering (the coordinator)

        The coordinator aligns on what is to be done before any code is touched —
        and before delegating.

        - Task description: a GitHub issue, a `/handoff` artifact, or an
          interactive message.
        - Contextualization: read the description and the related code /
          resources.
        - Clarifications: if information is missing or a real decision point
          exists, surface it to the user — and where possible propose a preferred
          option rather than only asking.

        The coordinator does the reading/research needed to brief the work, then
        delegates the implementation. It does not implement.

    1.2. Implementation (the implementer subagent)

        The coordinator spawns an IMPLEMENTER subagent to do the task, writing or
        improving tests where needed. This includes a changelog fragment in the
        SAME PR: `release-core changelog add <slug> "<one-line summary>"` (writes
        `CHANGELOG/unreleased-<slug>.md`; `<slug>` is kebab-case). A release
        refuses to cut without one, so a fragment-less merge silently blocks the
        next release. Never hand-edit `CHANGELOG.md`.

        The implementer runs `release-core gate` until green before opening the
        PR. The gate is lint/format/static only — it deliberately does NOT run
        the test suite, so a green gate is necessary but not sufficient for "CI
        will be green". The implementer also runs the Kind's tests separately
        (`release-core test*` / `cargo test` / `npm test` / `pytest`; see
        `release-core how-to`), since CI runs them as a separate required check
        (tooling.lex §2).

        Gate fidelity: a local check that reads ambient local state — a sibling
        `../repo` checkout, a tool only your machine has, an env var CI doesn't
        set — passes locally and lies about CI. CI must provision the resource
        explicitly (fetch it, install it, set it); if a check needs something,
        make CI provide it rather than trusting that local green implies CI
        green.

    1.3. PR shepherding — the role split (draft-first, state-machine-driven)

        The single-task cycle is SPLIT across roles so no one context carries all
        of it. An implementer that also shepherds its own review rounds drags the
        full implementation context — exploration, dead ends, test output —
        through every round: single agents have ballooned past ~700k tokens that
        way, and a long context is also a worse judge of review comments,
        defending remembered choices instead of reading the diff on its merits.
        Reaching PR-open is already token-intensive; addressing on top of it is
        more expensive and a worse review than starting cold. So:

        - The IMPLEMENTER subagent STOPS AT PR-OPEN. It opens the PR as a DRAFT
          (linking the issue if relevant: `for #<id>` / `closes #<id>`) with a
          `## Context` handoff note in the PR body — why this approach, what is
          out of scope, what NOT to "fix" — written for a stranger, because a
          stranger is exactly who addresses the review rounds. Then it reports
          back and terminates. It never sees a review round.

        - The COORDINATOR owns every wait and the flip. It blocks in-turn on
          `release-core pr wait` — the one engine-driven wait, which returns as
          soon as there is something to do and performs the guarded draft → ready
          flip itself once the engine reaches READY. Never hand the wait to a
          detached background process (a subagent that yields to wait terminates
          and is never re-woken). `release-core pr ready` is the explicit flip
          for when the coordinator lands on READY without a wait; the guard
          refuses it early, so it can't fire prematurely. That hands the PR to a
          human; don't auto-merge.

        - A FRESH SHEPHERD subagent handles each ADDRESSING round. When the wait
          returns ADDRESSING, the coordinator spawns a shepherd whose brief is
          just the PR number and the Context note. Following the
          `gh-pr-review-loop` discipline, it triages the open threads (fix or
          reply with rationale, resolve as it goes), pushes, re-requests the
          review, hands the wait back to the coordinator, and terminates. One
          fresh shepherd per round — each starts cold on the diff as it exists.

    1.4. User validation

        For a single-PR task the one PR targets `main`; the coordinator drives
        it to READY and the HUMAN merges it to `main`. The coordinator stops at
        the READY flip and does not auto-merge — merge only on explicit
        authorization. The user merges or asks for changes/clarifications. If more
        work is needed, flip the PR back to draft (`release-core pr ready
        --undo`); only when the new changes + checks pass flip back to READY for
        re-validation.

    How a review round works (round 1 exhaustive, later rounds incremental):

        Round 1 is engineered to exhaust the high-severity findings: each
        local-agent reviewer fans its review out into parallel dimension passes
        (correctness, cross-file invariants, security/robustness, test quality)
        whose union is mechanically deduped and posted directly — expensive
        once, by design. Every finding arrives pre-classified on ONE 4-tier
        severity ladder (`critical | major | minor | nit`): its comment carries
        the human-readable Conventional Comments label AND a machine marker the
        engine reads, so the engine routes by severity directly and no shepherd
        re-classifies. Rounds AFTER the first are cheap and narrow: one
        incremental pass over the fix range only (`last-reviewed-head..new-head`,
        both SHAs the engine already knows) with dependency-neighborhood context,
        new nits suppressed. A rebase or force-push that voids the incremental
        premise falls back to a full re-review. The loop converges by
        construction: high volume/value first, then progressively less.

    On re-requesting a review (per-reviewer config, default head-strict):

        Re-run-on-push is a PER-REVIEWER setting and now defaults ON
        (head-strict, `rerun: true`) for every required reviewer. Because rounds
        after the first review only the fix range as one cheap incremental pass,
        the cost premise for review-once is gone: every push re-stales the
        required reviewers and the engine re-requests them, so the commits that
        ADDRESS a review are themselves reviewed — the exact place fresh mistakes
        land. The state engine is the arbiter: after a push it re-requests the
        stale reviewers (`release-core pr status` advises RE-REQUEST); a bare
        `release-core pr review request` skips a reviewer already done on the
        current head and re-requests the rest. The manual escape hatch is
        `--reviewer <name>`, which FORCES a (re-)request regardless of state.
        Review-once (`rerun: false`) remains an explicit per-reviewer opt-out for
        reviewers whose re-runs stay expensive (full-diff app reviewers on
        metered plans); such a reviewer's review counts on ANY head and is never
        stale-after-push. Trust the next action `release-core pr status` reports.

    On stopping the review loop (the round-cap / no-major+ breaker):

        Each round, address every finding in severity order (critical → major →
        minor → nit), EXCEPT the loop stops when EITHER:

        - the round cap is hit (default 6 — there is no 7th round), or
        - the round surfaced NO major-or-worse finding — every finding is minor
          or nit (worth doing, not worth holding the merge; wording, naming, or
          style with no correctness, behavioral, or security impact).

        Minor and nit findings still get their threads RESOLVED before Ready —
        the breaker ends the machinery, it does not waive them — but they never
        mint another round. When the breaker fires on an otherwise-ready PR (CI
        green, mergeable), the engine routes straight to READY and suppresses all
        re-requests, so a final nit-fix push cannot reopen the loop; the
        coordinator flips and hands to the human. A real blocker (failing CI,
        conflict) still blocks on its own terms — the breaker never invents a
        block. The state engine applies the breaker itself (`release-core pr
        status` reports the `round-cap` / `no-major` stopping condition on the
        READY status); there is no acknowledgement flag to pass.

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
        only when EVERY required reviewer has a counting review — on the CURRENT
        head for a default head-strict (`rerun: true`) reviewer (which a push
        stales, so `release-core pr review request` re-requests it), on ANY head
        for one explicitly opted into review-once (`rerun: false`).
        `release-core pr ready` requires all present + resolved. Dual coverage
        trades availability — if one
        required reviewer is down, the PR stays at `REVIEWS_PENDING` until it
        recovers, and the engine names the outstanding reviewer so the
        stall is visible, never silent.

        The required set AND each reviewer's rerun policy are a CONFIG knob,
        not code — reviewer pricing and availability shift, so changing them
        is a one-line edit with no engine change. The shipped default
        `{copilot: rerun=true}` lives in `reviewers_config.DEFAULT_REVIEWERS`
        (carried by the `release_core` wheel). A single repo overrides it with
        a `reviewers:` map in its existing optional `.release-sync.yaml` (the
        same file that carries `capabilities:` — no new tracked file). The map
        KEYS are the required reviewers; each value is an options dict whose
        one option is `rerun: bool` (default true = head-strict; set false to
        opt a reviewer back into review-once):

        Opt a pilot repo into CodeRabbit alongside Copilot, both head-strict:

            reviewers:
              copilot:    {rerun: true}
              coderabbit: {rerun: true}

        :: yaml ::

        A LIST shorthand means all-required, head-strict (rerun=true):

            reviewers: [copilot, coderabbit]

        :: yaml ::

        Each name must map to a registered REQUESTABLE reviewer adapter
        (`copilot`, `coderabbit`, `codex`, `agy`); an unknown or
        non-requestable name fails loud, and an empty/absent map falls back to
        the default. The retired `required_reviewers:` list key fails loud with
        a migration message (no backwards compat). Adding a NEW reviewer
        backend is still an adapter in the registry; flipping which existing
        ones gate, or whether each re-runs on push, is purely this config.

2. Epics (multiple PRs) — the topology

    An epic — a feature comprising multiple PRs — is the SAME coordinator +
    role-split model as [#1], differing only in branch/merge topology. There is
    one overarching feature branch (the *epic branch*) and one umbrella PR; the
    execution is a series of single-task cycles ([#1]) whose workstream PRs merge
    into the epic branch, and the umbrella PR finally merges the epic branch to
    `main`. Delegation, the implementer-stops-at-open rule, and the fresh-shepherd
    per round are NOT epic-specific — they are [#1.3], applied here per
    workstream. The phases below run in order.

    2.1. Information gathering

        The coordinator is briefed as in [#1.1] — via a GitHub issue, a product
        spec, or a chat with the user. It does the general reading/research, cuts
        the epic branch, and asks the user for decisions/clarifications as needed.

    2.2. Delegation per workstream

        The coordinator does NOT implement (the standing rule — [#intro], [#1]).
        It spins one IMPLEMENTER subagent per workstream — ideally each scoped by
        its own GitHub issue — and runs the [#1.3] role split for each: the
        implementer stops at PR-open, the only topology change being that its
        draft PR targets the EPIC branch (not `main`); the coordinator owns the
        wait and the flip; a fresh shepherd handles each addressing round. The
        round-cap / no-major+ breaker ([#1.4]) applies to every workstream PR.

    2.3. Integration

        The COORDINATOR merges each workstream PR into the epic branch — its own
        go/no-go, no user approval needed for these intra-epic merges. This is
        the one place the coordinator merges: it merges workstreams INTO the epic
        branch, never the epic branch into `main`. The user's approval gate is
        the umbrella PR ([#2.6]), not the individual workstreams.

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
          trade-offs, pointers, and heads-ups.

    2.6. The umbrella PR

        With the work and docs in, the coordinating agent opens the feature's
        umbrella PR. It:

        1. double-checks which issues the PR actually closes (and which it does
           not);
        2. writes a high-level description of the whole epic, pointing to the
           related issues;
        3. drives the PR (epic branch → `main`) through the same
           `gh-pr-review-loop` discipline ([#1.3]) under the same split — the
           coordinator waits and flips, a fresh shepherd handles each review
           round — then flips it to READY and stops. The HUMAN merges the
           umbrella PR to `main`; the coordinator does not auto-merge it (merge
           only on explicit authorization).

    2.7. Release

        When the user merges the umbrella PR, the coordinating agent cuts the
        release(s) (`release-core cut`), using the realized epic as the guide
        for whether to bump MINOR or PATCH — only the user requests a MAJOR (see
        the versioning contract). If the project has chained dependencies — e.g.
        a CLI that a downstream desktop app bundles — the cascade runs all the
        way through: release each repo in dependency order.
