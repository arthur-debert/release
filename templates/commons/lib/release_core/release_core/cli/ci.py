"""``release-core ci`` — CI-glue fetch helpers (STUB — for a parallel agent).

Intended commands (see #460), both backed by standalone scripts → use
:func:`~release_core.cli._helpers.wrap_script`:

  ci fetch-deps        ← bin/fetch-deps
  ci fetch-artifact    ← bin/fetch-artifact

To fill this in, add (this is the ENTIRE change — no other file is touched)::

    from ._helpers import wrap_script

    group.add_command(wrap_script("fetch-deps", name="fetch-deps",
        short_help="Fetch a release's built dependencies."))
    group.add_command(wrap_script("fetch-artifact", name="fetch-artifact",
        short_help="Fetch a named build artifact from a release run."))
"""

from __future__ import annotations

import click


@click.group(
    name="ci",
    short_help="CI-glue fetch helpers (fetch-deps, fetch-artifact).",
)
def group() -> None:
    """CI-side fetch helpers.

    Backed by the standalone ``fetch-deps`` / ``fetch-artifact`` scripts.
    (Stub — to be filled by a parallel agent per #460.)
    """
