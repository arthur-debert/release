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


def test_how_to_dev_cycle_branches_from_origin_not_local_main():
    # release#566: SessionStart may have auto-committed a managed sync on the
    # clone's LOCAL default branch; branching there carries that alien commit
    # into the feature PR diff. Step 1 must say branch off origin/<default>.
    body = how_to._render("rust-cli")
    assert "Branch off origin/<default-branch>" in body
    # The example ref uses the same placeholder — never a hard-coded
    # `origin/main` that misleads repos whose default branch differs.
    assert "origin/main" not in body
    assert "NOT the local default branch" in body
    assert "auto-committed a managed sync" in body


def test_how_to_documents_the_materialize_only_ci_pattern(tmp_path):
    # release#581: a consumer-authored CI job invoking a managed bin/ tool must
    # materialize the managed tree first — how-to is the consumer-facing home of
    # that pattern (arm-gate with toolset:'false'), on BOTH render paths.
    for body in (how_to._render("rust-cli"), how_to._render_repo(str(tmp_path), "unknown")):
        assert "arthur-debert/release/.github/actions/arm-gate@v2" in body
        assert "toolset: 'false'" in body
        assert "DO NOT exist on a fresh CI checkout" in body
        assert "Never hand-copy the materialize recipe" in body


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
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: None)
    monkeypatch.setattr(gate, "_repo_root", lambda: ".")
    assert gate.main([]) == 1
    assert "lefthook not found" in capsys.readouterr().err


def test_gate_no_lefthook_at_the_pin_is_a_hard_failure(monkeypatch, capsys):
    """One gate, ONE runner (release#567): a PATH that only carries lefthook at
    some OTHER version than the toolset pin is a hard failure naming the pin and
    the bootstrap remedy — never a silent fall-back to whatever is around."""
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: "2.1.9")
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: None)
    monkeypatch.setattr(gate, "_repo_root", lambda: ".")
    assert gate.main([]) == 1
    err = capsys.readouterr().err
    assert "pinned toolset version 2.1.9" in err
    assert "setup-dev-env.sh" in err


def _gated_root(tmp_path):
    """A repo root whose gate IS materialized (root config), so main() gets past
    the release#567 unmaterialized-config hard failure."""
    (tmp_path / "lefthook.yml").write_text("pre-commit:\n")
    return str(tmp_path)


