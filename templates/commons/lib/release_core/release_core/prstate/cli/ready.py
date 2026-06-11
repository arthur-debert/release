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
from ..state import TaskState, evaluate
from .review import _resolve_pr
from .task_status import emit

USAGE = """\
release-core pr ready — flip a PR draft->ready, guarded by the state engine.

Usage:
  release-core pr ready [<pr-number>] [--undo]

With no <pr-number>, resolves the PR for the current branch.

Without --undo, reads the state engine first and flips ONLY when the PR is
READY (reviews done + threads resolved + CI green + mergeable). Any other
state refuses with exit 1 and prints the engine's status + next action — an
agent cannot hand off early. Flipping an already-ready READY PR is a no-op
success.

With --undo, flips ready->draft unconditionally (the human asked for changes;
the agent takes its turn back).

Options:
  --undo     flip ready->draft (always allowed)
  -h --help  show this help

Exit codes:
  0   flipped (or already in the target state)
  1   refused (state is not READY), or gh failure
  64  bad usage
"""


def main(argv: list[str]) -> int:
    pr_arg: str | None = None
    undo = False
    for arg in argv:
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg == "--undo":
            undo = True
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
        return _undo(pr) if undo else _flip(pr)
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


def _flip(pr: int) -> int:
    """draft->ready, ONLY when the engine reads READY."""
    ctx = gather(pr)
    status = evaluate(ctx, diff_sizer=gitstat.diff_sizer(ctx.base_ref))
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
        "note: GitHub re-runs checks on the ready_for_review event, so status "
        "may briefly read VALIDATING — run `release-core pr wait` once more "
        "if you need green confirmation"
    )
    return 0
