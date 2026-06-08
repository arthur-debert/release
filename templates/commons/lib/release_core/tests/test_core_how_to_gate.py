"""``release-core how-to`` + ``release-core gate`` (release#501, WS1).

how-to is the Kind-aware playbook that replaces the synced ORIENTATION.md /
per-Kind docs (single source). gate is the one quality entry that wraps the
lefthook gate over the whole tree (no false-green on unstaged files).
"""

from __future__ import annotations

import click
from release_core import cli_entry
from release_core.verbs import gate, how_to


def _root() -> click.Group:
    return cli_entry.root


# --- registration ---------------------------------------------------------


def test_how_to_and_gate_registered():
    names = set(_root().commands)
    assert {"how-to", "gate"} <= names
    for n in ("how-to", "gate"):
        assert (_root().commands[n].short_help or "").strip()


# --- how-to is Kind-aware, single-source ----------------------------------


def test_how_to_renders_kind_specific_verbs():
    rust = how_to._render("rust-cli")
    assert "cargo test" in rust and "cargo build" in rust
    npm = how_to._render("vscode-ext")
    assert "npm test" in npm and "npm install" in npm  # deps line for node stacks
    # rust has no npm deps line
    assert "npm install" not in rust


def test_how_to_always_states_the_one_gate_and_draft_first_cycle():
    for kind in ("rust-cli", "go-cli", "vscode-ext", "unknown"):
        body = how_to._render(kind)
        assert "release-core gate" in body
        assert "gh pr create --draft" in body
        assert "changelog add" in body


def test_how_to_main_explicit_kind(capsys):
    assert how_to.main(["go-cli"]) == 0
    out = capsys.readouterr().out
    assert "Kind: go-cli" in out and "go test ./..." in out


def test_how_to_help(capsys):
    assert how_to.main(["--help"]) == 0
    assert "how-to" in capsys.readouterr().out


# --- gate is a hard gate over the whole tree ------------------------------


def test_gate_missing_lefthook_is_a_hard_failure(monkeypatch, capsys):
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda root: None)
    monkeypatch.setattr(gate, "_repo_root", lambda: ".")
    assert gate.main([]) == 1
    assert "lefthook not found" in capsys.readouterr().err


def test_gate_runs_all_files_over_the_repo(monkeypatch):
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda root: "lefthook")
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    cmd = captured["cmd"]
    assert cmd[:5] == ["lefthook", "run", "pre-commit", "--all-files", "--no-tty"]
    assert captured["cwd"] == "/repo"


def test_gate_help(capsys):
    assert gate.main(["--help"]) == 0
    assert "gate" in capsys.readouterr().out
