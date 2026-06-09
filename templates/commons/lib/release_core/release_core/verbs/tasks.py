"""test-unit / test-e2e / test-all / build / run — detect THIS repo's real
command for each task and exec it, propagating the exit code.

These are the runnable half of release#507: where ``release-core how-to`` renders
what each verb resolves to, these execute it. They read :mod:`release_core.repo_commands`
— the component detector — so they dispatch the repo's REAL command (the actual
``test:unit`` script, ``cargo test`` under ``src-tauri/``, …) rather than a
per-Kind guess. They graduate the per-Kind ``bin/{check-tests,build,e2e}`` shell
wrappers into the binary (the wrappers stay until CI is migrated — a follow-up).

Dispatch shape (see repo_commands for the rationale):
  test-unit  fan-out over every component's unit suite, cheap-first.
  test-e2e   the build-dependent e2e suite, kept separate from unit.
  test-all   unit then e2e.
  build/run  the SINGLE app-root command.

Skip-vs-fail: a test verb with NOTHING wired skips-with-notice (exit 0) — an
unscaffolded suite must not fail the loop (mirrors the wrappers). ``build``/``run``
asked of a repo with no derivable command is a real error (exit 1) — you asked to
build something that has no build.
"""

from __future__ import annotations

import os
import subprocess
import sys
from shutil import which

from .. import repo_commands
from ..repo_commands import Cmd


def _repo_root() -> str:
    """The git work-tree root, so the verb binds to this repo from any cwd
    (mirrors gate._repo_root / how_to._repo_root)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.getcwd()


def _resolve_argv(cmd: Cmd, root: str) -> list[str]:
    """Final exec argv: upgrade ``cargo test`` → ``cargo nextest run`` when
    nextest is installed (the portfolio canonical runner), PRESERVING the
    command's own flags and adding ``--no-tests=pass`` so a zero-test crate
    doesn't fail the umbrella. Everything else runs verbatim."""
    if cmd.argv[:2] == ["cargo", "test"] and which("cargo-nextest"):
        rest = cmd.argv[2:]  # keep --all-features and any other cargo test flags
        return ["cargo", "nextest", "run", *rest, "--no-tests=pass"]
    return list(cmd.argv)


def _exec(cmd: Cmd, root: str) -> int:
    """Run one command in its component dir, streaming output, return its code."""
    cwd = root if cmd.cwd is None else os.path.join(root, cmd.cwd)
    argv = _resolve_argv(cmd, root)
    label = f"[{cmd.label}] " if cmd.label else ""
    print(f"→ {label}{cmd.display}", flush=True)
    return subprocess.run(argv, cwd=cwd).returncode


def _fanout(cmds: list[Cmd], root: str) -> int:
    """Run each command in order; stop at the first non-zero and propagate it."""
    for cmd in cmds:
        rc = _exec(cmd, root)
        if rc != 0:
            return rc
    return 0


def _is_help(argv: list[str]) -> bool:
    return bool(argv) and argv[0] in ("-h", "--help")


# --- test verbs -----------------------------------------------------------


def test_unit(argv: list[str]) -> int:
    """Run every component's unit suite (node test:unit/test + cargo + make)."""
    if _is_help(argv):
        print("release-core test-unit — run THIS repo's unit suites (all components)")
        return 0
    root = _repo_root()
    cmds = repo_commands.unit_commands(root)
    if not cmds:
        print("release-core test-unit: no unit suite wired in this repo — nothing to run.")
        return 0
    return _fanout(cmds, root)


def test_e2e(argv: list[str]) -> int:
    """Run the e2e suite (separate from unit; needs a built binary)."""
    if _is_help(argv):
        print("release-core test-e2e — run THIS repo's e2e suite (build-dependent)")
        return 0
    root = _repo_root()
    cmds = repo_commands.e2e_commands(root)
    if not cmds:
        print("release-core test-e2e: no test:e2e suite wired in this repo — nothing to run.")
        return 0
    return _fanout(cmds, root)


def test_all(argv: list[str]) -> int:
    """Run unit then e2e (unit first — fail fast on the cheap suite)."""
    if _is_help(argv):
        print("release-core test-all — run THIS repo's unit then e2e suites")
        return 0
    root = _repo_root()
    unit = repo_commands.unit_commands(root)
    e2e = repo_commands.e2e_commands(root)
    if not unit and not e2e:
        print("release-core test-all: no test suite wired in this repo — nothing to run.")
        return 0
    rc = _fanout(unit, root)
    if rc != 0:
        return rc
    return _fanout(e2e, root)


# --- build / run (single app-root command) --------------------------------


def _with_args(cmd: Cmd, extra: list[str], root: str) -> Cmd:
    """Append caller args to a single-command verb. npm's ``run`` needs an
    explicit ``--`` to forward flags to the underlying tool; pnpm/yarn/cargo/go
    take them directly (mirrors electron-app/bin/build's pm branching)."""
    if not extra:
        return cmd
    pm = repo_commands.detect_pm(root)
    if cmd.argv[:2] == [pm, "run"] and pm == "npm":
        argv = [*cmd.argv, "--", *extra]
    else:
        argv = [*cmd.argv, *extra]
    return Cmd(argv=argv, display=" ".join(argv), label=cmd.label, cwd=cmd.cwd)


def build(argv: list[str]) -> int:
    """Build this repo via its single app-root command (tauri/node/cargo/go)."""
    if _is_help(argv):
        print("release-core build — build THIS repo (the app-root build command)")
        return 0
    root = _repo_root()
    cmd = repo_commands.build_command(root)
    if cmd is None:
        print(
            "release-core build: no build command detected (no package.json "
            "build script, Cargo.toml, or go.mod). See `release-core how-to`.",
            file=sys.stderr,
        )
        return 1
    return _exec(_with_args(cmd, argv, root), root)


def run(argv: list[str]) -> int:
    """Run/dev this repo via its single app-root command (node dev / cargo run)."""
    if _is_help(argv):
        print("release-core run — run THIS repo (the app-root dev/run command)")
        return 0
    root = _repo_root()
    cmd = repo_commands.run_command(root)
    if cmd is None:
        print(
            "release-core run: no run command detected (no dev/start/watch "
            "script, Cargo.toml, or go.mod). See `release-core how-to`.",
            file=sys.stderr,
        )
        return 1
    return _exec(_with_args(cmd, argv, root), root)
