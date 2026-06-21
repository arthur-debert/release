"""Tests for ``release-core review app …`` CLI leaves (``review.app_cli``).

Focused on the failure path: a registration error must go to STDERR (like every
other CLI entrypoint) and the leaf must exit nonzero — not pollute stdout.
"""

from __future__ import annotations

from release_core.review import app_cli, ghapp


def test_register_main_error_goes_to_stderr(monkeypatch, capsys):
    def _boom(agent, code):
        raise RuntimeError("bad code")

    monkeypatch.setattr(ghapp, "register_from_code", _boom)

    rc = app_cli.register_main(["codex", "--code", "abc"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Could not register app for 'codex'" in captured.err
    assert "bad code" in captured.err
    # The error must NOT leak onto stdout.
    assert "Could not register" not in captured.out
