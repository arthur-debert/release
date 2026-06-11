"""`release-core pr review` — the reviewer-agnostic act surface.

request / cancel / show over the reviewer-adapter registry. Nothing
here branches on a reviewer's name: the default scope is the *required*
reviewer set, `--reviewer <name>` selects one adapter by registry name, and
every per-bot mechanic (how a request is placed, what "done" looks like)
lives behind the `ReviewerAdapter` interface in `prstate.reviewers`.

Commands (each is a `main(argv) -> int`, wrapped into the click tree by
`release_core.cli.pr`):

  request — request (or re-request) review(s); no-op backends say so.
  cancel  — withdraw pending review request(s).
  show    — read-only: print posted review(s) + ALL review threads.

Waiting is NOT here: the engine-owned `pr wait` (release#503, `cli/wait.py`)
replaced the per-reviewer `review wait` — the state engine knows what is
being waited on, so there is one wait for the whole loop.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from .. import ghapi
from ..fetch import gather
from ..model import PullContext
from ..reviewers import REGISTRY, ReviewerAdapter, by_name, required_reviewers

_USAGE_COMMON = """\
With no <pr-number>, resolves the PR for the current branch. Default scope is
every REQUIRED reviewer in the adapter registry; --reviewer <name> selects one
adapter by name (required or best-effort).

Options:
  --reviewer <name>  act on one reviewer (an adapter registry name)
  -h --help          show this help
"""

USAGE_REQUEST = f"""\
release-core pr review request — request (or re-request) review(s) on a PR.

Usage:
  release-core pr review request [<pr-number>] [--reviewer <name>]

Re-requesting after a fixup push is the same command — the backend call is
identical. Reviewers without a request mechanism (auto-triggering, best-effort
backends) report a no-op rather than failing.

{_USAGE_COMMON}
Exit codes:
  0   request(s) placed (or no-op for no-mechanism reviewers)
  1   gh failure
  64  bad usage
"""

USAGE_CANCEL = f"""\
release-core pr review cancel — withdraw pending review request(s) on a PR.

Usage:
  release-core pr review cancel [<pr-number>] [--reviewer <name>]

{_USAGE_COMMON}
Exit codes:
  0   request(s) withdrawn (or no-op for no-mechanism reviewers)
  1   gh failure
  64  bad usage
"""

USAGE_SHOW = f"""\
release-core pr review show — print posted review(s) + all threads. Read-only.

Usage:
  release-core pr review show [<pr-number>] [--reviewer <name>]

Prints whatever review(s) the selected reviewer(s) have ALREADY posted, then
EVERY review thread on the PR — any author, human or bot: thread accounting
is reviewer-agnostic, an unresolved thread blocks readiness no matter who
opened it. Never requests or waits (absence of a review is not an error).

{_USAGE_COMMON}
Exit codes:
  0   printed (even if no review is posted yet)
  1   gh failure
  64  bad usage
"""


# --- shared argv handling ---------------------------------------------------


def _parse(argv: list[str], usage: str) -> tuple[int | None, str | None] | int:
    """Parse `[<pr>] [--reviewer <name>]`; return (pr, reviewer) or an exit code."""
    pr_arg: str | None = None
    reviewer: str | None = None
    it = iter(argv)
    for arg in it:
        if arg in ("-h", "--help"):
            print(usage)
            return 0
        if arg == "--reviewer":
            reviewer = next(it, None)
            if reviewer is None:
                print("error: --reviewer needs a name", file=sys.stderr)
                return 64
        elif arg.startswith("--reviewer="):
            reviewer = arg.split("=", 1)[1]
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
    return (int(pr_arg) if pr_arg is not None else None, reviewer)


def _select(reviewer: str | None) -> list[ReviewerAdapter] | None:
    """The adapters to act on: all required ones, or the one named."""
    if reviewer is None:
        return required_reviewers()
    adapter = by_name(reviewer)
    if adapter is None:
        known = ", ".join(r.name for r in REGISTRY)
        print(f"error: unknown reviewer {reviewer!r} (known: {known})", file=sys.stderr)
        return None
    return [adapter]


def _resolve_pr(pr: int | None) -> int | None:
    """The given PR number, or the current branch's PR.

    Raises `ghapi.GhError` when `gh` itself fails (missing gh, auth, no PR for
    the branch, transient API errors) — the caller reports that as a gh
    failure (exit 1), never as a usage error: swallowing it here would mask
    the real cause behind a misleading "no PR number given".
    """
    if pr is not None:
        return pr
    try:
        data = json.loads(ghapi._gh(["pr", "view", "--json", "number"]))
    except json.JSONDecodeError as exc:
        raise ghapi.GhError(f"unparseable `gh pr view` output: {exc}") from exc
    number = data.get("number")
    return int(number) if number is not None else None


def _setup(argv: list[str], usage: str) -> tuple[int, list[ReviewerAdapter]] | int:
    """Common front half of every command: parse, select, resolve the PR."""
    parsed = _parse(argv, usage)
    if isinstance(parsed, int):
        return parsed
    pr_arg, reviewer = parsed
    adapters = _select(reviewer)
    if adapters is None:
        return 64
    try:
        pr = _resolve_pr(pr_arg)
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if pr is None:
        print("error: could not resolve a PR for the current branch", file=sys.stderr)
        return 1
    return pr, adapters


# --- request / cancel -------------------------------------------------------


def request_main(argv: list[str]) -> int:
    front = _setup(argv, USAGE_REQUEST)
    if isinstance(front, int):
        return front
    pr, adapters = front
    return _act(pr, adapters, act=lambda a: a.request(pr), did="requested review")


def cancel_main(argv: list[str]) -> int:
    front = _setup(argv, USAGE_CANCEL)
    if isinstance(front, int):
        return front
    pr, adapters = front
    return _act(pr, adapters, act=lambda a: a.cancel(pr), did="withdrew review request")


def _act(
    pr: int,
    adapters: list[ReviewerAdapter],
    *,
    act: Callable[[ReviewerAdapter], bool],
    did: str,
) -> int:
    try:
        for adapter in adapters:
            if act(adapter):
                print(f"{did}: {adapter.name} on #{pr}")
            else:
                print(f"{adapter.name}: auto-triggers, no request mechanism — no-op")
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


# --- show ---------------------------------------------------------------------


def show_main(argv: list[str]) -> int:
    front = _setup(argv, USAGE_SHOW)
    if isinstance(front, int):
        return front
    pr, adapters = front
    try:
        ctx = gather(pr)
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    render_show(ctx, adapters)
    return 0


def render_show(ctx: PullContext, adapters: list[ReviewerAdapter]) -> None:
    """Print the selected reviewers' posted reviews, then ALL threads."""
    for adapter in adapters:
        print()
        print(f"=== {adapter.name} review(s) on #{ctx.number} ===")
        for review in ctx.reviews:
            if not adapter.matches(review.author):
                continue
            stale = "" if review.commit_id == ctx.head_sha else " [stale: earlier head]"
            print()
            print(f"state: {review.state}{stale}")
            print()
            print(review.body or "")
    print()
    print("=== review threads ===")
    for t in ctx.threads:
        marker = "resolved" if t.is_resolved else "open"
        print(f"[{marker}] {t.path or '?'}:{t.line or '?'} (thread {t.thread_id})")
        for c in t.comments:
            print(f"  [{c.comment_id}] {c.author}: {c.body}")
        print()
