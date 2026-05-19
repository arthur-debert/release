"""orc CLI — orchestrator entry point."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import state
from .session import run_session


def _guard_billing() -> None:
    """Hard-fail if ANTHROPIC_API_KEY is set; subscription billing is the intent."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is set in the environment. Unset it to use "
            "subscription billing. Refusing to start to avoid surprise API charges."
        )


def cmd_run(args: argparse.Namespace) -> int:
    asyncio.run(run_session(args.repo, args.prompt, resume=False, verbose=args.verbose))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    asyncio.run(run_session(args.repo, args.prompt, resume=True, verbose=args.verbose))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Send an eval prompt to a fresh subordinate agent.

    Probe is for verification-by-proxy: a fresh agent in `<repo>` answers
    structured questions about its environment, reports whether the setup
    is coherent, runs lint/test commands as needed. Permission mode is
    `bypassPermissions` — assumes `<repo>` is a throwaway clone, not a
    user-owned working tree. The clone bounds the blast radius; the
    widened permissions let the agent actually run things rather than
    just describe them.

    See orchestrator/README.md for the canonical eval-prompt pattern.
    """
    asyncio.run(
        run_session(
            args.repo,
            args.prompt,
            resume=False,
            verbose=args.verbose,
            permission_mode="bypassPermissions",
        )
    )
    return 0


def cmd_sessions_list(_: argparse.Namespace) -> int:
    sessions = state.all_sessions()
    if not sessions:
        print("(no sessions)")
        return 0
    for repo, sid in sorted(sessions.items()):
        print(f"{sid}  {repo}")
    return 0


def cmd_sessions_clear(args: argparse.Namespace) -> int:
    state.clear(args.repo)
    print(f"cleared session for {Path(args.repo).expanduser().resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _guard_billing()

    parser = argparse.ArgumentParser(prog="orc", description="release orchestrator (spike)")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="print raw SDK messages to stderr",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="open a fresh session for a repo")
    p_run.add_argument("repo", help="path to consumer repo")
    p_run.add_argument("prompt", help="prompt to send")
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="continue the last session for a repo")
    p_resume.add_argument("repo")
    p_resume.add_argument("prompt")
    p_resume.set_defaults(func=cmd_resume)

    p_probe = sub.add_parser(
        "probe",
        help="evaluate a repo's environment via a fresh subordinate agent "
        "(throwaway clone — uses bypassPermissions)",
    )
    p_probe.add_argument("repo", help="path to throwaway clone of consumer repo")
    p_probe.add_argument("prompt", help="eval prompt — see orchestrator/README.md")
    p_probe.set_defaults(func=cmd_probe)

    p_sessions = sub.add_parser("sessions", help="manage stored sessions")
    sp = p_sessions.add_subparsers(dest="sub_cmd", required=True)

    p_list = sp.add_parser("list")
    p_list.set_defaults(func=cmd_sessions_list)

    p_clear = sp.add_parser("clear")
    p_clear.add_argument("repo")
    p_clear.set_defaults(func=cmd_sessions_clear)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
