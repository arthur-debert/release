"""release-core admin repos migrate — seed the fleet onto the pull model.

The pull-model successor to the removed ``orc propagate``. For each managed repo
it: clones hermetically, runs ``release-core init`` (the full managed-tree
build + auto-commit of *only* the managed paths — force-added past a
consumer ``.gitignore``), and, when that produced a managed commit, pushes a
branch and opens one managed-sync PR. The content is the consumer's
self-built managed tree (what it would pull at SessionStart), NOT a
release-sync push — so this is just a one-time seed for repos still on a
pre-pull tree; after it merges, each repo self-updates natively.

Usage::

  release-core admin repos migrate [--only owner/name,...] [--dry-run]
                                   [--root DIR] [--branch NAME]

  --only owner/name,...   restrict to a comma-separated subset (default: all).
  --dry-run               clone + init + report, but never push or open a PR.
  --root DIR              where to clone (default: /tmp/release-fleet-migrate-$USER).
  --branch NAME           branch to create in each consumer (default:
                          chore/pull-model-migration).

Exit: 0 all-ok; 1 if any repo errored.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .. import gh
from . import managed_repos

_BRANCH = "chore/pull-model-migration"
_TITLE = "chore(release): migrate to the pull model (managed sync from release_core)"
_BODY = (
    "Pull-model migration: the managed tree self-built from the published "
    "`release_core` wheel (a bare `release-core init`) and auto-committed — no "
    "`orc propagate` (removed). After this merges the repo carries the current "
    "managed tree + resolver and self-updates at every SessionStart.\n\n"
    "Managed/generated content only (`.release/**`, synced `.github/**`, "
    "`.claude/skills`, configs) — excluded from review as a managed change per the "
    "fleet rule; the threads on synced files are upstream `arthur-debert/release` "
    "concerns, not blockers for this consumer PR.\n\n"
    "\U0001f916 Generated with Claude Code"
)


def _usage_error(msg: str) -> int:
    print(f"release-core admin repos migrate: {msg}", file=sys.stderr)
    return 64


def _base_branch(dest: str) -> str:
    try:
        ref = gh.git(["-C", dest, "symbolic-ref", "refs/remotes/origin/HEAD"])
        return ref.rsplit("/", 1)[-1]
    except Exception:
        return "main"


_INIT_SNIPPET = "from release_core.verbs import init; raise SystemExit(init.main([]))"


def _run_init(dest: str) -> bool:
    """Run a bare `release-core init` in ``dest`` via an isolated subprocess.

    RELEASE_HOME is STRIPPED so init builds from the wheel bundle — exactly
    what the consumer pulls at SessionStart — not a maintainer's local release
    checkout. (Critically, the bundle path also REMOVES retired tree like
    `.release/lib/release_core/`; a RELEASE_HOME git-source run rebuilds it
    and the migration PR falls out of sync with the real pull.) Best-effort: a non-zero exit
    surfaces via the caller's post-init ahead-count, not here."""
    env = {k: v for k, v in os.environ.items() if k != "RELEASE_HOME"}
    proc = subprocess.run(
        [sys.executable, "-c", _INIT_SNIPPET],
        cwd=dest,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main(argv: list[str]) -> int:  # noqa: C901 — flat per-repo dispatch
    only: list[str] = []
    dry_run = False
    branch = _BRANCH
    user = os.environ.get("USER", "user")
    root = f"/tmp/release-fleet-migrate-{user}"  # noqa: S108 — per-user, matches verify's pattern

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__)
            return 0
        elif arg == "--only":
            if i + 1 >= len(argv):
                return _usage_error("--only needs a value")
            only = [s for s in argv[i + 1].split(",") if s]
            i += 2
        elif arg == "--dry-run":
            dry_run = True
            i += 1
        elif arg == "--root":
            if i + 1 >= len(argv):
                return _usage_error("--root needs a value")
            root = argv[i + 1]
            i += 2
        elif arg == "--branch":
            if i + 1 >= len(argv):
                return _usage_error("--branch needs a value")
            branch = argv[i + 1]
            i += 2
        else:
            return _usage_error(f"unknown arg: {arg}")

    pairs = managed_repos._pairs(managed_repos._manifest_path(), only)
    if not pairs:
        print("release-core admin repos migrate: no repos matched", file=sys.stderr)
        return 1

    os.makedirs(root, exist_ok=True)
    results: list[tuple[str, str]] = []
    errored = False

    for repo, _path in pairs:
        dest = os.path.join(root, repo.replace("/", "__"))
        shutil.rmtree(dest, ignore_errors=True)
        if gh.repo_clone(repo, dest).returncode != 0:
            results.append((repo, "ERROR clone failed"))
            errored = True
            continue
        base = _base_branch(dest)
        try:
            gh.git(["-C", dest, "checkout", "-b", branch])
        except Exception as exc:
            results.append((repo, f"ERROR branch: {exc}"))
            errored = True
            continue

        _run_init(dest)
        try:
            ahead = int(gh.git(["-C", dest, "rev-list", "--count", f"origin/{base}..HEAD"]))
        except Exception:
            ahead = -1

        if ahead == 0:
            results.append((repo, "already current (no managed changes)"))
            continue
        if ahead != 1:
            # init's auto-commit makes exactly ONE managed commit; anything else
            # (init failed, or a stray commit) is held for a human, never pushed.
            results.append((repo, f"HOLD: unexpected commit count ({ahead}) — not pushed"))
            errored = True
            continue

        files = len(gh.git(["-C", dest, "show", "--name-only", "--format=", "HEAD"]).split())
        if dry_run:
            results.append((repo, f"dry-run: would open PR ({files} managed files)"))
            continue

        try:
            # force-with-lease: the migration branch is tool-generated, and a
            # re-run after a partial failure re-creates it with a fresh commit SHA
            # (same tree) — a plain push would be rejected non-fast-forward.
            gh.git(["-C", dest, "push", "-u", "--force-with-lease", "origin", branch])
        except Exception as exc:
            results.append((repo, f"ERROR push: {exc}"))
            errored = True
            continue
        # Run `gh pr create` INSIDE the clone so it infers the repo (origin) and
        # head (the checked-out, pushed branch) correctly — gh's repo/head
        # detection is cwd-based, so creating from the parent process dir targets
        # the wrong repo/branch.
        cp = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base,
                "--head",
                branch,
                "--title",
                _TITLE,
                "--body",
                _BODY,
            ],
            cwd=dest,
            capture_output=True,
            text=True,
        )
        if cp.returncode != 0:
            results.append((repo, f"ERROR pr-create: {cp.stderr.strip() or cp.stdout.strip()}"))
            errored = True
            continue
        url = next(
            (tok for tok in cp.stdout.split() if tok.startswith("https://")),
            cp.stdout.strip(),
        )
        results.append((repo, f"PR {url} ({files} files)"))

    print(f"\n=== migrate: {len(results)} repo(s){' (dry-run)' if dry_run else ''} ===")
    for repo, status in results:
        print(f"  {repo:<32} {status}")
    return 1 if errored else 0
