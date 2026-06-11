"""``release-core admin contract`` — the consumer-contract manifest (#584).

Flat ``wrap_verb`` leaves over the contract verb (one verb module, three
entry wrappers)::

  dump    release_core.verbs.contract.dump_main   regenerate + write the manifest
  check   release_core.verbs.contract.check_main  diff committed vs generated (gate)
  lint    release_core.verbs.contract.lint_main   the assumption lint (gate)

The module exports a ``group`` (a ``click.Group``); the ``admin`` assembler
imports and attaches it. Each leaf is a faithful passthrough — argv and
``--help`` go straight to the verb.
"""

from __future__ import annotations

import click

from ...verbs import contract
from .._helpers import wrap_verb


@click.group(
    name="contract",
    short_help="Consumer-contract manifest: dump / check / lint.",
)
def group() -> None:
    """The consumer-tree contract (epic #583 WS-A).

    ``docs/references/consumer-contract.yaml`` is a generated, PRESCRIPTIVE
    view of release_core/sync.py: what a consumer tree MUST look like
    (tracked real files, untracked ephemeral mirrors, gate-internal set,
    tombstones, managed-path patterns). ``dump`` regenerates it, ``check``
    fails on drift, ``lint`` sweeps every CI surface for jobs that reference
    a managed path without materializing first. Run from inside
    ``arthur-debert/release``.
    """


group.add_command(
    wrap_verb(
        contract.dump_main,
        name="dump",
        short_help="Regenerate docs/references/consumer-contract.yaml.",
    )
)
group.add_command(
    wrap_verb(
        contract.check_main,
        name="check",
        short_help="Fail if the committed manifest drifted from sync.py.",
    )
)
group.add_command(
    wrap_verb(
        contract.lint_main,
        name="lint",
        short_help="Assumption lint: managed-path use without materialize.",
    )
)
