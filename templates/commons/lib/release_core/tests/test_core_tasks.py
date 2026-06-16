"""``release-core test-unit|test-e2e|test-all|build|run`` — the runnable task
verbs (release#507). They detect THIS repo's real command and exec it,
propagating the exit code; test-* skip-with-notice when nothing is wired,
build/run error when no command exists.
"""

from __future__ import annotations

import click
from release_core import cli_entry
from release_core.repo_commands import Cmd
from release_core.verbs import tasks


def _root() -> click.Group:
    return cli_entry.root


def test_verbs_registered():
    names = set(_root().commands)
    assert {"test-unit", "test-e2e", "test-all", "coverage", "build", "run"} <= names
    for n in ("test-unit", "test-e2e", "test-all", "coverage", "build", "run"):
        assert (_root().commands[n].short_help or "").strip()


# --- skip-with-notice vs propagate ---------------------------------------


def test_test_unit_skips_with_notice_when_nothing(monkeypatch, capsys):
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "unit_commands", lambda r: [])
    assert tasks.test_unit([]) == 0  # not a failure — nothing to run
    assert "nothing to run" in capsys.readouterr().out


def test_test_unit_fans_out_and_propagates_failure(monkeypatch):
    calls: list[list[str]] = []

    def _fake_exec(cmd, root):
        calls.append(cmd.argv)
        return 0 if cmd.label == "node" else 2  # rust fails

    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks, "_preflight", lambda cmd, root: 0)  # clean tree
    monkeypatch.setattr(tasks, "_exec", _fake_exec)
    monkeypatch.setattr(
        tasks.repo_commands,
        "unit_commands",
        lambda r: [
            Cmd(["npm", "run", "test:unit"], "npm run test:unit", label="node"),
            Cmd(["cargo", "test"], "cargo test", label="rust", cwd="src-tauri"),
        ],
    )
    assert tasks.test_unit([]) == 2
    assert len(calls) == 2  # ran node then rust, stopped at the failure


def test_test_all_runs_unit_then_e2e(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(
        tasks.repo_commands, "unit_commands", lambda r: [Cmd(["u"], "u", label="node")]
    )
    monkeypatch.setattr(
        tasks.repo_commands, "e2e_commands", lambda r: [Cmd(["e"], "e", label="node")]
    )

    def _fake_exec(cmd, root):
        order.append(cmd.display)
        return 0

    monkeypatch.setattr(tasks, "_exec", _fake_exec)
    assert tasks.test_all([]) == 0
    assert order == ["u", "e"]


def test_test_all_stops_if_unit_fails(monkeypatch):
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "unit_commands", lambda r: [Cmd(["u"], "u")])
    e2e_ran = {"v": False}

    def _e2e(r):
        e2e_ran["v"] = True
        return [Cmd(["e"], "e")]

    monkeypatch.setattr(tasks.repo_commands, "e2e_commands", _e2e)
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: 3)
    assert tasks.test_all([]) == 3
    # e2e_commands is queried up front, but the e2e command must NOT exec.


# --- coverage: loud-error semantics (release#568) -------------------------


def test_coverage_errors_loudly_when_no_tool_for_kind(monkeypatch, capsys):
    # UNLIKE the test verbs, coverage never skips-with-notice: asked-for
    # coverage with no coverage-capable component is exit 1 + a clear, non-
    # alarming kind-naming notice that points at how-to (release#701).
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "coverage_commands", lambda r: [])
    monkeypatch.setattr(tasks.manifest, "detect_kind", lambda r: "nvim-plugin")
    assert tasks.coverage([]) == 1
    err = capsys.readouterr().err
    assert "nvim-plugin Kind has no coverage tool" in err
    assert "expected" in err  # reads as an expected notice, not a crash
    assert "release-core how-to" in err  # points at the discoverability path


def test_coverage_rejects_unknown_args(capsys):
    # An unknown/typo'd arg is a usage error, not silently ignored (#732 review).
    assert tasks.coverage(["--verbsoe"]) == 2
    err = capsys.readouterr().err
    assert "unexpected argument" in err
    assert "--verbsoe" in err


