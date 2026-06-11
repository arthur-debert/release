"""orc CLI — orchestrator entry point."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from . import state, watch
from .boot import BootError, boot_clone
from .session import run_session


def _guard_billing() -> None:
    """Hard-fail if ANTHROPIC_API_KEY is set; subscription billing is the intent.

    Called by the SDK-backed commands (run/resume/probe), not by the
    mechanical commands (sessions), which don't touch the SDK.
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

    Before the agent session launches, the clone is BOOTED via its own
    `bin/setup-dev-env.sh` (the real SessionStart chain — SDK sessions
    never fire the hook, release#578), with a fail-loud boot-assert and a
    boot report on stderr. A boot failure aborts: an unbooted probe is
    invalid by design. The prompt stays hint-free; only the boot is added.

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
    try:
        boot_clone(args.repo)
    except BootError as e:
        sys.exit(f"orc probe: boot failed — probe invalidated: {e}")
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


class _WatchSink(watch.Sink):
    """Concrete side effects for `orc watch`: terminal log, desktop pings,
    draft→ready flip, and (in --auto) a fresh fixer agent in an isolated clone.
    """

    def __init__(self, repo_path: str, *, auto: bool) -> None:
        self.repo_path = repo_path
        self.auto = auto

    def log(self, pr, prev, status) -> None:
        print(f"#{pr}: {prev or '-'} → {status.state.value}   {status.next_action}")

    def notify(self, pr, status) -> None:
        self._desktop(f"PR #{pr}: {status.state.value}", status.next_action)

    def page(self, pr, status, *, reason) -> None:
        self._desktop(f"PR #{pr} needs you — {reason}", status.next_action)

    def flip_ready(self, pr, status) -> None:
        # Idempotent enough: `gh pr ready` on a non-draft PR just errors out.
        subprocess.run(
            ["gh", "pr", "ready", str(pr)],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        self._desktop(f"PR #{pr} is READY", "reviewed + green + mergeable — your merge")

    def spawn_fixer(self, pr, status) -> None:
        print(f"#{pr}: spawning auto-fix agent…")
        _spawn_fixer(pr, self.repo_path)

    def _desktop(self, title: str, body: str) -> None:
        print(f">>> {title} — {body}")
        if sys.platform == "darwin":
            # Escape quotes in BOTH fields, else a breaker name / reason with a
            # quote breaks the osascript string and the notification silently fails.
            safe_body = body.replace('"', "'")
            safe_title = title.replace('"', "'")
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{safe_body}" with title "{safe_title}"',
                ],
                capture_output=True,
                check=False,
            )

    def error(self, pr, exc) -> None:
        print(f"#{pr}: poll error ({exc}); skipping this pass.", file=sys.stderr)


def _spawn_fixer(pr: int, repo_path: str) -> None:
    """Run a fresh auto-fix agent in an isolated clone of the PR's branch.

    A clone (not the user's working tree) bounds the blast radius of the
    bypassPermissions agent; it pushes fixups to the PR branch on origin, then
    the clone is removed. The session is NOT persisted (fresh every time — #338).
    """
    import shutil
    import tempfile

    # One `gh pr view` gets everything: the branch, the HEAD repo's clone URL
    # (the base URL is wrong for fork PRs — the branch doesn't exist there), and
    # whether this is a cross-repo (fork) PR at all.
    try:
        raw = subprocess.run(
            ["gh", "pr", "view", str(pr), "--json", "headRefName,headRepository,isCrossRepository"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        data = json.loads(raw)
        if data.get("isCrossRepository"):
            # Auto-fix can't push to a fork we don't own — fail fast, leave it.
            print(
                f"#{pr}: auto-fix skips fork PR (can't push to the fork); leaving for a human.",
                file=sys.stderr,
            )
            return
        branch = data["headRefName"]
        url = data["headRepository"]["url"]
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError) as e:
        detail = getattr(e, "stderr", None) or getattr(e, "stdout", None) or e
        print(f"#{pr}: could not resolve PR branch to fix ({detail}); skipping.", file=sys.stderr)
        return

    tmp = tempfile.mkdtemp(prefix=f"orc-fix-{pr}-")
    clone = str(Path(tmp) / "repo")
    try:
        subprocess.run(
            ["git", "clone", "--branch", branch, url, clone],
            check=True,
            capture_output=True,
            text=True,
        )
        asyncio.run(
            run_session(
                clone,
                watch.build_fixer_prompt(pr),
                resume=False,
                permission_mode="bypassPermissions",
                persist_session=False,
            )
        )
    except subprocess.CalledProcessError as e:
        print(f"#{pr}: auto-fix clone/setup failed ({e.stderr or e}); skipping.", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_watch(args: argparse.Namespace) -> int:
    """Poll PRs and act on lifecycle transitions (detached transport, #338)."""
    if args.auto:
        _guard_billing()  # --auto spawns SDK agents
    repo_path = str(Path(args.repo).expanduser().resolve())
    os.chdir(repo_path)  # release_core.prstate.fetch shells `gh` in the cwd
    prs = args.prs  # argparse already parsed these as ints
    mode = "AUTO-FIX" if args.auto else "notify-only"
    print(
        f"watching {len(prs)} PR(s) in {repo_path} every {args.interval:g}s [{mode}]. "
        "Ctrl-C to stop."
    )
    if args.auto:
        print("  --auto: fresh agents run with bypassPermissions in throwaway clones.")
    sink = _WatchSink(repo_path, auto=args.auto)
    try:
        watch.run(prs, sink=sink, auto=args.auto, interval=args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # No top-level _guard_billing(): mechanical commands (sessions) don't
    # touch the SDK and shouldn't be blocked by
    # ANTHROPIC_API_KEY presence. Each SDK-backed cmd (run/resume/probe)
    # calls _guard_billing() itself.
    parser = argparse.ArgumentParser(prog="orc", description="release orchestrator (spike)")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
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
        "(throwaway clone — uses bypassPermissions; boots the clone via its "
        "own bin/setup-dev-env.sh first, fail-loud)",
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

    p_watch = sub.add_parser(
        "watch",
        help="poll PRs and act on lifecycle transitions (detached transport)",
    )
    p_watch.add_argument("prs", nargs="+", type=int, help="PR numbers to watch (in --repo)")
    p_watch.add_argument("--repo", default=".", help="repo working tree (default: current dir)")
    p_watch.add_argument(
        "--interval", type=float, default=45.0, help="poll interval seconds (default 45)"
    )
    p_watch.add_argument(
        "--auto",
        action="store_true",
        help="full auto-fix: on ADDRESSING/BLOCKED spawn a fresh agent "
        "(bypassPermissions, in a throwaway clone). Without it, notify-only.",
    )
    p_watch.set_defaults(func=cmd_watch)

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
