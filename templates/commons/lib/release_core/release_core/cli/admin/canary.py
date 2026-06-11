"""``release-core admin canary`` — pre-ship canary rounds (#587, epic #583).

Flat ``wrap_verb`` leaves over the canary verbs::

  run   release_core.verbs.canary_run.main

The module exports a ``group`` (a ``click.Group``); the ``admin`` assembler
imports and attaches it. Each leaf is a faithful passthrough — argv and
``--help`` go straight to the verb. ``canary init`` (idempotent create/reset
of a canary repo) is slice 2 and lands here when it ships.
"""

from __future__ import annotations

import click

from ...verbs import canary_run
from .._helpers import wrap_verb


@click.group(
    name="canary",
    short_help="Pre-ship canary rounds against the registered canary consumers.",
)
def group() -> None:
    """Pre-ship consumer-life testing.

    Exercises the registered canary repos (the ``canaries:`` block of
    ``managed-repos.yaml``) against an UNRELEASED candidate ref — boot from
    source, CI (check + e2e) and a real prerelease cut — before the fleet
    moves. Run from inside ``arthur-debert/release``.
    """


group.add_command(
    wrap_verb(
        canary_run.main,
        name="run",
        short_help="One canary round against --ref: publish, seed, dispatch, classify.",
    )
)
