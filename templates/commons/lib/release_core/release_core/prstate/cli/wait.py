"""`release-core pr wait` — the engine-owned wait (release#503).

ONE wait for the whole PR loop. It replaces both `pr review wait` (the
retired 7m-initial / 2m-poll copilot wait) and `pr checks-wait` (the
`gh pr checks --watch` shim): instead of waiting on a *named thing*, it polls
the state engine (fetch context -> `evaluate()`) and returns the moment the
snapshot calls for agent action. The engine knows WHAT is being waited on —
a requested review, running checks, mergeability computing — and its
`next_action` says so; nothing here names a reviewer or a check.

Semantics:

- Agent-action states (ADDRESSING, BLOCKED, READY, NO_PR) exit immediately:
  there is nothing to wait for. So does a REVIEWS_PENDING snapshot where a
  pending required reviewer still needs a (re-)request — placing the request
  is agent action, not waiting.
- Waiting states (REVIEWS_PENDING with every pending required reviewer
  already requested/in-progress on the head, VALIDATING, REVIEWED) block
  IN-TURN, re-polling until agent action is available, then print the new
  state + next action (the same rendering as `pr status`). Transitions
  between waiting states (review lands -> CI still running) are reported and
  the wait keeps going: exit 0 always means "the agent can act NOW".
- A mid-wait poll that fails (transient gh/network blip) is retried
  POLL_RETRIES times with backoff — logged to stderr, never silent — before
  the wait gives up with exit 1 (release#582). The first fetch still fails
  fast: that's where the real errors (bad PR number, auth) surface.

Cadence is data, not magic constants buried in logic: the module-level
constants below are the single source, and `--poll` / `--timeout` override
them per-invocation.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable

from .. import ghapi, gitstat
from ..fetch import gather
from ..ghapi import GhError
from ..model import ReviewLifecycle
from ..reviewers import ReviewerAdapter, required_reviewers
from ..state import TaskState, TaskStatus, evaluate, no_pr
from .task_status import emit

# --- cadence (the single source; --poll/--timeout override) ------------------
#
# The old 7m-initial-sleep rationale is stale — Copilot is often much faster
# now (release#503) — so polling starts quickly and backs off gently, keeping
# fast reviews cheap without hammering the API across a long CI run.
POLL_INITIAL_S = 20.0  # first re-poll interval
POLL_BACKOFF = 1.5  # interval multiplier per poll (disabled by --poll)
POLL_MAX_S = 90.0  # backoff cap
MAX_WAIT_S = 45 * 60.0  # overall cap — generous enough for review + CI
POLL_RETRIES = 3  # transient mid-wait poll failures retried before giving up
POLL_RETRY_BACKOFF_S = 5.0  # retry sleep grows linearly: 5s, 10s, 15s

# States the wait blocks through; everything else is agent action.
# REVIEWS_PENDING is conditional — see _agent_action_needed.
_WAITING_STATES = {TaskState.REVIEWS_PENDING, TaskState.VALIDATING, TaskState.REVIEWED}

# ReviewLifecycle values as they appear in TaskStatus.reviewers.
_DONE_VALUES = {ReviewLifecycle.DONE_CLEAN.value, ReviewLifecycle.DONE_COMMENTS.value}
_PENDING_ON_HEAD_VALUES = {ReviewLifecycle.REQUESTED.value, ReviewLifecycle.IN_PROGRESS.value}

USAGE = """\
release-core pr wait — block until the PR needs the agent again.

Usage:
  release-core pr wait [<pr-number>] [--poll <seconds>] [--timeout <seconds>]

THE one wait of the PR loop (it replaced `pr review wait` and `pr
checks-wait`): polls the state engine and returns as soon as the snapshot
calls for agent action. Exits immediately when there is already nothing to
wait for — ADDRESSING, BLOCKED, READY, NO_PR, or a required review that still
needs to be (re-)requested. In a waiting state (a requested review pending,
CI running, mergeability computing) it blocks IN-TURN — this is how an agent
waits without yielding — re-polling until agent action is available, then
prints the new state + next action (the same rendering as `pr status`).

With no <pr-number>, resolves the PR for the current branch.

Options:
  --poll <seconds>     fixed poll interval (default: 20s, growing 1.5x per
                       poll up to a 90s cap)
  --timeout <seconds>  overall cap (default: 2700 = 45m)
  -h --help            show this help

Exit codes:
  0   agent action available now (including READY)
  1   gh failure
  2   timeout cap hit with nothing actionable yet
  64  bad usage
