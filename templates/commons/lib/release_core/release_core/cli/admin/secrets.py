"""``release-core admin secrets`` — release-secret provisioning (STUB — parallel agent).

Intended leaves (← old name), flat ``wrap_verb``::

  install   ← install-release-secrets   release_core.verbs.install_release_secrets.main
  token     ← install-release-token     release_core.verbs.install_release_token.main
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command


@click.group(
    name="secrets",
    short_help="Provision release secrets: install / token.",
)
def group() -> None:
    """Release-secret provisioning for onboarded repos. (Stub group — fill the
    leaves with wrap_verb per the module docstring.)"""


for _name, _help in (
    ("install", "Install the release secret set on a repo. (stub ← install-release-secrets)"),
    ("token", "Install the release token on a repo. (stub ← install-release-token)"),
):
    group.add_command(_stub_command(_name, _help))
