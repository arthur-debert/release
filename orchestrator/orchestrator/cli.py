"""orc CLI — orchestrator entry point."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import state
from .session import run_session


_DEFAULT_PROPAGATE_BRANCH = "chore/release-sync-update"


def _guard_billing() -> None:
    """Hard-fail if ANTHROPIC_API_KEY is set; subscription billing is the intent.

    Called by the SDK-backed commands (run/resume/probe), not by the
    mechanical commands (propagate, sessions). `propagate` shells out to
    git + gh only — no LLM calls, no billing risk — and shouldn't be
    blocked by API-key presence.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is set in the environment. Unset it to use "
            "subscription billing. Refusing to start to avoid surprise API charges."
        )


def cmd_run(args: argparse.Namespace) -> int:
    _guard_billing()
    asyncio.run(run_session(args.repo, args.prompt, resume=False, verbose=args.verbose))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    _guard_billing()
    asyncio.run(run_session(args.repo, args.prompt, resume=True, verbose=args.verbose))
    return 0


def cmd_propagate(args: argparse.Namespace) -> int:
    """Run release-sync across multiple consumer repos and open PRs.

    Each repo is treated independently — a failure in one does not
    abort the rest. The summary at the end reports per-repo outcomes
    (ok / no-changes / dry-run / error). Exit 1 if any repo errored.
    """
    import subprocess
    from .propagate import PropagateResult, propagate_many, render_summary

    paths = [Path(p).expanduser().resolve() for p in args.repos]
    release_home = Path(args.release_home).expanduser().resolve()

    # Resolve the ref to a short SHA up-front and append to the branch
    # name (unless the user supplied --branch explicitly). Makes the
    # default branch unique per ref, so re-running propagate against the
    # same ref won't collide with a prior partial run's leftover branch.
    branch = args.branch
    if branch == _DEFAULT_PROPAGATE_BRANCH:
        try:
            short_sha = subprocess.run(
                ["git", "-C", str(release_home), "rev-parse", "--short", args.ref],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            branch = f"{branch}-{short_sha}"
        except subprocess.CalledProcessError as e:
            print(
                f"orc propagate: could not resolve --ref '{args.ref}' in "
                f"{release_home}: {e.stderr or e}",
                file=sys.stderr,
            )
            return 1

    print(f"propagating release@{args.ref} → {len(paths)} repo(s)")
    print(f"branch in each consumer: {branch}")
    if args.dry_run:
        print("DRY RUN — no push, no PR")
    print()

    results: list[PropagateResult] = propagate_many(
        paths,
        release_home=release_home,
        ref=args.ref,
        branch=branch,
        pr_title=args.pr_title,
        pr_body=args.pr_body,
        commit_msg=args.commit_msg,
        base_branch=args.base_branch,
        dry_run=args.dry_run,
    )
    print(render_summary(results))
    print()
    counts = {"ok": 0, "no-changes": 0, "dry-run": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(
        f"summary: {counts['ok']} ok, {counts['no-changes']} no-changes, "
        f"{counts['dry-run']} dry-run, {counts['error']} error"
    )
    return 1 if counts["error"] else 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Send an eval prompt to a fresh subordinate agent.

    Probe is for verification-by-proxy: a fresh agent in `<repo>` answers
    structured questions about its environment, reports whether the setup
    is coherent, runs lint/test commands as needed. Permission mode is
    `bypassPermissions` — assumes `<repo>` is a throwaway clone, not a
    user-owned working tree. The clone bounds the blast radius; the
    widened permissions let the agent actually run things rather than
    just describe them.

    Probe requires `--yes` as an explicit acknowledgement, because
    accidentally pointing `bypassPermissions` at your real working tree
    would let the subordinate agent run anything. The probe session is
    NOT persisted (so a later `orc resume` won't pick it up).

    See orchestrator/README.md for the canonical eval-prompt pattern.
    """
    if not args.yes:
        print(
            "orc probe runs with bypassPermissions — the subordinate agent\n"
            "can execute any command in <repo>. Pass --yes to confirm <repo>\n"
            "is a throwaway clone (e.g. under /tmp), not a user-owned working\n"
            "tree.",
            file=sys.stderr,
        )
        return 2
    _guard_billing()
    asyncio.run(
        run_session(
            args.repo,
            args.prompt,
            resume=False,
            verbose=args.verbose,
            permission_mode="bypassPermissions",
            persist_session=False,
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
    # No top-level _guard_billing(): mechanical commands (propagate,
    # sessions) don't touch the SDK and shouldn't be blocked by
    # ANTHROPIC_API_KEY presence. Each SDK-backed cmd (run/resume/probe)
    # calls _guard_billing() itself.
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

    p_propagate = sub.add_parser(
        "propagate",
        help="run release-sync across multiple consumer repos and open PRs",
    )
    p_propagate.add_argument(
        "repos",
        nargs="+",
        help="paths to consumer repo working trees (must be clean, on base branch)",
    )
    p_propagate.add_argument(
        "--ref", default="main",
        help="release-sync ref (default: main; use take-iii while it's open)",
    )
    p_propagate.add_argument(
        "--branch", default=_DEFAULT_PROPAGATE_BRANCH,
        help=(
            "branch name to create in each consumer (default: "
            f"{_DEFAULT_PROPAGATE_BRANCH}-<short-sha>, where short-sha "
            "is the resolved release ref — keeps the default unique per "
            "ref so re-runs don't collide with a prior partial run's "
            "leftover branch)"
        ),
    )
    p_propagate.add_argument(
        "--base-branch", default="main",
        help="base branch in each consumer (default: main)",
    )
    p_propagate.add_argument(
        "--release-home", default=str(Path.home() / "h" / "release"),
        help="path to local release/ clone (default: ~/h/release)",
    )
    p_propagate.add_argument(
        "--pr-title", default="chore: release-sync update",
        help="PR title for each consumer's PR",
    )
    p_propagate.add_argument(
        "--pr-body",
        default=(
            "Routine release-sync update from arthur-debert/release.\n\n"
            "Refs arthur-debert/release#103."
        ),
        help="PR body for each consumer's PR",
    )
    p_propagate.add_argument(
        "--commit-msg",
        default=(
            "chore: release-sync update from arthur-debert/release\n\n"
            "Routine sync. Refs arthur-debert/release#103."
        ),
        help="commit message in each consumer",
    )
    p_propagate.add_argument(
        "--dry-run", action="store_true",
        help="don't push or open PR; report what would happen",
    )
    p_propagate.set_defaults(func=cmd_propagate)

    p_probe = sub.add_parser(
        "probe",
        help="evaluate a repo's environment via a fresh subordinate agent "
        "(throwaway clone — uses bypassPermissions)",
    )
    p_probe.add_argument("repo", help="path to throwaway clone of consumer repo")
    p_probe.add_argument("prompt", help="eval prompt — see orchestrator/README.md")
    p_probe.add_argument(
        "--yes",
        action="store_true",
        help="confirm <repo> is a throwaway clone (required — bypassPermissions "
        "lets the subordinate agent run anything in it)",
    )
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
