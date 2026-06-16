"""``release-core pr`` — the PR-loop helpers (EXEMPLAR group).

This group is the reference implementation for the parallel agents. It shows,
in one place, every pattern they need:

- a nested subgroup (``pr review`` with ``request``/``cancel``/``show`` —
  the reviewer-agnostic act surface over the adapter registry),
- :func:`~release_core.cli._helpers.wrap_script` for the standalone bash/python
  tools (``gh-pr-resolve-thread``),
- :func:`~release_core.cli._helpers.wrap_verb` for a native verb
  (``pr status`` → the folded PR-state engine, ``prstate.cli.task_status``;
  ``pr wait`` → the engine-owned wait, ``prstate.cli.wait`` — it replaced
  ``pr review wait`` and ``pr checks-wait`` outright, release#503).

A group module's only contract: define a ``click.Group`` and export it as
``group``. ``cli_entry`` imports ``group`` and attaches it to the root.

No command here names a reviewer: ``pr review`` defaults to every REQUIRED
reviewer in ``prstate.reviewers.REGISTRY`` and ``--reviewer <name>`` selects
one adapter — the registry is the only place that knows a bot's name.
"""

from __future__ import annotations

import click

from ..prstate.cli import ready as ready_cli
from ..prstate.cli import review as review_cli
from ..prstate.cli import task_status
from ..prstate.cli import wait as wait_cli
from ._helpers import wrap_script, wrap_verb


@click.group(
    name="review",
    short_help="Drive the PR's reviews (request/cancel/show).",
)
def review() -> None:
    """Request, cancel, or read the reviews on a PR.

    Reviewer-agnostic: every command dispatches through the reviewer-adapter
    registry (``prstate.reviewers``). Default scope is all required
    reviewers; ``--reviewer <name>`` selects one by adapter name.
    """


review.add_command(
    wrap_verb(
        review_cli.request_main,
        name="request",
        short_help="Request (or re-request) review(s) on a PR.",
    )
)
review.add_command(
    wrap_verb(
        review_cli.cancel_main,
        name="cancel",
        short_help="Withdraw pending review request(s) on a PR.",
    )
)
review.add_command(
    wrap_verb(
        review_cli.show_main,
        name="show",
        short_help="Print posted review(s) + all review threads. Read-only.",
    )
)
review.add_command(
    wrap_verb(
        review_cli.reply_main,
        name="reply",
        short_help="Reply to a review comment with rationale (the push-back path).",
    )
)


@click.group(
    name="pr",
    short_help="PR-loop helpers: reviews, wait, threads, status.",
)
def group() -> None:
    """Per-project PR-loop helpers.

    Everything an agent or human needs to drive a single PR through the
    review pipeline: request the required reviews, block until the PR needs
    the agent again (``wait`` — the engine-owned wait over reviews, checks
    and mergeability alike), resolve review threads, and read the PR's
    lifecycle state (``status``).
    """


group.add_command(review)
group.add_command(
    wrap_verb(
        wait_cli.main,
        name="wait",
        short_help="Block in-turn until the PR needs the agent again.",
    )
)
group.add_command(
    wrap_script(
        "gh-pr-resolve-thread",
        name="resolve-thread",
        short_help="Resolve a PR review thread (or all addressed threads).",
    )
)
group.add_command(
    wrap_verb(
        task_status.main,
        name="status",
        short_help="Where does this PR stand? (lifecycle state + next action)",
    )
)
group.add_command(
    wrap_verb(
        ready_cli.main,
        name="ready",
        short_help="Flip draft->ready when the engine says READY (--undo reverts).",
    )
)
