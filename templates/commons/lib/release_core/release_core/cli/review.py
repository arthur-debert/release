"""``release-core review`` — run a local code review of a PR through an agent.

Two leaves: ``review run <agent> --pr N`` forwards to the review facade
(``release_core.review.facade.run_review``), which resolves the PR's diff,
builds the shared prompt, and drives the chosen backend (codex / agy), emitting
a structured JSON review; ``review post --pr N`` forwards to
``release_core.review.post_cli.main``, which posts a review JSON to the PR as a
single inline grouped GitHub review. ``run`` only generates and ``post`` only
posts — Phase 3 composes them. Like every other group module the only contract
is: define a ``click.Group`` and export it as ``group``; ``cli_entry`` attaches
it to the root.
"""

from __future__ import annotations

import click

from ..review.facade import run_review
from ..review.post_cli import main as post_main
from ._helpers import wrap_verb


@click.group(
    name="review",
    short_help="Run a local agent code review of a PR.",
)
def group() -> None:
    """Review a PR locally with a code-review agent backend.

    ``review run <agent> --pr N`` resolves the PR's diff and runs the chosen
    backend (``codex`` / ``agy``) over it, emitting a structured JSON review;
    ``review post --pr N`` posts a review JSON to the PR as one inline grouped
    GitHub review. Use ``--dry-run`` on either to preview without side effects.
    """


group.add_command(
    wrap_verb(
        run_review,
        name="run",
        short_help="Review a PR with an agent backend (codex/agy); --dry-run to preview.",
    )
)

group.add_command(
    wrap_verb(
        post_main,
        name="post",
        short_help="Post a review JSON to a PR as one inline grouped review; --dry-run to preview.",
    )
)
