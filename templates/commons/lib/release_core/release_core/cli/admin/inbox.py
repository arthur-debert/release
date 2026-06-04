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

from .._helpers import stub_group
from ..toplevel import _stub_command

# Stub group for now: bare `release-core admin inbox` prints help + a stub note
# and exits 69 (never a silent exit 0). When the bare form is implemented it
# should forward to release_core.verbs.release_inbox.main — at that point drop
# stub_group for a normal @click.group(invoke_without_command=True) whose
# callback mirrors the wrap_verb passthrough settings so `admin inbox --json`
# reaches release-inbox untouched.
group = stub_group(
    "inbox",
    short_help="Consumer-feedback inbox: triage view + notify-source. (stub)",
    help=(
        "The #348 consumer-feedback inbox. (Stub group — the bare form will map "
        "to release-inbox; see this module's docstring.)"
    ),
)

group.add_command(
    _stub_command(
        "notify-source",
        "Notify source PRs that an upstream fix shipped. (stub ← release-notify-source)",
    )
)
