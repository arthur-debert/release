"""``release-core admin`` — fleet / meta-release commands (assembler).

``admin`` is large, so it is its OWN subpackage split one module per nested
group, exactly mirroring the per-top-level-group split at the ``cli/`` level —
so it too can be handed to several parallel agents without collisions:

  admin repos    ← repos.py         (list / prs / scripts / audit / verify)
  admin release  ← release_cmds.py  (advance-major / betas / lex)
  admin policy   ← policy.py        (ruleset / sweep / dependabot)
  admin secrets  ← secrets.py       (install / token)
  admin inbox    ← inbox.py         (bare / notify-source)
  admin smoke-test  ← audit-smoke-test  (a flat leaf, defined here)

Each module exports a ``group`` (a ``click.Group``); this assembler imports
and attaches them. Filling a nested group touches ONLY that module.

ALL of admin is a STUB in Phase 1 — the groups register so ``release-core admin
--help`` shows the full shape, but the leaves are filled by parallel agents.
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command  # shared stub leaf for `smoke-test`
from . import inbox, policy, release_cmds, repos, secrets


@click.group(
    name="admin",
    short_help="Fleet / meta-release ops (run from inside arthur-debert/release).",
)
def group() -> None:
    """Meta-release / fleet administration.

    Everything that operates ON the fleet rather than on a single repo:
    onboarding (policy/secrets), the release-side mechanics (advance-major,
    betas), portfolio audits, and the consumer-feedback inbox. Run from inside
    ``arthur-debert/release``.
    """


group.add_command(repos.group)
group.add_command(release_cmds.group)
group.add_command(policy.group)
group.add_command(secrets.group)
group.add_command(inbox.group)

# A flat leaf directly under admin (no nested group). Stub for now; a parallel
# agent swaps this for:  wrap_verb(audit_smoke_test.main, name="smoke-test", …)
group.add_command(
    _stub_command(
        "smoke-test",
        "Fleet smoke-test sweep. (stub ← audit-smoke-test)",
    )
)
