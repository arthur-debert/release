Development Workflow State Engine:
A reviewer-agnostic model for driving PRs to Ready

Source location: arthur-debert/release, branch main, path docs/proposals/. Builds on foundational-gh-layer.lex (problem analysis), pr-review-loop-circuit-breakers.md (breaker heuristics), and agentic-dev-workflow.lex (the surrounding development lifecycle). Companion implementation lives under lib/release_gh/ and orchestrator/.

1. Context

    1.1. The problem

        foundational-gh-layer.lex lays it out: in no environment does the PR flow work smoothly. Agents cannot reliably tell which reviews are requested, pending, or done; cannot gather and resolve comments; cannot tell when a PR is mergeable. The friction is concentrated in two places — reviewer mechanics differ per reviewer and per environment, and there is no single command that answers "where does this PR stand."

    1.2. The design constraint that shapes everything

        The landscape of coding-agent quality, capability, and cost is in constant flux. Today the reviewers are Copilot and Gemini; tomorrow one is replaced, a third is added, or the way one signals a finished review changes. A design that bakes "Copilot" into its core will need rewriting every time the landscape shifts.

        The response is to separate what is stable from what is volatile. The stable core is the shape of a reviewed PR — the state machine and the two definitions below. The volatile part — which reviewers exist and how each one is requested and read — is pushed into swappable adapters. The core never names a reviewer.

    1.3. What "done" means

        Two definitions anchor the machine:

        Reviewed:
            Every *required* reviewer has finished, and every comment has been addressed — fix-or-reply — and its thread resolved. Best-effort reviewers that did not respond are noted, not blocking.

        Ready:
            Reviewed, plus CI checks green, plus mergeable. Only at Ready does the PR flip from draft to ready and the human gate open.

        One assumption is carried over from foundational-gh-layer.lex: a single code-then-review cycle (the first addressing is not itself re-reviewed). The code is structured so cycles can be added later, but the machine is specified for one.

2. Architecture

    Three layers. One is the stable core; one isolates the flux; one is fully separable.

    2.1. Reviewer adapter — isolates the flux

        Each reviewer is a declarative adapter. This is the *only* place that knows reviewer-specific mechanics. Adding a reviewer is adding an adapter; the state machine does not change.

        Adapter shape:

            adapter = {
              name,
              requiredness:  required | best-effort,
              request(pr),                 # how a review is asked for (or noop if automatic)
              detect(pr) -> one of {
                not_requested, requested, in_progress,
                done_clean, done_comments
              },
              comments(pr) -> [ thread... ],
              timeout,
            }

        :: text ::

        The machine consumes only the adapter's interface — `requiredness`, `detect()`, `comments()`. It never branches on a reviewer's name. A new reviewer next quarter is a new adapter file and a one-line registry entry.

    2.2. State machine — the stable core

        `gh-task-status` is a pure, read-only snapshot. It computes where a PR stands and what the next action is. It does not mutate: it reports Ready, and the skill performs the draft-to-ready flip and pages the human. Keeping it side-effect-free is what makes it safe to call on every wake, in any environment, as often as needed.

    2.3. Transport — separable

        Transport is how the agent learns that state changed. It is independent of the machine.

        Polling:
            Available now. The existing wait primitives (gh-copilot-wait, gh-pr-checks-wait) block; the skill loop re-checks gh-task-status against them. The snapshot itself never waits.

        Events:
            A later milestone. orchestrator Phase B (gh-event-driven session wake-up) imports `release_gh.state`, so the webhook handler computes the exact same state the hand-driven agent does. Lower latency, fewer tokens. One state machine, two callers.

3. The state machine

    gh-task-status returns exactly one state with its next action:

    PR lifecycle:
        | State            | Means                                                          | Next action                        |
        | NO_PR            | no PR exists for the branch                                    | create draft PR                    |
        | REVIEWS_PENDING  | a required reviewer is not requested or not finished           | request missing / wait             |
        | ADDRESSING       | all required reviews landed; open threads remain              | triage: fix-or-reply, then resolve |
        | REVIEWED         | required reviewers done, all threads resolved                 | proceed to validation              |
        | VALIDATING       | reviewed; CI checks still running                             | wait on checks                     |
        | READY            | reviewed + CI green + mergeable                               | flip draft to ready, page human    |
        | BLOCKED          | check failing, merge conflict, or a breaker fired (STOP)      | surface to human                   |
    :: table align=lll ::

    Best-effort reviewers that time out (Gemini over quota) do not hold a PR in REVIEWS_PENDING; they are recorded as skipped and the machine advances. The circuit breakers from pr-review-loop-circuit-breakers.md (cycle count, diff trajectory, comment-set hash, repeat-finding) fold in as the STOP verdict inside BLOCKED — one API read, one command, rather than a second tool reading the same review history.

