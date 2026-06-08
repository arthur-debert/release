"""gate — run THE pre-commit quality gate over this repo, once.

``release-core gate`` is the single, Kind-agnostic entry point for "run the
checks" that an agent or human reaches for every loop. It runs the SAME gate CI
runs (``lefthook run pre-commit --all-files``), so green here means green
everywhere — the unified-gate doctrine (release#348, CLAUDE.md "The gate is ONE
definition, run everywhere").

Why ``--all-files`` and not the staged set: lefthook's default ``pre-commit``
run inspects only STAGED files, so a bare run over an unstaged edit reports a
false green (everything ``(skip) no files for inspection``). ``release-core
gate`` always runs the whole tree so "gate green" is never a lie about an
unstaged change. (caught in the #501 footprint validation run.)

A missing lefthook is a HARD gate failure — the gate never skips. Re-run the
bootstrap (``setup-dev-env.sh`` / ``release-core init``) to provision it.

Config resolution is forward-compatible with the minimal-footprint model
(#501): if ``.release/lefthook.yml`` exists it is pointed at explicitly via
``LEFTHOOK_CONFIG``, so the gate runs even once the root ``lefthook.yml``
discovery symlink is gone. Today the root symlink still resolves to the same
file, so either path works.

Usage:
  release-core gate [extra lefthook args...]

Exit codes:
  0  — the gate passed
  1  — the gate failed, or lefthook is not installed (a HARD failure, not a skip)
"""

from __future__ import annotations

import os
import subprocess
import sys

USAGE = __doc__ or ""


def _repo_root() -> str:
    """The git work-tree root, so the gate binds to this repo from any cwd."""
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


def _resolve_lefthook(root: str) -> str | None:
    """node-stack consumers vendor lefthook under node_modules/.bin; prefer it,
    then a PATH-global install. None when absent (a hard gate failure)."""
    local = os.path.join(root, "node_modules", ".bin", "lefthook")
    if os.access(local, os.X_OK):
        return local
    from shutil import which

    return which("lefthook")


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    root = _repo_root()
    lefthook = _resolve_lefthook(root)
    if lefthook is None:
        print(
            "error: lefthook not found — the gate does not skip. Re-run the "
            "bootstrap (setup-dev-env.sh / release-core init).",
            file=sys.stderr,
        )
        return 1

    env = dict(os.environ)
    # Point lefthook at the managed config explicitly when it lives under
    # .release/ — forward-compatible with dropping the root discovery symlink.
    managed_cfg = os.path.join(root, ".release", "lefthook.yml")
    if os.path.isfile(managed_cfg) and "LEFTHOOK_CONFIG" not in env:
        env["LEFTHOOK_CONFIG"] = managed_cfg

    cmd = [lefthook, "run", "pre-commit", "--all-files", "--no-tty", *argv]
    return subprocess.run(cmd, cwd=root, env=env).returncode