def test_gate_runs_all_files_over_the_repo(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return _Result()

    root = _gated_root(tmp_path)
    monkeypatch.setattr(gate, "_repo_root", lambda: root)
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    cmd = captured["cmd"]
    expected = ["lefthook", "run", "pre-commit", "--no-auto-install", "--all-files", "--no-tty"]
    assert cmd[:6] == expected
    assert captured["cwd"] == root


def test_gate_hook_mode_runs_staged_not_all_files(monkeypatch, tmp_path):
    """--hook (the git pre-commit entry) runs lefthook over the STAGED set — NO
    --all-files — so stage_fixed auto-fix+restage stays correct at commit time."""
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: _gated_root(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main(["--hook"]) == 0
    cmd = captured["cmd"]
    assert cmd == ["lefthook", "run", "pre-commit", "--no-auto-install", "--no-tty"]
    assert "--all-files" not in cmd  # staged set, so stage_fixed restage works
    assert "--no-auto-install" in cmd  # lefthook must not re-manage our binary hook


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
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.delenv("LEFTHOOK_CONFIG", raising=False)
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    assert captured["env"]["LEFTHOOK_CONFIG"] == str(tmp_path / ".release" / "lefthook.yml")


# --- one runner: the gate pins its lefthook to the toolset (release#567) ----


def test_pin_read_from_root_gate_tool_versions(tmp_path, monkeypatch):
    """The pin comes from the SAME single source the provisioners use —
    bin/gate-tool-versions.sh (release#499/#531)."""
    monkeypatch.delenv("LEFTHOOK_VERSION", raising=False)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gate-tool-versions.sh").write_text(
        '# pins\nLEFTHOOK_VERSION="${LEFTHOOK_VERSION:-9.9.9}"\n'
    )
    assert gate._pinned_lefthook_version(str(tmp_path)) == "9.9.9"


def test_pin_env_override_wins(tmp_path, monkeypatch):
    """LEFTHOOK_VERSION env is the single source's documented override knob —
    it wins over the file, matching the shell-side `:-` semantics."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gate-tool-versions.sh").write_text(
        'LEFTHOOK_VERSION="${LEFTHOOK_VERSION:-9.9.9}"\n'
    )
    monkeypatch.setenv("LEFTHOOK_VERSION", "1.2.3")
    assert gate._pinned_lefthook_version(str(tmp_path)) == "1.2.3"


def test_pin_falls_back_to_the_wheel_bundle(tmp_path, monkeypatch):
    """Pre-init (fresh clone), the consumer's bin/gate-tool-versions.sh is a
    dangling symlink into the not-yet-composed .release/ — the wheel-bundled
    copy that ships WITH this code still pins the runner."""
    monkeypatch.delenv("LEFTHOOK_VERSION", raising=False)
    pkg = tmp_path / "pkg"
    bundled = pkg / "_bundled_templates" / "templates" / "commons" / "bin"
    bundled.mkdir(parents=True)
    (bundled / "gate-tool-versions.sh").write_text(
        'LEFTHOOK_VERSION="${LEFTHOOK_VERSION:-7.7.7}"\n'
    )
    (pkg / "verbs").mkdir()
    monkeypatch.setattr(gate, "__file__", str(pkg / "verbs" / "gate.py"))
    repo = tmp_path / "repo"  # no bin/gate-tool-versions.sh here
    repo.mkdir()
    assert gate._pinned_lefthook_version(str(repo)) == "7.7.7"


def _fake_lefthook(dirpath, version: str) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    binpath = dirpath / "lefthook"
    binpath.write_text(f'#!/bin/sh\necho "lefthook version {version}"\n')
    binpath.chmod(0o755)


def test_resolve_lefthook_picks_the_pinned_binary_not_first_on_path(tmp_path, monkeypatch):
    """PATH order does not pick the runner — the PIN does. A floating/stale
    lefthook earlier on PATH is skipped in favor of the toolset-pinned one
    (the #525 probe's version-skew bug)."""
    stale = tmp_path / "stale"
    pinned = tmp_path / "pinned"
    _fake_lefthook(stale, "2.1.1")
    _fake_lefthook(pinned, "2.1.9")
    monkeypatch.setenv("PATH", f"{stale}{os.pathsep}{pinned}")
    got = gate._resolve_lefthook("2.1.9")
    assert got == str(pinned / "lefthook")


def test_resolve_lefthook_skips_node_modules(tmp_path, monkeypatch):
    """A consumer's package.json-vendored lefthook (node_modules/.bin on PATH)
    is a floating consumer dep, never the gate's runner — even at the pin."""
    vendored = tmp_path / "node_modules" / ".bin"
    _fake_lefthook(vendored, "2.1.9")
    monkeypatch.setenv("PATH", str(vendored))
    assert gate._resolve_lefthook("2.1.9") is None
    # Without a pin it is skipped too — node_modules is never the toolset.
    assert gate._resolve_lefthook(None) is None


def test_resolve_lefthook_unpinned_takes_first_on_path(tmp_path, monkeypatch):
    """No knowable pin (source-tree dev checkout): the first non-node_modules
    PATH lefthook is the runner — still one deterministic runner per env."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _fake_lefthook(first, "2.0.0")
    _fake_lefthook(second, "2.1.9")
    monkeypatch.setenv("PATH", f"{first}{os.pathsep}{second}")
    assert gate._resolve_lefthook(None) == str(first / "lefthook")


# --- fail loud on an unmaterialized gate config (release#567) ---------------


def test_gate_unmaterialized_config_fails_loud(monkeypatch, tmp_path, capsys):
    """No .release/lefthook.yml AND no root config: the gate exits non-zero and
    names `release-core init` — never lefthook's warn-and-pass (the
    first-commit-of-session boot hole)."""
    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.delenv("LEFTHOOK_CONFIG", raising=False)
    assert gate.main(["--hook"]) == 1
    err = capsys.readouterr().err
    assert "not materialized" in err
    assert "release-core init" in err


def test_gate_root_config_uses_default_discovery(monkeypatch, tmp_path):
    """release-self / not-yet-migrated: a root lefthook.yml satisfies the gate
    via lefthook's own discovery — no LEFTHOOK_CONFIG injected."""
    (tmp_path / "lefthook.yml").write_text("pre-commit:\n")
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.delenv("LEFTHOOK_CONFIG", raising=False)
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    assert "LEFTHOOK_CONFIG" not in captured["env"]


def test_gate_explicit_lefthook_config_missing_fails_loud(monkeypatch, tmp_path, capsys):
    """An explicit LEFTHOOK_CONFIG pointing at a missing file is a hard failure
    too — lefthook would warn-and-pass it."""
    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setenv("LEFTHOOK_CONFIG", str(tmp_path / "nope.yml"))
    assert gate.main([]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_gate_explicit_lefthook_config_is_respected(monkeypatch, tmp_path):
    """A caller's existing LEFTHOOK_CONFIG wins over the managed config."""
    (tmp_path / ".release").mkdir()
    (tmp_path / ".release" / "lefthook.yml").write_text("pre-commit:\n")
    mine = tmp_path / "mine.yml"
    mine.write_text("pre-commit:\n")
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setenv("LEFTHOOK_CONFIG", str(mine))
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    assert captured["env"]["LEFTHOOK_CONFIG"] == str(mine)


def test_gate_empty_lefthook_config_counts_as_unset(monkeypatch, tmp_path):
    """An empty/whitespace LEFTHOOK_CONFIG is unset — scrubbed from the env so
    it never leaks to lefthook; the managed config applies as usual."""
    (tmp_path / ".release").mkdir()
    (tmp_path / ".release" / "lefthook.yml").write_text("pre-commit:\n")
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setenv("LEFTHOOK_CONFIG", "   ")
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    assert gate.main([]) == 0
    assert captured["env"]["LEFTHOOK_CONFIG"] == str(tmp_path / ".release" / "lefthook.yml")


def test_gate_explicit_relative_lefthook_config_resolves_against_root(monkeypatch, tmp_path):
    """A RELATIVE LEFTHOOK_CONFIG is relative to the repo root (lefthook runs
    with cwd=root) — validated and resolved there, not against the caller's
    cwd, so the gate works from any subdirectory."""
    (tmp_path / ".release").mkdir()
    (tmp_path / ".release" / "lefthook.yml").write_text("pre-commit:\n")
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, cwd, env):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(gate, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(gate, "_pinned_lefthook_version", lambda root: None)
    monkeypatch.setattr(gate, "_resolve_lefthook", lambda pin: "lefthook")
    monkeypatch.setenv("LEFTHOOK_CONFIG", os.path.join(".release", "lefthook.yml"))
    monkeypatch.setattr(gate.subprocess, "run", _fake_run)
    # cwd is the pytest rootdir (a subdir-of-nowhere relative to tmp_path) —
    # the relative path must still validate against root and pass resolved.
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
