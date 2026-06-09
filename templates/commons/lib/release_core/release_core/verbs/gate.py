"""gate — run THE pre-commit quality gate over this repo, once.

``release-core gate`` is the single, Kind-agnostic entry point for "run the
checks" that an agent or human reaches for every loop. It runs the SAME
``lefthook run pre-commit`` gate CI runs — the unified-gate doctrine
(release#348, CLAUDE.md "The gate is ONE definition, run everywhere").

SCOPE — the gate is the fast lint/format/static-analysis set (fmt, clippy,
eslint, markdownlint, …). It deliberately does NOT run the test suite: tests are
a separate, slower CI check, not a pre-commit hook. So a green gate is NECESSARY
but not SUFFICIENT for "CI will be green" — run the Kind's test command
(``cargo test`` / ``npm test`` / ``pytest``; see ``release-core how-to``)
separately before pushing. (Conflating the two is the "gate over-claims its
coverage" trap — same class as a check that silently no-ops; caught in the #501
lex-fmt dogfood run.)

Why ``--all-files`` and not the staged set: lefthook's default ``pre-commit``
run inspects only STAGED files, so a bare run over an unstaged edit reports a
false green (everything ``(skip) no files for inspection``). ``release-core
gate`` always runs the whole tree so "gate green" is never a lie about an
unstaged change. (caught in the #501 footprint validation run.)

A missing lefthook is a HARD gate failure — the gate never skips. Re-run the
bootstrap (``setup-dev-env.sh`` / ``release-core init``) to provision it.

Config resolution (#501 / WS3 release#524): the gate definition lives ONLY in the
ephemeral ``.release/`` build dir now — the root ``lefthook.yml`` discovery symlink
is gone — so this verb points lefthook at ``.release/lefthook.yml`` explicitly via
``LEFTHOOK_CONFIG`` when present, falling back to lefthook's default discovery for a
release-self / not-yet-migrated repo that still keeps a root config.

Git-hook integration (WS3): ``release-core gate --install-hook`` writes
``.git/hooks/pre-commit`` to ``exec release-core gate --hook`` — so the committed
gate runs through the binary, with no tracked root ``lefthook.yml`` for lefthook to
discover. ``--hook`` runs lefthook over the STAGED set (not ``--all-files``), so
``stage_fixed`` auto-fix+restage stays correct at commit time; the bare,
agent-facing ``release-core gate`` keeps ``--all-files`` (no false green on an
unstaged edit).

Usage:
  release-core gate [extra lefthook args...]   # agent/human: full --all-files gate
  release-core gate --hook [lefthook args...]  # git pre-commit: staged-set gate
  release-core gate --install-hook             # wire .git/hooks/pre-commit

Exit codes:
  0  — the gate passed (or the hook was installed)
  1  — the gate failed, or lefthook is not installed (a HARD failure, not a skip)
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

USAGE = __doc__ or ""

_HOOK_BODY = """\
#!/bin/sh
# Managed by release — do not edit. Regenerate via release-core gate --install-hook.
# THE pre-commit gate, run through the binary (release#524): the gate definition
# lives in the ephemeral .release/ build dir, so there is no tracked root
# lefthook.yml for a stock git hook to discover — release-core gate points lefthook
# at .release/lefthook.yml itself.
exec release-core gate --hook "$@"
"""


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
    then a PATH-global install. None when absent (a hard gate failure).

    ``shutil.which`` (not ``os.access``) so Windows PATHEXT resolution finds a
    ``lefthook.cmd`` shim under node_modules/.bin too."""
    from shutil import which

    local = which("lefthook", path=os.path.join(root, "node_modules", ".bin"))
    return local or which("lefthook")


def _install_hook(root: str) -> int:
    """Write .git/hooks/pre-commit to exec ``release-core gate --hook``.

    The committed gate runs through the binary (WS3, release#524): there is no
    tracked root lefthook.yml for a stock git hook to discover, so we install our
    own one-line hook that defers to this verb (which points lefthook at
    .release/lefthook.yml). Idempotent; unsets any core.hooksPath redirect first
    (husky / a prior hook manager would otherwise shadow .git/hooks/)."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except FileNotFoundError:
        print("error: git not found — cannot install the pre-commit hook.", file=sys.stderr)
        return 1
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print("release-core gate: not inside a git work tree — skipping hook install.")
        return 0

    # A custom core.hooksPath redirects git away from .git/hooks/, where we write
    # the shim — unset any value so the install actually takes effect.
    hooks_path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    if hooks_path:
        subprocess.run(["git", "config", "--unset", "core.hooksPath"], cwd=root)

    hooks_dir = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    hooks_dir = os.path.join(root, hooks_dir) if not os.path.isabs(hooks_dir) else hooks_dir
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, "pre-commit")
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write(_HOOK_BODY)
    mode = os.stat(hook_path).st_mode
    os.chmod(hook_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"release-core gate: installed pre-commit hook → {hook_path}")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    root = _repo_root()

    if argv and argv[0] == "--install-hook":
        return _install_hook(root)

    # --hook: the git pre-commit entry point. Run lefthook over the STAGED set
    # (lefthook's default — NO --all-files), so stage_fixed auto-fix+restage works
    # at commit time. The bare gate keeps --all-files (no false green on an
    # unstaged edit — see the module docstring).
    hook_mode = bool(argv) and argv[0] == "--hook"
    if hook_mode:
        argv = argv[1:]

    lefthook = _resolve_lefthook(root)
    if lefthook is None:
        print(
            "error: lefthook not found — the gate does not skip. Re-run the "
            "bootstrap (setup-dev-env.sh / release-core init).",
            file=sys.stderr,
        )
        return 1

    env = dict(os.environ)
    # Strip lefthook's truecolor banner — the `\e[38;2;r;g;b` gradient is a wall
    # of escape codes when an agent captures the output non-interactively. Honor
    # an explicit NO_COLOR=0 / FORCE_COLOR if the caller really wants color.
    if "FORCE_COLOR" not in env and env.get("NO_COLOR") != "0":
        env["NO_COLOR"] = "1"
    # Point lefthook at the managed config explicitly when it lives under
    # .release/ — the root discovery symlink is gone (WS3), so this is how lefthook
    # finds the gate. Falls back to lefthook's default discovery for a release-self
    # / not-yet-migrated repo that still keeps a root config.
    managed_cfg = os.path.join(root, ".release", "lefthook.yml")
    if os.path.isfile(managed_cfg) and "LEFTHOOK_CONFIG" not in env:
        env["LEFTHOOK_CONFIG"] = managed_cfg

    scope = [] if hook_mode else ["--all-files"]
    cmd = [lefthook, "run", "pre-commit", *scope, "--no-tty", *argv]
    return subprocess.run(cmd, cwd=root, env=env).returncode
