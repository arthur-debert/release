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

from ..review.app_cli import list_main, manifest_main, register_main
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


@click.group(
    name="app",
    short_help="Mint a review agent's thin GitHub App (bot identity).",
)
def app_group() -> None:
    """Create + manage the GitHub App that gives a review agent its identity.

    ``app manifest <agent> --name <app_name>`` writes the HTML form that starts
    GitHub's app-manifest flow; ``app register <agent> --code <code>`` exchanges
    the post-redirect code for the app and saves its id + private key locally
    (outside any repo); ``app list`` shows the configured agents. The app is
    identity only — posting AS the bot is a separate, later task.
    """


app_group.add_command(
    wrap_verb(
        manifest_main,
        name="manifest",
        short_help="Write the HTML form that starts the GitHub app-manifest flow.",
    )
)
app_group.add_command(
    wrap_verb(
        register_main,
        name="register",
        short_help="Exchange a manifest code for the app; save its id + pem locally.",
    )
)
app_group.add_command(
    wrap_verb(
        list_main,
        name="list",
        short_help="List the configured review-agent GitHub Apps.",
    )
)

group.add_command(app_group)
