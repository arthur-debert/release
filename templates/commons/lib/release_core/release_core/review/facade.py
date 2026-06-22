"""facade — tie instructions + diff + backend into one runnable review.

`run_review(argv)` is the module entry (`python -m release_core.review …`). It:

  * resolves the review instructions (bundled default or an explicit file),
  * computes the diff to review,
  * builds the shared prompt and the chosen backend,
  * in ``--dry-run`` prints EXACTLY what would be sent to the agent (argv, stdin,
    each would-be temp file with its contents) without touching the backend, and
  * otherwise preflights, runs the backend, and writes the parsed JSON review.

The diff source is real PR resolution (``diff.resolve_pr``): given ``--pr N`` it
asks GitHub for the PR's base/head, fetches them locally (never a branch switch),
and reviews the three-dot diff. The agent backend runs in the PR's checkout so it
can read the surrounding files. This module is wired into the Click
``release-core`` CLI as ``release-core review run``.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import proc
from .backends import get_backend
from .backends.base import BackendUnavailable
from .diff import ReviewError, resolve_pr
from .instructions import load_instructions
from .prompt import build_prompt
from .schema import REVIEW_SCHEMA


def _render_dry_run(agent: str, command: dict) -> str:
    """Human-eyeballable rendering of what would be sent to the agent.

    Shows the argv, whether stdin is used (and its content), and each would-be
    temp file with its contents — so two agents' renderings can be compared.
    """
    lines: list[str] = []
    lines.append(f"=== DRY RUN: {agent} ===")
    lines.append("")
    lines.append("argv:")
    for arg in command["argv"]:
        lines.append(f"  {arg}")
    lines.append("")

    stdin = command.get("stdin")
    if stdin is None:
        lines.append("stdin: <none>")
    else:
        lines.append("stdin:")
        lines.append(stdin)
    lines.append("")

    files = command.get("files") or {}
    if not files:
        lines.append("files: <none>")
    else:
        lines.append("files:")
        for path, contents in files.items():
            lines.append(f"--- {path} ---")
            lines.append(contents)
            lines.append(f"--- end {path} ---")
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m release_core.review",
        description="Run a PR code review through a local agent backend.",
    )
    parser.add_argument("agent", choices=["codex", "agy"], help="Which review backend to use.")
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="PR number to review (resolved via `gh pr view`).",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="OWNER/NAME of the PR's repo (default: inferred from the current checkout).",
    )
    parser.add_argument(
        "--instructions",
        default=None,
        help="Path to a custom review instructions file (default: bundled instructions).",
    )
    parser.add_argument("--model", default="pro", help="Model name to pass to the backend.")
    parser.add_argument(
        "--output",
        default=None,
        help="Write the JSON review to this file (default: stdout).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exactly what would be sent to the agent; do not run it.",
    )
    return parser.parse_args(argv)


def run_review(argv: list[str]) -> int:
    """Entry point for ``python -m release_core.review``. Returns a process exit code."""
    args = _parse_args(argv)

    instructions = load_instructions(args.instructions)
    try:
        ctx = resolve_pr(args.pr, repo=args.repo)
    except ReviewError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    backend = get_backend(args.agent, model=args.model)
    prompt = build_prompt(instructions, ctx.diff, schema_inline=(args.agent == "agy"))

    if args.dry_run:
        command = backend.build_command(prompt, REVIEW_SCHEMA)
        print(_render_dry_run(args.agent, command))
        return 0

    try:
        backend.preflight()
    except BackendUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        result = backend.run(prompt, REVIEW_SCHEMA, cwd=ctx.workdir)
    except (proc.ProcError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rendered = json.dumps(result, indent=2)
    if args.output is not None:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
    else:
        print(rendered)
    return 0