def test_coverage_kind_detection_failure_still_errors(monkeypatch, capsys):
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "coverage_commands", lambda r: [])

    def _boom(r):
        raise tasks.manifest.KindError("nope")

    monkeypatch.setattr(tasks.manifest, "detect_kind", _boom)
    assert tasks.coverage([]) == 1
    assert "unknown Kind has no coverage tool" in capsys.readouterr().err


def test_coverage_hard_errors_when_cargo_llvm_cov_missing(monkeypatch, capsys):
    # Missing tool = loud non-zero with the install command, never a silent
    # skip (the hard-gate philosophy). Nothing may exec.
    executed = {"v": False}
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks, "which", lambda name: None)
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: executed.update(v=True) or 0)
    monkeypatch.setattr(
        tasks.repo_commands,
        "coverage_commands",
        lambda r: [Cmd(["cargo", "llvm-cov", "--all-features"], "cargo llvm-cov", label="rust")],
    )
    assert tasks.coverage([]) == 1
    assert "cargo install cargo-llvm-cov" in capsys.readouterr().err
    assert not executed["v"]


def test_coverage_fans_out_and_propagates_failure(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks, "which", lambda name: "/usr/bin/cargo-llvm-cov")
    monkeypatch.setattr(tasks, "_preflight", lambda cmd, root: 0)  # clean tree

    def _fake_exec(cmd, root, *, raw):
        calls.append(cmd.argv)
        return 0 if cmd.label == "node" else 5  # rust leg fails

    monkeypatch.setattr(tasks, "_coverage_exec", _fake_exec)
    monkeypatch.setattr(
        tasks.repo_commands,
        "coverage_commands",
        lambda r: [
            Cmd(["pnpm", "run", "test:unit", "--coverage"], "pnpm …", label="node"),
            Cmd(["cargo", "llvm-cov", "--all-features"], "cargo llvm-cov", label="rust"),
        ],
    )
    assert tasks.coverage([]) == 5
    assert len(calls) == 2  # node ran, rust ran + failed, stop there


def test_coverage_go_pair_stops_at_failed_test_leg(monkeypatch):
    # `go test -coverprofile` failing must short-circuit the `go tool cover`
    # leg — the && semantics of the issue's go command.
    calls: list[list[str]] = []
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(
        tasks, "_coverage_exec", lambda cmd, root, *, raw: calls.append(cmd.argv) or 2
    )
    monkeypatch.setattr(
        tasks.repo_commands,
        "coverage_commands",
        lambda r: [
            Cmd(["go", "test", "-coverprofile=/tmp/c.out", "./..."], "go test", label="go"),
            Cmd(["go", "tool", "cover", "-func=/tmp/c.out"], "go tool cover", label="go"),
        ],
    )
    assert tasks.coverage([]) == 2
    assert calls == [["go", "test", "-coverprofile=/tmp/c.out", "./..."]]


# --- coverage: concise default output, --verbose/--raw opt-in (release#694) -


def _coverage_one(monkeypatch, run_returncode, run_stdout):
    """Wire a single coverage command + a fake subprocess.run for _coverage_exec."""
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks, "which", lambda name: "/usr/bin/cargo-llvm-cov")
    monkeypatch.setattr(
        tasks.repo_commands,
        "coverage_commands",
        lambda r: [Cmd(["cargo", "llvm-cov", "--all-features"], "cargo llvm-cov", label="rust")],
    )

    class _Proc:
        def __init__(self):
            self.returncode = run_returncode
            self.stdout = run_stdout

    recorded = {}

    def _fake_run(argv, **kw):
        recorded["kw"] = kw
        return _Proc()

    monkeypatch.setattr(tasks.subprocess, "run", _fake_run)
    return recorded


def test_coverage_default_hides_verbose_stream_shows_tail(monkeypatch, capsys):
    # A long build log + a trailing table: default view hides the body and shows
    # the tail (the summary), with a hidden-lines notice (release#694).
    body = "\n".join(f"compiling crate line {i}" for i in range(200))
    table = "TOTAL    1234    56    95.46%"
    _coverage_one(monkeypatch, 0, body + "\n" + table + "\n")
    assert tasks.coverage([]) == 0
    out = capsys.readouterr().out
    assert table in out  # the summary survived
    assert "lines of build/test output hidden" in out
    assert "compiling crate line 5" not in out  # the body was suppressed


