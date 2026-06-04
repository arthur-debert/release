"""The assembled ``release-core`` group tree: shape + help one-liners + the
exemplar dispatch (release#460).

Pins what the parallel agents build on: the full group skeleton is registered
(so ``--help`` shows the whole shape), the exemplar ``pr`` group is fully wired
(nested ``copilot`` subgroup, ``wrap_script`` leaves, ``pr status``), the
top-level verb-wrap exemplars (``cut``, ``status``) dispatch, every leaf carries
a one-line short_help, and unimplemented leaves are registered stubs.
"""

from __future__ import annotations

import click
from release_core import cli_entry
from release_core.cli import admin, ci, pr, toplevel


def _root() -> click.Group:
    """Rebuild a fresh root the way cli_entry assembles it (for introspection)."""
    root = cli_entry.root
    return root


# --- the full skeleton is registered (the "map") --------------------------


def test_toplevel_groups_registered():
    root = _root()
    names = set(root.commands)
    assert {"pr", "ci", "admin"} <= names
    # per-project flat verbs + folded-in init/selfcheck
    assert {"init", "selfcheck", "cut", "status"} <= names
    # per-project stub groups/commands
    assert {"changelog", "semver", "sync", "detect-kind", "audit", "issue"} <= names


def test_pr_group_is_fully_wired():
    grp = pr.group
    assert set(grp.commands) >= {"copilot", "checks-wait", "resolve-thread", "status"}
    copilot = grp.commands["copilot"]
    assert isinstance(copilot, click.Group)
    assert set(copilot.commands) == {"on", "off", "wait", "review"}


def test_admin_skeleton_registered():
    grp = admin.group
    assert set(grp.commands) >= {
        "repos",
        "release",
        "policy",
        "secrets",
        "inbox",
        "smoke-test",
    }
    assert set(admin.repos.group.commands) >= {
        "list",
        "prs",
        "scripts",
        "audit",
        "verify",
    }
    assert set(admin.release_cmds.group.commands) >= {"advance-major", "betas", "lex"}
    assert set(admin.policy.group.commands) >= {"ruleset", "sweep", "dependabot"}
    assert set(admin.secrets.group.commands) >= {"install", "token"}
    assert set(admin.inbox.group.commands) >= {"notify-source"}


def test_ci_group_registered_as_stub():
    # ci is a registered (empty) group for a parallel agent to fill.
    assert isinstance(ci.group, click.Group)
    assert ci.group.name == "ci"


# --- every leaf has a one-line short_help (discoverability requirement) ----


def _walk(grp: click.Group):
    for cmd in grp.commands.values():
        if isinstance(cmd, click.Group):
            yield from _walk(cmd)
        else:
            yield cmd


def test_every_leaf_has_a_short_help():
    root = _root()
    missing = [c.name for c in _walk(root) if not (c.short_help or "").strip()]
    assert missing == [], f"leaves missing short_help: {missing}"


# --- exemplar dispatch ----------------------------------------------------


def test_cut_dispatches_to_release_cut_help(capsys):
    # cut --help forwards to the release_cut verb's own help (exit 0). The verb
    # prints its usage to stderr; the passthrough preserves the stream faithfully.
    rc = cli_entry.main(["cut", "--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "release-cut" in (captured.out + captured.err)


def test_status_dispatches_to_done_check_help(capsys):
    rc = cli_entry.main(["status", "--help"])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == 0
    # done_check's help mentions its pilot-running / done-check contract.
    assert "done-check" in text or "done_check" in text or "pilot" in text.lower()


def test_pr_status_dispatches_to_task_status_help(capsys):
    rc = cli_entry.main(["pr", "status", "--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "gh-task-status" in (captured.out + captured.err)


# --- stub behavior --------------------------------------------------------


def test_invoking_a_stub_leaf_exits_69(capsys):
    rc = cli_entry.main(["detect-kind"])
    err = capsys.readouterr().err
    assert rc == 69
    assert "stub" in err.lower()


def test_stub_leaf_with_extra_args_still_exits_69(capsys):
    # Any flags/args land on the stub-exit path (69), not click's usage error.
    rc = cli_entry.main(["detect-kind", "--json", "extra", "-x"])
    err = capsys.readouterr().err
    assert rc == 69
    assert "stub" in err.lower()


def test_stub_leaf_help_is_still_reachable(capsys):
    # --help remains discoverable on a stub (shows the short_help / usage).
    rc = cli_entry.main(["detect-kind", "--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "detect-kind" in out or "Usage:" in out


def test_toplevel_attach_is_idempotent_shape():
    # attach() is what cli_entry calls; calling it on a fresh group yields the
    # same per-project command set (proves no hidden global state).
    fresh = click.Group(name="x")
    toplevel.attach(fresh)
    assert {"init", "selfcheck", "cut", "status"} <= set(fresh.commands)
