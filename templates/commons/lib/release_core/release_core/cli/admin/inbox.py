"""``release-core admin inbox`` — consumer-feedback inbox (STUB — parallel agent).

Shape (← old name): a group with BOTH a bare invocation AND a subcommand::

  admin inbox                 ← release-inbox          (the bare triage view)
  admin inbox notify-source   ← release-notify-source  (close-the-loop notice)

The bare form means the group needs ``invoke_without_command=True`` and a
callback that, when no subcommand is given, forwards to
``release_core.verbs.release_inbox.main`` (collecting the remaining argv via a
passthrough ``args`` argument). The agent filling this in should mirror the
``wrap_verb`` passthrough settings on the GROUP callback so ``admin inbox
--json`` reaches release-inbox untouched. ``notify-source`` is a plain
``wrap_verb`` leaf.
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command


@click.group(
    name="inbox",
    short_help="Consumer-feedback inbox: triage view + notify-source.",
)
def group() -> None:
    """The #348 consumer-feedback inbox. (Stub group — the bare form maps to
    release-inbox via invoke_without_command; see the module docstring.)"""


group.add_command(
    _stub_command(
        "notify-source",
        "Notify source PRs that an upstream fix shipped. (stub ← release-notify-source)",
    )
)