4. Reviewer adapters

    The central, uniform reviewer set for all consumers is Copilot plus Gemini. This is defined once in release/, not per-repo — accidental per-repo divergence is fixed in the consumer, not absorbed as configuration here.

    4.1. Copilot

        required:
            - request: gh pr edit --add-reviewer @copilot (the GraphQL path; the REST POST silently no-ops).
            - detect: a review object posted on the current head SHA. The head-SHA filter is load-bearing — submitted_at filtering misses reviews that landed before the wait began.
            - comments: inline review comments with stable ids, path, and line.

    4.2. Gemini

        best-effort:
            - request: automatic via the Gemini app; no explicit request call.
            - detect: an eyes reaction from the Gemini bot signals in_progress; bot comments signal done. Silence past the timeout records skipped(quota) and does not block Ready.
            - comments: issue/PR comments authored by the Gemini bot.

        Gemini is best-effort precisely because it goes over quota and signals weakly. Hard-requiring it would wedge every PR behind a flaky third party — the exact hang this work exists to kill.

5. Component inventory

    What is built new versus reused. The new modules live in the stdlib-only `release_gh` package; the reused primitives already sit in bin/.

    Inventory:
        | Component                                                  | Status                              |
        | release_gh.ghapi — gh subprocess + JSON wrappers           | new (removes the shell duplication) |
        | release_gh.reviewers — Copilot + Gemini adapters           | new                                 |
        | release_gh.state — the lifecycle machine                   | new                                 |
        | release_gh.breakers — cycle / diff / comment-set / repeat  | new (proposal exists, unbuilt)      |
        | release_gh.comments — list open threads, reply to a thread | new (only resolve exists today)     |
        | bin/gh-task-status — thin shim                             | new                                 |
        | gh-copilot-on / gh-copilot-wait                            | reuse                               |
        | gh-pr-checks-wait / gh-pr-resolve-thread                   | reuse                               |
        | orc probe (fresh-agent verification) / orc propagate       | reuse                               |
    :: table align=ll ::

6. Why Python, and the dependency boundary

    6.1. Why

        release/ is some 6k lines of shell. The PR-review history on it shows the cost: the overwhelming majority of review findings are bash-versus-zsh edge cases, quoting and safety traps, and the awkwardness of doing JSON in shell — rarely logic errors. Reuse is unnatural in shell, so duplication accumulates.

        This new layer is the worst possible fit for shell and the best possible fit for Python: it parses GitHub REST and GraphQL JSON, dispatches across reviewer adapters, computes a state machine, and runs breaker arithmetic (comment-set hashing, diff-size ratios). In Python that is `json.loads` and a dict; in shell it is a jq-and-quoting minefield. Python also opens the door to real unit testing of the logic.

        This is not a rewrite of release/. It is a new layer written in Python, on a clean greenfield slice. If the pattern pays off, file-by-file migration of existing shell becomes a separate, evidence-backed effort.

    6.2. The dependency boundary is the environment boundary

        The hard rule: anything that runs in GitHub Actions or Claude Cloud uses the standard library and `gh` only — no pip dependencies. python3 is present in all three environments, but a runtime pip install would add a provisioning step everywhere and break the "drop it in bin/, it just works" contract. The state engine shells out to `gh` for every API call (gh already handles auth, rate limits, pagination) and parses with stdlib `json`. It never speaks HTTP itself.

        The orchestrator is exempt because it is local-only. It is allowed uv and the Claude Agent SDK precisely because it never runs in CI or Cloud. The dependency line follows the environment line.

    6.3. Layout — one uv workspace, two packages

        Two packages, two members of one workspace:

        - `lib/release_gh/` — stdlib only, no dependencies. The state engine. Its bin/ shims run anywhere.
        - `orchestrator/` — depends on `release_gh` plus claude-agent-sdk. The local harness; Phase B imports `release_gh.state`.

        ruff, pytest, and a lefthook py-lint hook are configured once at the workspace root and cover both. The workspace gives shared toolchain with isolated dependencies — the must-run-everywhere code stays clean while the local harness keeps its SDK.

    6.4. Packaging — thin shims

        bin/ entrypoints stay runnable-by-name on PATH with no install step, so they survive the .release/ symlink materialization and run in CI/Cloud unchanged. Each is a few lines: resolve its own realpath, insert the lib directory onto sys.path, import `release_gh`, dispatch. The package is importable two ways — via sys.path at runtime (no install), via the workspace for tests and the orchestrator.

        Shim shape:

            #!/usr/bin/env python3
            import sys, os
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                "..", "lib", "release_gh"))
            from release_gh.cli import task_status
            task_status.main(sys.argv[1:])

        :: python ::

