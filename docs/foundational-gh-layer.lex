Development Flow

How It Should Work

    The development workflow is designed to accomplish the following:

    - Agentic implementation and review.
    - PR Ready triggers the human gate.

    The core point: a PR authored by an agent with no review addressed is not a full implementation. Reviews catch subtle things but also crude ones (i.e. the core feature was not implemented), which informs the human gate.

    Flow: agent pushes PR -> reviewers start -> reviews come in -> agent addresses them -> CI checks pass -> PR is ready (gated for human merge).

    This flow simplifies one assumption: that there is only one cycle of code -> review (not that the first addressing can be reviewed again). This is an OK assumption for now, but plan the code so cycles can be added later.

    To get this right, agents need to know how to:
    - Submit PRs (works)
    - Request reviewers if necessary
    - Verify whether reviews are requested, started, or finished
    - Gather unresolved comments
    - Submit review addressing
    - Resolve each addressed comment (either by fix + comment or pushback + rationale)
    - Verify the PR is mergeable (no merge conflicts, no failing checks)

    A PR with all expected reviewers submitted and addressed, and that is mergeable, is PR Ready. This is when the human gate enters.

How It Currently Works

    Now, either always or in one of the environments, agents cannot reliably:
    - Request reviewers and verify they were requested
    - Detect new reviews (either via auto-event or polling)
    - Resolve comments
    - Tell when the PR is mergeable

The Friction Points

    There is no formal definition of expected reviewers. Review requests are triggered by:

    Copilot:
        Sometimes by repo policy file, sometimes manually, sometimes only on PR ready versus on draft.
    Gemini:
        Currently triggered automatically by the Gemini app, which at some point goes over quota. While the Copilot review is a per-one event, Gemini comments on the issue with an eye emoji only.
    Claude Code Cloud auto-fix:
        When running in Claude Cloud, there is a Routine that listens to PRs and wakes agents waiting for events. When configured correctly this works for all but test check runs (reviews and status changes, not PR checks finished). When running locally, no such thing exists so agents need to poll and monitor on their own.
    Claude Code GitHub MCP issues:
        The cloud environment does internal GitHub MCP work and breaks a few things: the ability to resolve PR comment threads and comment on them. Our workaround is to install gh in env/setup.sh and let the agent know via skill instructions. Works most of the time.
    Copilot on drafts:
        We want Copilot reviews on every PR, even drafts. This can be enabled via repo policy but is either not enabled or is inconsistent.

The End Result

    In no environment does the flow work smoothly. The core needs are:

    - Standardize review requests.
    - Provide working, tested tools that tell agents: reviews requested, pending, done. Ditto for comments: list, reply, resolve.
    - Standardize PR event delivery. Polling can work but 9 out of 10 times agents do it wrong and hang forever. Event-based delivery (routines) is preferable.

Way Forward

    Two foundational pieces:

    4.1. gh-task-status

        A single command that tells the agent where it stands. If there is no PR, one must be created. If a PR exists: which reviews are not requested, pending, submitted, or resolved. If all reviews are resolved, show CI check status. If everything passes, report PR Ready.

        Should accept --pr for the rare case an agent manages multiple PRs. Orchestrates existing primitives (gh-copilot-wait, gh-pr-resolve-thread, gh-pr-checks-wait) rather than replacing them.

    4.2. Event listener

        A routines-based (or equivalent) event listener that processes each PR event and messages the agent. This removes the need for brittle polling.

    With these two bedrocks solid, we can build reliable development flow on any environment. To get there we must be disciplined: test all edge cases (Copilot requested or not, killed review, etc.) using the release repo itself, spawning sub-agents with mock tasks to exercise the full flow.

    Unless these two become solid, we remain in a brittle place.