def test_coverage_default_captures_not_streams(monkeypatch):
    # Default (non-raw) must capture: subprocess.run gets stdout=PIPE.
    recorded = _coverage_one(monkeypatch, 0, "TOTAL 95%\n")
    assert tasks.coverage([]) == 0
    assert recorded["kw"].get("stdout") is tasks.subprocess.PIPE


def test_coverage_verbose_streams_full_output(monkeypatch):
    # --verbose/--raw streams live: subprocess.run is NOT given a PIPE.
    for flag in ("--verbose", "--raw"):
        recorded = _coverage_one(monkeypatch, 0, None)
        assert tasks.coverage([flag]) == 0
        assert "stdout" not in recorded["kw"]  # streamed, not captured


def test_coverage_failure_prints_full_output_for_diagnosis(monkeypatch, capsys):
    # On failure the captured stream is printed in full so the error is
    # diagnosable, and the failing exit code propagates.
    body = "\n".join(f"line {i}" for i in range(100))
    _coverage_one(monkeypatch, 7, body + "\nerror: coverage run failed\n")
    assert tasks.coverage([]) == 7
    out = capsys.readouterr().out
    assert "error: coverage run failed" in out
    assert "line 5" in out  # full body shown, not tailed
    assert "hidden" not in out


# --- build / run: error when no command ----------------------------------


def test_build_errors_when_no_command(monkeypatch, capsys):
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "build_command", lambda r: None)
    assert tasks.build([]) == 1
    assert "no build command" in capsys.readouterr().err


def test_run_errors_when_no_command(monkeypatch, capsys):
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "run_command", lambda r: None)
    assert tasks.run([]) == 1
    assert "no run command" in capsys.readouterr().err


def test_build_execs_and_propagates(monkeypatch):
    captured = {}
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(
        tasks.repo_commands,
        "build_command",
        lambda r: Cmd(["cargo", "build", "--release"], "cargo build --release", label="rust"),
    )

    def _fake_exec(cmd, root):
        captured["argv"] = cmd.argv
        return 0

    monkeypatch.setattr(tasks, "_exec", _fake_exec)
    assert tasks.build([]) == 0
    assert captured["argv"] == ["cargo", "build", "--release"]


# --- arg forwarding (single-command verbs) -------------------------------


def test_build_forwards_args_npm_needs_dashdash(monkeypatch):
    captured = {}
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "detect_pm", lambda r: "npm")
    monkeypatch.setattr(tasks, "_preflight", lambda cmd, root: 0)  # clean tree
    monkeypatch.setattr(
        tasks.repo_commands,
        "build_command",
        lambda r: Cmd(["npm", "run", "build"], "npm run build", label="node"),
    )
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: captured.update(argv=cmd.argv) or 0)
    assert tasks.build(["--linux"]) == 0
    assert captured["argv"] == ["npm", "run", "build", "--", "--linux"]


def test_build_forwards_args_cargo_direct(monkeypatch):
    captured = {}
    monkeypatch.setattr(tasks, "_repo_root", lambda: "/repo")
    monkeypatch.setattr(tasks.repo_commands, "detect_pm", lambda r: "npm")
    monkeypatch.setattr(
        tasks.repo_commands,
        "build_command",
        lambda r: Cmd(["cargo", "build", "--release"], "cargo build --release", label="rust"),
    )
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: captured.update(argv=cmd.argv) or 0)
    assert tasks.build(["--features", "x"]) == 0
    assert captured["argv"] == ["cargo", "build", "--release", "--features", "x"]


# --- nextest upgrade in _resolve_argv ------------------------------------


def test_cargo_test_upgrades_to_nextest_when_present(monkeypatch):
    monkeypatch.setattr(tasks, "which", lambda name: "/usr/bin/cargo-nextest")
    cmd = Cmd(["cargo", "test", "--all-features"], "cargo test", label="rust")
    assert tasks._resolve_argv(cmd, "/repo") == [
        "cargo",
        "nextest",
        "run",
        "--all-features",
        "--no-tests=pass",
    ]