7. Build order

    State-engine-first. The snapshot is needed regardless of transport, and it is unblocked — it does not depend on the events investigation. Events are a latency optimization layered on later with no rework.

    7.1. Workspace and read layer

        Stand up the uv workspace; build `ghapi`, `reviewers` (both adapters), and `comments` (list + reply). Add the ruff hook to lefthook. Tests: pytest over recorded real-PR JSON fixtures — a Copilot review, a Gemini eyes-reaction, and the quota'd-silent case. Outcome: any PR's review state is machine-readable.

    7.2. State snapshot

        Build `state.py` and bin/gh-task-status, breakers excluded. Tests: BATS for the command contract, plus orc probe against a real release/ PR. Outcome: "where does this PR stand" answerable in one command.

    7.3. Breakers

        Fold the circuit-breaker heuristics in as the STOP verdict.

    7.4. Skill wiring

        Update the review-loop skill to call gh-task-status as its orient step, flip draft-to-ready and page the human on READY.

    7.5. Events (later milestone)

        orchestrator Phase B wake-up over `release_gh.state`, replacing polling with webhook-driven wake-up.

8. Testing strategy

    Three tiers, matching the unit-versus-E2E doctrine in CLAUDE.md:

    pytest — logic:
        Pure functions over *captured* gh JSON: every state transition, each adapter's detect() against real payloads including the quota'd-Gemini case, and the breaker arithmetic. Fixtures are recorded from real PRs (including this repo's own), never hand-written, so the JSON is real. Runs at dev and CI time, where pip is fine — pytest never enters the runtime path.

    BATS — command contract:
        Args, exit codes, stdout shape. It is ultimately a PATH command, so the command surface is verified as one.

    orc probe — empirical:
        A fresh agent drives a throwaway PR through the loop end-to-end, exercising the live edge cases foundational-gh-layer.lex mandates: Copilot requested or not, a killed review, Gemini over quota. The fresh agent's empirical experience is the integration test.

9. Decisions resolved

    Settled during design; recorded so they are not relitigated.

    - State engine first; polling now, events later. The snapshot is transport-agnostic and unblocked.
    - Expected reviewers defined centrally and uniformly in release/ (Copilot + Gemini), not per-repo.
    - Gemini is best-effort and degrades gracefully; a silent or quota'd Gemini does not block Ready. Copilot is required.
    - Circuit breakers fold into gh-task-status as the STOP verdict, not a separate tool.
    - The new layer is Python; release/ is not rewritten. Greenfield slice first; migration of existing shell is a later, separate effort.
    - Runtime is stdlib + gh only. The dependency line follows the environment line; the orchestrator's SDK deps are allowed because it is local-only.
    - One uv workspace, two packages: `release_gh` (stdlib) and `orchestrator` (SDK). Shared ruff/pytest/lefthook, isolated dependencies.
    - bin/ entrypoints are thin sys.path shims — no install step, run in CI/Cloud unchanged.

10. Out of scope

    - Multi-round review (re-reviewing the first addressing). The machine assumes one cycle; the code leaves room to add more.
    - The same-file-opposite-change revert breaker (textual revert only for v1), per pr-review-loop-circuit-breakers.md.
    - Migrating existing release/ shell to Python. Conditional on this slice proving out.
    - Event transport beyond the orchestrator Phase A spike. Phase B is its own research question.
