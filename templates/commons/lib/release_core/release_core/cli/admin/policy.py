"""``release-core admin policy`` — GitHub policy ops (STUB — parallel agent).

Intended leaves (← old name), flat ``wrap_verb``::

  ruleset      ← apply-ruleset               release_core.verbs.apply_ruleset.main
  sweep        ← sweep-github-policy          release_core.verbs.sweep_github_policy.main
  dependabot   ← enable-dependabot-security   release_core.verbs.enable_dependabot_security.main
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command


@click.group(
    name="policy",
    short_help="GitHub policy ops: ruleset / sweep / dependabot.",
)
def group() -> None:
    """GitHub policy administration for fleet repos. (Stub group — fill the
    leaves with wrap_verb per the module docstring.)"""


for _name, _help in (
    ("ruleset", "Apply the canonical branch ruleset. (stub ← apply-ruleset)"),
    ("sweep", "Sweep / reconcile GitHub policy. (stub ← sweep-github-policy)"),
    ("dependabot", "Enable Dependabot security updates. (stub ← enable-dependabot-security)"),
):
    group.add_command(_stub_command(_name, _help))
