"""``release-core how-to`` + ``release-core gate`` (release#501, WS1).

how-to is the Kind-aware playbook that replaces the synced ORIENTATION.md /
per-Kind docs (single source). gate is the one quality entry that wraps the
lefthook gate over the whole tree (no false-green on unstaged files).
"""

from __future__ import annotations

import os

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


def test_how_to_abstract_kind_describes_where_commands_live():
    # `how-to <kind>` is the abstract doc path (no repo to read): it names the
    # uniform verbs and hints where the real command lives — never a per-repo
    # hardcode. rust hints cargo; node stacks get a deps line.
    rust = how_to._render("rust-cli")
    assert "cargo test" in rust and "cargo build" in rust
    assert "release-core test-unit" in rust
    node = how_to._render("vscode-ext")
    assert "npm install" in node  # deps line for node stacks
    assert "release-core test-unit" in node
    # rust has no npm deps line
    assert "npm install" not in rust


def test_how_to_repo_path_surfaces_real_commands_not_npm_test(monkeypatch, tmp_path):
    # The no-arg path reads THIS repo: lexed's shape (test:unit + test:e2e, NO
    # bare `test`) must surface the real scripts, never a guessed `npm test`.
    import json

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:unit": "vitest", "test:e2e": "playwright test"}})
    )
    (tmp_path / "package-lock.json").write_text("")
    body = how_to._render_repo(str(tmp_path), "electron-app")
    assert "release-core test-unit" in body
    assert "npm run test:unit" in body
    assert "npm run test:e2e" in body
    assert "npm test" not in body


def test_how_to_repo_path_surfaces_docs_facet_on_any_kind(tmp_path):
    # mkdocs is orthogonal: a rust repo that also carries docs surfaces it.
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    (tmp_path / "mkdocs.yml").write_text("site_name: x\n")
    body = how_to._render_repo(str(tmp_path), "rust-cli")
    assert "mkdocs build --strict" in body
    assert "cargo test" in body


def test_how_to_always_states_the_one_gate_and_draft_first_cycle():
    for kind in ("rust-cli", "go-cli", "vscode-ext", "unknown"):
        body = how_to._render(kind)
        assert "release-core gate" in body
        assert "gh pr create --draft" in body
        assert "changelog add" in body


def test_how_to_distinguishes_gate_from_tests():
    # The gate runs lint/format, NOT tests — how-to must say so, or a fresh agent
    # treats a green gate as CI-green and skips tests (caught in the lex dogfood).
    body = how_to._render("rust-cli")
    assert "does NOT run tests" in body
    assert "separate check" in body


def test_how_to_main_explicit_kind(capsys):
    assert how_to.main(["go-cli"]) == 0
    out = capsys.readouterr().out
    assert "Kind: go-cli" in out and "go test ./..." in out


def test_how_to_help(capsys):
    assert how_to.main(["--help"]) == 0
    assert "how-to" in capsys.readouterr().out


def test_how_to_detects_from_repo_root(monkeypatch, capsys):
    # no-arg path resolves the repo root (works from a subdir) and detects there.
    monkeypatch.setattr(how_to, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(how_to.manifest, "detect_kind", lambda d: "go-cli")
    assert how_to.main([]) == 0
    assert "Kind: go-cli" in capsys.readouterr().out


def test_how_to_undetectable_kind_renders_generic_not_error(monkeypatch, capsys):
    # Design decision: an undetectable Kind renders the GENERIC playbook (exit 0),
    # not a hard error — orientation guidance (dev cycle + gate) is Kind-agnostic.
    monkeypatch.setattr(how_to, "_repo_root", lambda: "/repo")

    def _boom(d):
        raise how_to.manifest.KindError("nope")

    monkeypatch.setattr(how_to.manifest, "detect_kind", _boom)
    assert how_to.main([]) == 0
    out = capsys.readouterr().out
    assert "release-core gate" in out and "gh pr create --draft" in out


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


def test_gate_hook_mode_runs_staged_not_all_files(monkeypatch):
    """--hook (the git pre-commit entry) runs lefthook over the STAGED set — NO
    --all-files — so stage_fixed auto-fix+restage stays correct at commit time."""
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda root: "lefthook")
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main(["--hook"]) == 0
    cmd = captured["cmd"]
    assert cmd == ["lefthook", "run", "pre-commit", "--no-tty"]
    assert "--all-files" not in cmd


def test_gate_points_lefthook_at_managed_config(monkeypatch, tmp_path):
    """When .release/lefthook.yml exists, LEFTHOOK_CONFIG points lefthook at it —
    the root discovery symlink is gone (WS3, release#524)."""
    (tmp_path / ".release").mkdir()
    (tmp_path / ".release" / "lefthook.yml").write_text("pre-commit:\n")
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda root: "lefthook")
    monkeypatch.delenv("LEFTHOOK_CONFIG", raising=False)
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    assert captured["env"]["LEFTHOOK_CONFIG"] == str(tmp_path / ".release" / "lefthook.yml")


def _git_init(path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)


def test_install_hook_writes_pre_commit(tmp_path, capsys):
    """--install-hook wires .git/hooks/pre-commit to run the gate through the
    binary; the hook is executable and idempotent."""
    _git_init(tmp_path)
    monkeypatch_repo = tmp_path

    import release_core.verbs.gate as g

    rc = g._install_hook(str(monkeypatch_repo))
    assert rc == 0
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.is_file()
    assert os.access(str(hook), os.X_OK)
    body = hook.read_text()
    assert "release-core gate --hook" in body
    assert "Managed by release" in body
    # Idempotent: a second install is a clean no-op-equivalent (same content).
    assert g._install_hook(str(tmp_path)) == 0
    assert hook.read_text() == body


def test_install_hook_unsets_custom_hooks_path(tmp_path):
    """A husky-style core.hooksPath redirect is cleared so the shim takes effect."""
    import subprocess

    _git_init(tmp_path)
    subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=str(tmp_path), check=True)
    import release_core.verbs.gate as g

    assert g._install_hook(str(tmp_path)) == 0
    got = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert got.stdout.strip() == ""  # unset
    assert (tmp_path / ".git" / "hooks" / "pre-commit").is_file()


def test_install_hook_outside_worktree_is_noop(tmp_path, capsys):
    import release_core.verbs.gate as g

    rc = g._install_hook(str(tmp_path))  # not a git repo
    assert rc == 0
    assert "not inside a git work tree" in capsys.readouterr().out
    assert not (tmp_path / ".git").exists()


def test_gate_help(capsys):
    assert gate.main(["--help"]) == 0
    assert "gate" in capsys.readouterr().out
