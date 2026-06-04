"""``release-core admin release`` — release-side mechanics (STUB — parallel agent).

Module is named ``release_cmds`` (not ``release``) to avoid shadowing the
package name. Intended leaves (← old name), flat ``wrap_verb``::

  advance-major  ← release-advance-major   release_core.verbs.release_advance_major.main
  betas          ← release-beta-list       release_core.verbs.release_beta_list.main
  lex            ← release-lex             release_core.verbs.release_lex.main
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command


@click.group(
    name="release",
    short_help="Release-side mechanics: advance-major / betas / lex.",
)
def group() -> None:
    """Release-side mechanics for the fleet. (Stub group — fill the leaves with
    wrap_verb per the module docstring.)"""


for _name, _help in (
    ("advance-major", "Fast-forward the floating major branch. (stub ← release-advance-major)"),
    ("betas", "List beta/RC releases. (stub ← release-beta-list)"),
    ("lex", "Drive the release-lex pipeline. (stub ← release-lex)"),
):
    group.add_command(_stub_command(_name, _help))
