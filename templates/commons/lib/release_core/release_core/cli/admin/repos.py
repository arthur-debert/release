"""``release-core admin repos`` — fleet-repo views (STUB — for a parallel agent).

Intended leaves (← old name), all flat ``wrap_verb`` over an existing verb::

  list      ← managed-repos           release_core.verbs.managed_repos.main
  prs       ← list-repo-pr            release_core.verbs.list_repo_pr.main
  scripts   ← list-repo-scripts       release_core.verbs.list_repo_scripts.main
  audit     ← audit-portfolio         release_core.verbs.audit_portfolio.main
  verify    ← release-verify-fleet    release_core.verbs.release_verify_fleet.main

To fill in (this module ONLY)::

    from .._helpers import wrap_verb
    from ...verbs import managed_repos
    group.add_command(wrap_verb(managed_repos.main, name="list",
        short_help="List the managed fleet repos."))
"""

from __future__ import annotations

import click

from ..toplevel import _stub_command


@click.group(
    name="repos",
    short_help="Fleet-repo views: list / prs / scripts / audit / verify.",
)
def group() -> None:
    """Views over the managed fleet repos. (Stub group — fill the leaves with
    wrap_verb per the module docstring.)"""


for _name, _help in (
    ("list", "List the managed fleet repos. (stub ← managed-repos)"),
    ("prs", "List open PRs across the fleet. (stub ← list-repo-pr)"),
    ("scripts", "List per-repo scripts across the fleet. (stub ← list-repo-scripts)"),
    ("audit", "Audit the whole portfolio. (stub ← audit-portfolio)"),
    ("verify", "Hermetic pre-flight fleet sweep. (stub ← release-verify-fleet)"),
):
    group.add_command(_stub_command(_name, _help))
