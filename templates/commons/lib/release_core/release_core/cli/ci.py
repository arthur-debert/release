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

from ._helpers import stub_group

# Stub group: bare `release-core ci` prints help + a stub note and exits 69
# (never a silent exit 0). A parallel agent fills it by add_command-ing the two
# wrap_script leaves; once it has real subcommands, drop stub_group for a normal
# @click.group.
group = stub_group(
    "ci",
    short_help="CI-glue fetch helpers (fetch-deps, fetch-artifact). (stub)",
    help=(
        "CI-side fetch helpers, backed by the standalone fetch-deps / "
        "fetch-artifact scripts. (Stub — fill per #460 / this module's docstring.)"
    ),
)
