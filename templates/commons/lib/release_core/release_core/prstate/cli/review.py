"""`release-core pr review` — the reviewer-agnostic act surface.

request / cancel / show over the reviewer-adapter registry. Nothing
here branches on a reviewer's name: the default scope is the *required*
reviewer set, `--reviewer <name>` selects one adapter by registry name, and
every per-bot mechanic (how a request is placed, what "done" looks like)
lives behind the `ReviewerAdapter` interface in `prstate.reviewers`.

Commands (each is a `main(argv) -> int`, wrapped into the click tree by
`release_core.cli.pr`):

  request — request (or re-request) review(s) and VERIFY the attach;
            no-op backends say so and skip verification.
  cancel  — withdraw pending review request(s).
  show    — read-only: print posted review(s) + ALL review threads.
  reply   — post a threaded reply to a review comment (rationale / push-back).

Waiting is NOT here: the engine-owned `pr wait` (release#503, `cli/wait.py`)
replaced the per-reviewer `review wait` — the state engine knows what is
being waited on, so there is one wait for the whole loop.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable

from .. import ghapi
from ..fetch import attach_state, gather
from ..model import PullContext
from ..reviewers import REGISTRY, ReviewerAdapter, by_name, required_reviewers

# Attach-verification poll (release#614). GitHub's review service can accept
# every request channel's call yet silently drop the attach (service stall /
# quota): the call returns success but no `review_requested` edge is ever
# created, and the loop stalls invisibly waiting on a review that was never
# requested. The real postcondition of `request` is "the edge exists", so the
# verb polls for it. The edge is normally created synchronously — the first
# check usually verifies; the later checks absorb propagation lag without
# burning minutes on an outage that retrying won't fix.
ATTACH_VERIFY_CHECKS = 4
ATTACH_VERIFY_INTERVAL_SECONDS = 12  # 4 checks at t=0/12/24/36s — ~36s worst case
_VERIFY_WINDOW_SECONDS = (ATTACH_VERIFY_CHECKS - 1) * ATTACH_VERIFY_INTERVAL_SECONDS

_USAGE_COMMON = """\
With no <pr-number>, resolves the PR for the current branch. Default scope is
every REQUIRED reviewer in the adapter registry; --reviewer <name> selects one
adapter by name (required or best-effort).

Options:
  --reviewer <name>  act on one reviewer (an adapter registry name)
  -h --help          show this help
"""

USAGE_REQUEST = f"""\
release-core pr review request — request (or re-request) review(s) on a PR,
verifying the request actually attached.

Usage:
  release-core pr review request [<pr-number>] [--reviewer <name>]

Re-requesting after a fixup push is the same command — the backend call is
identical. Reviewers without a request mechanism (auto-triggering, best-effort
backends) report a no-op rather than failing; no-ops are not verified.

A request only counts when GitHub actually creates the review_requested edge:
the service can accept the call yet silently drop the attach (service stall /
quota — release#614). After placing, the verb polls briefly ({ATTACH_VERIFY_CHECKS} checks over
~{_VERIFY_WINDOW_SECONDS}s) until the reviewer appears in the PR's pending review requests — or has
already submitted a fresh review (a fast bot can consume the request before
the poll sees it). A dropped attach fails loud with exit 1.

{_USAGE_COMMON}
Exit codes:
  0   request(s) placed AND verified (or no-op for no-mechanism reviewers)
  1   gh failure, or request dropped by GitHub (no review_requested edge created)
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