def test_cargo_test_stays_plain_without_nextest(monkeypatch):
    monkeypatch.setattr(tasks, "which", lambda name: None)
    cmd = Cmd(["cargo", "test", "--all-features"], "cargo test", label="rust")
    assert tasks._resolve_argv(cmd, "/repo") == ["cargo", "test", "--all-features"]


# --- preflight: friendly-fail on a bare checkout (#710/#718/#711/#702) ----


# These exercise the REAL preflight against a real tmp tree (never a fake
# "/repo" + a stubbed preflight): the verb must friendly-fail BEFORE exec, so a
# real `_exec` would `chdir` into the tree — `_exec` is monkeypatched only as a
# tripwire asserting it is never reached.


def test_test_unit_friendly_fails_when_deps_missing(monkeypatch, capsys, tmp_path):
    # A node command on a bare clone (no node_modules) must NOT exec into a raw
    # `vitest: command not found` — it prints the install hint and exits 1.
    (tmp_path / "pnpm-lock.yaml").write_text("")  # no node_modules → deps missing
    executed = {"v": False}
    monkeypatch.setattr(tasks, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: executed.update(v=True) or 0)
    monkeypatch.setattr(
        tasks.repo_commands,
        "unit_commands",
        lambda r: [Cmd(["pnpm", "run", "test:unit"], "pnpm run test:unit", label="node")],
    )
    assert tasks.test_unit([]) == 1
    assert not executed["v"]  # never exec'd the failing command
    err = capsys.readouterr().err
    assert "dependencies not installed" in err and "pnpm install" in err


def test_coverage_friendly_fails_when_deps_missing(monkeypatch, capsys, tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")  # no node_modules → deps missing
    executed = {"v": False}
    monkeypatch.setattr(tasks, "_repo_root", lambda: str(tmp_path))
    # coverage execs through _coverage_exec (release#694), not _exec — tripwire
    # the real exec path so a preflight miss would be a loud assert, never a raw
    # subprocess.
    monkeypatch.setattr(
        tasks, "_coverage_exec", lambda cmd, root, *, raw: executed.update(v=True) or 0
    )
    monkeypatch.setattr(
        tasks.repo_commands,
        "coverage_commands",
        lambda r: [Cmd(["pnpm", "run", "test:unit", "--coverage"], "pnpm …", label="node")],
    )
    assert tasks.coverage([]) == 1
    assert not executed["v"]
    assert "dependencies not installed" in capsys.readouterr().err


def test_test_unit_runs_when_preflight_clean(monkeypatch, tmp_path):
    # A ready tree (deps installed) execs normally.
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(tasks, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: 0)
    monkeypatch.setattr(
        tasks.repo_commands,
        "unit_commands",
        lambda r: [Cmd(["pnpm", "run", "test:unit"], "pnpm run test:unit", label="node")],
    )
    assert tasks.test_unit([]) == 0


def test_build_friendly_fails_when_artifact_missing(monkeypatch, capsys, tmp_path):
    # deps present (node_modules) so we get past the deps check; a node build in
    # a repo with an unbuilt wasm crate friendly-fails on the missing pkg/.
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "node_modules").mkdir()
    crate = tmp_path / "wasm" / "phos-viewer-wasm"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname='x'\n")
    executed = {"v": False}
    monkeypatch.setattr(tasks, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(tasks, "_exec", lambda cmd, root: executed.update(v=True) or 0)
    monkeypatch.setattr(
        tasks.repo_commands,
        "build_command",
        lambda r: Cmd(["pnpm", "run", "build"], "pnpm run build", label="node"),
    )
    assert tasks.build([]) == 1
    assert not executed["v"]
    assert "wasm packages not built" in capsys.readouterr().err


def test_help(capsys):
    for fn in (
        tasks.test_unit,
        tasks.test_e2e,
        tasks.test_all,
        tasks.coverage,
        tasks.build,
        tasks.run,
    ):
        assert fn(["--help"]) == 0
    assert capsys.readouterr().out