"""


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if isinstance(parsed, int):
        return parsed
    pr_arg, poll, timeout = parsed

    try:
        pr = pr_arg if pr_arg is not None else _resolve_pr()
        if pr is None:
            # No PR for the branch: that IS agent action (create the draft PR).
            emit(no_pr())
            return 0
        return wait_for_action(pr, poll=poll, timeout=timeout)
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _resolve_pr() -> int | None:
    """The current branch's PR number, or None when the branch has none.

    None is reserved for gh's well-known "no pull requests found" answer —
    that IS the NO_PR state. Every other `gh` failure (missing gh, auth,
    transient API errors) propagates as GhError so the caller exits 1 per the
    contract, never as a misleading NO_PR. If gh ever rewords the no-PR
    message, the failure mode is loud (exit 1 with gh's real message), not a
    wrong state.
    """
    try:
        data = json.loads(ghapi._gh(["pr", "view", "--json", "number"]))
    except GhError as exc:
        if "no pull requests found" in str(exc).lower():
            return None
        raise
    except json.JSONDecodeError as exc:
        raise GhError(f"unparseable `gh pr view` output: {exc}") from exc
    number = data.get("number")
    return int(number) if number is not None else None


def wait_for_action(
    pr: int,
    *,
    poll: float | None = None,
    timeout: float | None = None,
    registry: list[ReviewerAdapter] | None = None,
    snapshot: Callable[[int], TaskStatus] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Poll the state engine until the snapshot calls for agent action.

    `poll` fixes the interval (no backoff); default is the module cadence.
    `registry`/`snapshot`/`sleep`/`clock` are injectable for tests. `registry`
    is the required-reviewer classifier set the request-vs-wait split reads —
    default the config-resolved required set (release#622), a test injects its
    own to drive the split off arbitrary reviewers.
    """
    required = registry if registry is not None else required_reviewers()
    take = snapshot if snapshot is not None else _snapshot
    deadline_cap = MAX_WAIT_S if timeout is None else timeout
    fixed = poll is not None
    interval: float = poll if poll is not None else POLL_INITIAL_S

    start = clock()
    deadline = start + deadline_cap

    # The next sleep, capped to the time left: a poll interval longer than the
    # remaining budget must not carry the wall clock past --timeout. The max()
    # guards a real monotonic clock advancing past the deadline between the
    # loop condition and the computation — time.sleep rejects negatives. The
    # progress line prints this same clamped value, never the raw interval.
    def next_sleep() -> float:
        return max(0.0, min(interval, deadline - clock()))

    # The FIRST fetch fails fast: a GhError here is where the real errors live
    # (bad PR number, auth, missing gh) and retrying them only delays the loud
    # failure. Once a poll has succeeded, mid-wait failures are overwhelmingly
    # transient (TLS handshake timeout, API blip) — those go through
    # _poll_with_retry below.
    status = take(pr)
    if _agent_action_needed(status, required):
        emit(status)
        return 0
    print(_cadence_line(poll=poll, timeout=deadline_cap))
    print(_waiting_line(status, elapsed=0.0, interval=next_sleep()))

    while clock() < deadline:
        sleep(next_sleep())
        if not fixed:
            interval = min(interval * POLL_BACKOFF, POLL_MAX_S)
        status = _poll_with_retry(take, pr, sleep=sleep)
        if _agent_action_needed(status, required):
            print(f"-- agent action available after {_fmt(clock() - start)} --")
            emit(status)
            return 0
        print(_waiting_line(status, elapsed=clock() - start, interval=next_sleep()))

    # One last look before declaring timeout: the state can flip during the
    # sleep that carries the clock past the deadline, and exiting on the stale
    # snapshot would falsely report a timeout.
    status = _poll_with_retry(take, pr, sleep=sleep)
    if _agent_action_needed(status, required):
        print(f"-- agent action available after {_fmt(clock() - start)} --")
        emit(status)
        return 0
    print(
        f"timeout: nothing actionable on #{pr} after {_fmt(deadline_cap)} "
        f"(state: {status.state.value}; next: {status.next_action})",
        file=sys.stderr,
    )
    return 2


# --- snapshot + classification ------------------------------------------------


def _snapshot(pr: int) -> TaskStatus:
    """One engine evaluation: fetch the PR context, run `evaluate()`.

    Resolves the required reviewer set here (cached) and passes it in, so the
    engine call stays pure — config resolution is the entrypoint's job.
    """
    ctx = gather(pr)
    return evaluate(ctx, diff_sizer=gitstat.diff_sizer(ctx.base_ref), required=required_reviewers())


def _poll_with_retry(
    take: Callable[[int], TaskStatus],
    pr: int,
    *,
    sleep: Callable[[float], None],
) -> TaskStatus:
    """One mid-wait poll, retried on failure (release#582).

    The wait's whole job is to be left alone for minutes, so a single
    transient gh/network blip (`net/http: TLS handshake timeout`, a 5xx)
    must not kill it. No error taxonomy: the structural distinction is
    positional — real errors (bad PR number, auth) surface on the FIRST
    fetch, which fails fast in `wait_for_action`; by the time this runs, a
    poll has already succeeded, so a GhError is presumed transient. Retry
    POLL_RETRIES times with linear backoff, logging each failure to stderr
    so the blip is visible, and re-raise only when it persists (-> exit 1).
    The retry sleeps are bounded (~30s total) and deliberately not clamped
    to the --timeout budget — the post-deadline final look still runs.
    """
    failures = 0
    while True:
        try:
            return take(pr)
        except GhError as exc:
            failures += 1
            if failures > POLL_RETRIES:
                raise
            backoff = POLL_RETRY_BACKOFF_S * failures
            print(
                f"poll failed (retry {failures}/{POLL_RETRIES} in {_fmt(backoff)}): {exc}",
                file=sys.stderr,
            )
            sleep(backoff)


def _agent_action_needed(status: TaskStatus, required: list[ReviewerAdapter]) -> bool:
    """True when the snapshot calls for agent action NOW — nothing to wait on.

    Every non-waiting state is agent action. REVIEWS_PENDING splits: a pending
    required reviewer already REQUESTED / IN_PROGRESS on the head is genuinely
    pending (wait); one that is not — never requested, or stale after a push —
    needs a (re-)request, and placing it is the agent's move.
    """
    if status.state not in _WAITING_STATES:
        return True
    if status.state is not TaskState.REVIEWS_PENDING:
        return False
    return _needs_request(status, required)


def _needs_request(status: TaskStatus, required: list[ReviewerAdapter]) -> bool:
    """A pending required reviewer is not requested on the head -> request it.

    Reads the config-resolved required SET (release#622), not the whole
    registry — a best-effort reviewer never drives a (re-)request.
    """
    for adapter in required:
        lifecycle = status.reviewers.get(adapter.name)
        if lifecycle is None or lifecycle in _DONE_VALUES:
            continue
        if lifecycle not in _PENDING_ON_HEAD_VALUES:
            return True
    return False


# --- rendering ------------------------------------------------------------------


def _cadence_line(*, poll: float | None, timeout: float) -> str:
    """Printed once on entering the wait: the cadence in effect and its knobs."""
    if poll is not None:
        cadence = f"polling every {_fmt(poll)} (fixed via --poll)"
    else:
        cadence = (
            f"polling every {_fmt(POLL_INITIAL_S)}, backing off x{POLL_BACKOFF:g} "
            f"to a {_fmt(POLL_MAX_S)} cap (--poll <s> fixes the interval)"
        )
    return f"cadence: {cadence}; giving up after {_fmt(timeout)} (--timeout <s> overrides)"


def _waiting_line(status: TaskStatus, *, elapsed: float, interval: float) -> str:
    """One progress line: the engine's own words on what is being waited on."""
    return (
        f"waiting [{status.state.value}] {status.next_action} "
        f"({_fmt(elapsed)} elapsed; next poll in {_fmt(interval)})"
    )


def _fmt(seconds: float) -> str:
    """Compact duration: 45s, 1m20s, 45m."""
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        return f"{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


# --- argv -----------------------------------------------------------------------


def _parse(argv: list[str]) -> tuple[int | None, float | None, float | None] | int:
    """Parse `[<pr>] [--poll <s>] [--timeout <s>]`; return values or an exit code."""
    pr_arg: str | None = None
    poll: float | None = None
    timeout: float | None = None
    it = iter(argv)
    for arg in it:
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg == "--poll" or arg.startswith("--poll="):
            value = arg.split("=", 1)[1] if "=" in arg else next(it, None)
            poll = _positive_seconds("--poll", value)
            if poll is None:
                return 64
        elif arg == "--timeout" or arg.startswith("--timeout="):
            value = arg.split("=", 1)[1] if "=" in arg else next(it, None)
            timeout = _positive_seconds("--timeout", value)
            if timeout is None:
                return 64
        elif arg.startswith("-"):
            print(f"error: unknown option {arg}", file=sys.stderr)
            return 64
        elif pr_arg is None:
            pr_arg = arg
        else:
            print("error: too many arguments", file=sys.stderr)
            return 64
    if pr_arg is not None and not pr_arg.isdigit():
        print(f"error: PR number must be numeric (got: {pr_arg})", file=sys.stderr)
        return 64
    return (int(pr_arg) if pr_arg is not None else None, poll, timeout)


def _positive_seconds(flag: str, value: str | None) -> float | None:
    if value is None:
        print(f"error: {flag} needs a number of seconds", file=sys.stderr)
        return None
    try:
        seconds = float(value)
    except ValueError:
        print(f"error: {flag} must be a number of seconds (got: {value})", file=sys.stderr)
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        print(f"error: {flag} must be a positive, finite number (got: {value})", file=sys.stderr)
        return None
    return seconds
