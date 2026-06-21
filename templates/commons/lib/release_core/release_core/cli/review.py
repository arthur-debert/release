"""``release-core review`` — run a local code review of a PR through an agent.

One leaf so far: ``review run <agent> --pr N`` forwards verbatim to the review
facade (``release_core.review.facade.run_review``), which resolves the PR's
diff, builds the shared prompt, and drives the chosen backend (codex / agy).
Like every other group module the only contract is: define a ``click.Group``
and export it as ``group``; ``cli_entry`` attaches it to the root.
"""

from __future__ import annotations

import click

from ..review.facade import run_review
from ._helpers import wrap_verb


@click.group(
    name="review",
    short_help="Run a local agent code review of a PR.",
)
def group() -> None:
    """Review a PR locally with a code-review agent backend.

    ``review run <agent> --pr N`` resolves the PR's diff and runs the chosen
    backend (``codex`` / ``agy``) over it, emitting a structured JSON review.
    Use ``--dry-run`` to print exactly what would be sent to the agent.
    """


group.add_command(
    wrap_verb(
        run_review,
        name="run",
        short_help="Review a PR with an agent backend (codex/agy); --dry-run to preview.",
    )
)
