"""`release-core pr ready` — the engine-owned draft<->ready flip (#456).

The draft flag encodes whose turn it is: draft = the agent is still working
it; ready-for-review = handed to the human. This command is the only
sanctioned way the loop flips the flag forward: it reads the state engine
(`fetch.gather()` -> `state.evaluate()`) and refuses unless the PR is READY,
so an agent cannot hand off early by design. The reverse flip (`--undo`) is
unconditional — re-work is always allowed once the human asks for changes.

`evaluate()` stays pure (it *reports* READY); the mutation lives here in the
act layer, behind `ghapi.pr_ready`.
"""

from __future__ import annotations

import sys

from .. import ghapi, gitstat
from ..fetch import gather
from ..reviewers import required_reviewers
from ..state import TaskState, evaluate
from .review import _resolve_pr
from .task_status import emit

USAGE = """\
release-core pr ready — flip a PR draft->ready, guarded by the state engine.

Usage:
  release-core pr ready [<pr-number>] [--undo] [--ack-cycle-cap]

With no <pr-number>, resolves the PR for the current branch.

Without --undo, reads the state engine first and flips ONLY when the PR is
READY (reviews done + threads resolved + CI green + mergeable). Any other
state refuses with exit 1 and prints the engine's status + next action — an
agent cannot hand off early. Flipping an already-ready READY PR is a no-op
success.

When the cycle-cap circuit breaker has fired on an OTHERWISE-ready PR (0 open
threads + CI green + CLEAN merge), the engine reports BLOCKED and routes you
here with --ack-cycle-cap. That flag acknowledges the cap so the flip
proceeds — but ONLY the cap is waived: every other readiness gate (open
threads, CI, merge state) still applies, so you never drop to a raw
`gh pr ready` that bypasses all of them. It is a no-op on a PR the cap did not
trip.

With --undo, flips ready->draft unconditionally (the human asked for changes;
the agent takes its turn back).

Options:
  --undo            flip ready->draft (always allowed)
  --ack-cycle-cap   acknowledge a fired cycle-cap and flip an otherwise-ready PR
  -h --help         show this help

Exit codes:
  0   flipped (or already in the target state)
  1   refused (state is not READY), or gh failure
  64  bad usage
"""


def main(argv: list[str]) -> int:
    pr_arg: str | None = None
    undo = False
    ack_cycle_cap = False
    for arg in argv:
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg == "--undo":
            undo = True
        elif arg == "--ack-cycle-cap":
            ack_cycle_cap = True
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

    try:
        pr = _resolve_pr(int(pr_arg) if pr_arg is not None else None)
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if pr is None:
        print("error: could not resolve a PR for the current branch", file=sys.stderr)
        return 1

    try:
        return _undo(pr) if undo else _flip(pr, ack_cycle_cap=ack_cycle_cap)
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _undo(pr: int) -> int:
    """ready->draft, unconditionally: re-work is always allowed.

    Unconditional means "never gated on the ENGINE" — a cheap metadata
    pre-check keeps the output truthful (an already-draft PR is a no-op
    success, not a fresh "ready -> draft" claim).
    """
    if ghapi.pr_meta(pr).get("isDraft"):
        print(f"#{pr} is already a draft — nothing to flip")
        return 0
    ghapi.pr_ready(pr, undo=True)
    print(f"#{pr}: ready -> draft — the agent takes its turn back")
    return 0


def _flip(pr: int, *, ack_cycle_cap: bool = False) -> int:
    """draft->ready, ONLY when the engine reads READY.

    `ack_cycle_cap` waives ONLY a fired cycle-cap breaker (release#738): the
    engine still enforces every other readiness gate, so the flip lands only if
    the PR is otherwise genuinely converged. Without the flag a cap-blocked PR
    refuses as before and the engine's status names the ack route.
    """
    ctx = gather(pr)
    status = evaluate(
        ctx,
        diff_sizer=gitstat.diff_sizer(ctx.base_ref),
        required=required_reviewers(),
        ack_cycle_cap=ack_cycle_cap,
    )
    if status.state is not TaskState.READY:
        print(
            f"refusing to flip #{pr}: state is {status.state.value.upper()}, not READY",
            file=sys.stderr,
        )
        emit(status)
        return 1
    if not ctx.is_draft:
        print(f"#{pr} is already ready-for-review (and READY) — nothing to flip")
        return 0
    ghapi.pr_ready(pr)
    print(f"#{pr}: draft -> ready — handed to the human for verify + merge")
    print(
        "note: this is done — the human merges from here. GitHub re-runs checks "
        "on the ready_for_review event, so a brief VALIDATING status right after "
        "the flip is normal and expected, not a failure. No further action is "
        "needed; `release-core pr wait` will re-confirm green if you want to watch it."
    )
    return 0
