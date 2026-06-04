"""Per-project top-level commands of ``release-core`` (the flat verbs a single
repo's agent/human uses), assembled onto the root by :func:`attach`.

Two are IMPLEMENTED as EXEMPLARS of the flat ``wrap_verb`` pattern:

  cut       ← release_cut.main      (cut a release for this repo)
  status    ← done_check.main       (this repo's pilot-running gate)

``init`` and ``selfcheck`` are folded in here too (verb-wraps), replacing the
hand-rolled dispatcher entries that previously lived in ``cli_entry``.

The REMAINING per-project surface is registered as STUBS for the parallel
agents (#460). Each stub is either a flat command or a small group; filling one
in touches only this file's relevant block. The intended mapping (← old name):

  cut <version|bump>            ← release-cut            [DONE]
  status                        ← done-check             [DONE]
  changelog [add|cut|render]    ← changelog*             [stub group]
  semver  validate|get          ← semver                 [stub group]
  sync [drift-check]            ← release-sync / release-drift-check  [stub group]
  detect-kind                   ← detect-kind            [stub command]
  audit                         ← audit-repo             [stub command]
  issue file <component> <msg>  ← gh-release-issue       [stub group]
"""

from __future__ import annotations

import click

from ..verbs import done_check, init, release_cut, selfcheck
from ._helpers import STUB_EXIT, stub_group, wrap_verb


def attach(root: click.Group) -> None:
    """Attach every per-project top-level command/group to ``root``.

    Called by ``cli_entry`` exactly once. Grouped by IMPLEMENTED vs STUB so a
    parallel agent can see at a glance what is left to fill.
    """

    # --- folded-in dispatcher commands (were in the hand-rolled cli_entry) ---
    root.add_command(
        wrap_verb(
            init.main,
            name="init",
            short_help="Materialize this repo's committed release config.",
        )
    )
    root.add_command(
        wrap_verb(
            selfcheck.main,
            name="selfcheck",
            short_help="Verify release-core's runtime deps are importable.",
        )
    )

    # --- EXEMPLARS: flat wrap_verb commands -------------------------------
    root.add_command(
        wrap_verb(
            release_cut.main,
            name="cut",
            short_help="Cut a release for this repo (any Kind).",
        )
    )
    root.add_command(
        wrap_verb(
            done_check.main,
            name="status",
            short_help="This repo's pilot-running gate (done-check).",
        )
    )

    # --- STUBS for the parallel agents (#460) -----------------------------
    # Flat commands — fill with a one-line wrap_verb, e.g.:
    #   root.add_command(wrap_verb(detect_kind.main, name="detect-kind",
    #       short_help="Detect this repo's release Kind."))
    root.add_command(
        _stub_command(
            "detect-kind",
            "Detect this repo's release Kind. (stub ← detect-kind)",
        )
    )
    root.add_command(
        _stub_command(
            "audit",
            "Audit THIS repo's release posture. (stub ← audit-repo)",
        )
    )

    # Small per-project groups — fill each group's leaves with wrap_verb.
    root.add_command(_changelog_group())
    root.add_command(_semver_group())
    root.add_command(_sync_group())
    root.add_command(_issue_group())


# --------------------------------------------------------------------------
# Stub scaffolding. These keep `release-core --help` showing the full shape
# while a parallel agent fills the real wrap_verb/wrap_script in. A stub leaf
# or a bare-invoked stub group exits STUB_EXIT (69, EX_UNAVAILABLE) with a clear
# "not yet wired" message, so it can never be mistaken for working. The group
# factory is the shared `stub_group` in _helpers; the leaf factory is below.
# --------------------------------------------------------------------------


class _StubCommand(click.Command):
    """A registered-but-unimplemented LEAF: shows in ``--help`` like any other
    command, but exits ``STUB_EXIT`` with a clear message if actually invoked,
    so it can never be mistaken for working.

    It accepts *any* args/flags (``ignore_unknown_options`` + a catch-all
    ``args`` param) so that even ``release-core detect-kind --json foo`` lands on
    the stub-exit path rather than failing with click's usage error (exit 2).
    ``--help`` is still handled (eager), so the stub's help stays discoverable.
    """

    def invoke(self, ctx: click.Context):
        click.echo(
            f"release-core: `{self.name}` is a registered stub (not yet "
            f"wired to its verb — see #460).",
            err=True,
        )
        raise SystemExit(STUB_EXIT)


def _stub_command(name: str, short_help: str) -> click.Command:
    cmd = _StubCommand(
        name=name,
        short_help=short_help,
        callback=lambda args=None: None,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        params=[click.Argument(["args"], nargs=-1, type=click.UNPROCESSED)],
    )
    return cmd


def _changelog_group() -> click.Group:
    return stub_group(
        "changelog",
        short_help="Manage this repo's changelog (add/cut/render). (stub)",
        help=(
            "Changelog management. (Stub group ← changelog* — fill add/cut/render "
            "with wrap_verb over release_core.verbs.changelog's *_main functions.)"
        ),
    )


def _semver_group() -> click.Group:
    return stub_group(
        "semver",
        short_help="Validate / extract semver parts. (stub)",
        help=(
            "Semver helpers. (Stub group ← semver — fill validate/get with "
            "wrap_verb over release_core.verbs.semver.main.)"
        ),
    )


def _sync_group() -> click.Group:
    return stub_group(
        "sync",
        short_help="Materialize / drift-check the synced .release/ tree. (stub)",
        help=(
            "Sync helpers. (Stub group ← release-sync / release-drift-check — "
            "fill the bare sync + drift-check leaf with wrap_verb.)"
        ),
    )


def _issue_group() -> click.Group:
    return stub_group(
        "issue",
        short_help="Escalate infra friction to arthur-debert/release. (stub)",
        help=(
            "Issue helpers. (Stub group ← gh-release-issue — fill `issue file` "
            "with wrap_verb over release_core.verbs.gh_release_issue.main.)"
        ),
    )