USAGE_REPLY = """\
release-core pr review reply — post a threaded reply to a review comment.

Usage:
  release-core pr review reply <comment-id> <body> [--pr <pr-number>]

The push-back path: when you DISAGREE with a review comment (or want to
explain a fix), reply with rationale on the thread rather than silently
resolving it. <comment-id> is the numeric REST comment id printed by
`release-core pr review show` (the same handle `pr resolve-thread` takes);
<body> is the reply text. After replying, resolve the thread with
`release-core pr resolve-thread <pr> <comment-id>`.

With no --pr, resolves the PR for the current branch.

Options:
  --pr <pr-number>  the PR to reply on (default: current branch's PR)
  -h --help         show this help

Exit codes:
  0   reply posted
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
            print(
                f"error: too many arguments ({arg!r}) — the only positional is "
                "the PR number; select a reviewer with --reviewer <name>",
                file=sys.stderr,
            )
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
    try:
        # Baseline BEFORE placing: any review submitted after this snapshot
        # is fresh, so a fast bot consuming the request before the poll sees
        # the edge still verifies. Snapshotting between request and poll
        # instead would swallow exactly that consumed review.
        _, baseline_reviews = attach_state(pr)
        # flush=True: under a pipe, stdout is block-buffered while stderr is
        # not — without it the placement lines appear AFTER a drop report.
        placed: list[ReviewerAdapter] = []
        for adapter in adapters:
            if adapter.request(pr):
                print(f"requested review: {adapter.name} on #{pr}", flush=True)
                placed.append(adapter)
            else:
                print(f"{adapter.name}: auto-triggers, no request mechanism — no-op", flush=True)
        dropped = _verify_attached(pr, placed, baseline_ids={rid for rid, _ in baseline_reviews})
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for adapter in placed:
        if adapter not in dropped:
            print(f"verified: {adapter.name} request attached on #{pr}", flush=True)
    for adapter in dropped:
        print(
            f"{adapter.name}: request dropped by GitHub: no review_requested "
            "edge created (service stall / quota) — retry later",
            file=sys.stderr,
        )
    return 1 if dropped else 0


def _verify_attached(
    pr: int,
    placed: list[ReviewerAdapter],
    *,
    baseline_ids: set[int],
) -> list[ReviewerAdapter]:
    """The placed adapters whose request edge never appeared (empty = all good).

    An adapter is verified when its reviewer appears in the PR's pending
    review requests, OR when a fresh review by it (one not in the pre-request
    baseline) has been submitted — a fast bot can consume the request before
    a poll sees the edge. Only adapters that actually placed a request are
    checked; no-mechanism backends never enter.
    """
    pending = list(placed)
    for check in range(ATTACH_VERIFY_CHECKS):
        if not pending:
            break
        if check:
            time.sleep(ATTACH_VERIFY_INTERVAL_SECONDS)
        requested_logins, reviews = attach_state(pr)
        fresh_authors = [author for rid, author in reviews if rid not in baseline_ids]
        pending = [
            a
            for a in pending
            if not any(a.matches(login) for login in requested_logins)
            and not any(a.matches(author) for author in fresh_authors)
        ]
    return pending


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


# --- reply --------------------------------------------------------------------


def reply_main(argv: list[str]) -> int:
    """Post a threaded reply to a review comment (the rationale / push-back path)."""
    comment_arg: str | None = None
    body: str | None = None
    pr_arg: str | None = None
    it = iter(argv)
    for arg in it:
        if arg in ("-h", "--help"):
            print(USAGE_REPLY)
            return 0
        if arg == "--pr":
            pr_arg = next(it, None)
            if pr_arg is None:
                print("error: --pr needs a number", file=sys.stderr)
                return 64
        elif arg.startswith("--pr="):
            pr_arg = arg.split("=", 1)[1]
        elif arg.startswith("-") and arg != "-":
            print(f"error: unknown option {arg}", file=sys.stderr)
            return 64
        elif comment_arg is None:
            comment_arg = arg
        elif body is None:
            body = arg
        else:
            print(
                f"error: too many arguments ({arg!r}) — usage is "
                "`reply <comment-id> <body> [--pr <pr-number>]`",
                file=sys.stderr,
            )
            return 64
    if comment_arg is None or body is None:
        print("error: reply needs a <comment-id> and a <body>", file=sys.stderr)
        return 64
    if not comment_arg.isdigit():
        print(
            f"error: comment id must be numeric (got: {comment_arg}) — use the "
            "numeric comment-id from `pr review show`",
            file=sys.stderr,
        )
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
        ghapi.pr_review_reply(pr, int(comment_arg), body)
    except ghapi.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"replied to comment {comment_arg} on #{pr}")
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
    print(
        "(reply with `pr review reply <comment-id> <body>`; resolve with "
        "`pr resolve-thread <pr> <comment-id>` — both take the numeric comment id)"
    )
    for t in ctx.threads:
        marker = "resolved" if t.is_resolved else "open"
        print(f"[{marker}] {t.path or '?'}:{t.line or '?'} (graphql thread id {t.thread_id})")
        for c in t.comments:
            print(f"  comment-id {c.comment_id} | {c.author}: {c.body}")
        print()
