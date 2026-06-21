"""diff — resolve a PR to the diff (and changed files) the review runs over.

This replaces the Phase-1.2 single-base stepping-stone (a bare ``git diff
<base>...HEAD`` in cwd) with real PR resolution: given a PR number, ask GitHub
for the PR's base/head refs, make the base + head available locally (FETCH only —
never a branch switch, so the user's working tree is untouched), and compute the
three-dot diff and changed-file list the agent reviews.

The CHECKOUT model: the agent backend reads files from ``PRContext.workdir`` so
it can open the surrounding source for context. When the review runs in the
consumer's own checkout of the PR (``workdir`` defaults to cwd) the head is
typically already at ``HEAD``; otherwise we fetch the PR head as an object and
diff against it. We never switch branches — if the head isn't the current
working tree, the agent can still read the changed content via the diff and via
``git show <sha>:<path>``, but a full file-tree read of the head requires that
the head actually be checked out (documented limitation, not a branch switch).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from .. import gh, proc


class ReviewError(RuntimeError):
    """A review precondition failed (not a git checkout, PR unresolvable, …).

    Carries an actionable message — the facade prints it and exits nonzero.
    """


@dataclass
class PRContext:
    """Everything the review needs about one PR, resolved and ready to diff."""

    number: int
    repo: str | None
    head_sha: str
    base_ref: str
    base_sha: str
    diff: str
    changed_files: list[str] = field(default_factory=list)
    workdir: str = "."


def _is_git_checkout(workdir: str) -> bool:
    result = proc.run(
        ["git", "-C", workdir, "rev-parse", "--is-inside-work-tree"],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git(workdir: str, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return proc.run(["git", "-C", workdir, *args], check=check)


def _sha_present(workdir: str, sha: str) -> bool:
    """True if ``sha`` is a commit object reachable in ``workdir`` (no fetch)."""
    if not sha:
        return False
    result = proc.run(
        ["git", "-C", workdir, "cat-file", "-e", f"{sha}^{{commit}}"],
        check=False,
    )
    return result.returncode == 0


def _pr_meta(pr: int, repo: str | None) -> dict:
    """``gh pr view <pr> [--repo …] --json …`` → parsed metadata dict.

    Raises :class:`ReviewError` if gh can't resolve the PR.
    """
    try:
        raw = gh.pr_view(
            str(pr),
            repo=repo,
            json_fields=["number", "headRefName", "headRefOid", "baseRefName"],
        )
    except gh.GhError as exc:
        raise ReviewError(
            f"Could not resolve PR #{pr}"
            + (f" in {repo}" if repo else "")
            + f" via `gh pr view`: {exc}"
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"Unparseable `gh pr view` output for PR #{pr}: {exc}") from exc


def resolve_pr(
    pr: int,
    *,
    repo: str | None = None,
    workdir: str | None = None,
) -> PRContext:
    """Resolve PR ``pr`` to a :class:`PRContext` (diff + changed files + workdir).

    * ``repo`` (``OWNER/NAME``) targets a specific repo; ``None`` lets ``gh``
      infer the repo from ``workdir``'s remote.
    * ``workdir`` is the checkout the agent reads files from; defaults to the
      current directory (the consumer reviewing their own PR).

    Fetches (never switches branches) the base ref and, if absent, the PR head,
    then computes ``base_sha = merge-base(origin/<base_ref>, <head>)`` and the
    three-dot diff. Raises :class:`ReviewError` if ``workdir`` is not a git
    checkout or the PR can't be resolved.
    """
    workdir = workdir or os.getcwd()
    if not _is_git_checkout(workdir):
        raise ReviewError(
            f"{workdir!r} is not a git checkout — `release-core review` resolves a "
            f"PR by diffing inside a clone of the repository. cd into the repo (or "
            f"pass a checkout) and re-run."
        )

    # Normalize any explicit repo slug to its canonical owner/name. An aliased
    # slug (e.g. a transferred/renamed repo) 307-redirects on GET but NOT on
    # POST, so posting a review to it hard-fails with HTTP 307. Normalizing here
    # — at the boundary where the external slug enters — keeps ALL downstream
    # consumers (generation AND posting) on the canonical slug. When repo is
    # None, gh infers it from the checkout, which is already canonical.
    if repo is not None:
        try:
            repo = gh.repo_canonical(repo)
        except gh.GhError as exc:
            raise ReviewError(
                f"Could not resolve repo {repo!r} to its canonical owner/name via "
                f"`gh repo view`: {exc}"
            ) from exc

    meta = _pr_meta(pr, repo)
    base_ref = meta.get("baseRefName") or "main"
    head_sha = meta.get("headRefOid") or ""
    head_ref = meta.get("headRefName") or ""

    # Make the base available (fetch only — never checkout-switch).
    _git(workdir, ["fetch", "--quiet", "origin", base_ref], check=False)

    # Make the head available if it isn't already a local object.
    if head_sha and not _sha_present(workdir, head_sha):
        # Try the PR head ref namespace first (works without the branch being
        # local), then fall back to fetching the named head branch.
        _git(workdir, ["fetch", "--quiet", "origin", f"pull/{pr}/head"], check=False)
        if not _sha_present(workdir, head_sha) and head_ref:
            _git(workdir, ["fetch", "--quiet", "origin", head_ref], check=False)

    # The head endpoint of the diff: the resolved sha if we have it locally,
    # else the just-fetched remote-tracking ref / FETCH_HEAD, else HEAD.
    if head_sha and _sha_present(workdir, head_sha):
        head_point = head_sha
    elif _sha_present(workdir, "FETCH_HEAD"):
        head_point = "FETCH_HEAD"
    else:
        head_point = "HEAD"
        head_sha = head_sha or _git(workdir, ["rev-parse", "HEAD"]).stdout.strip()

    base_point = f"origin/{base_ref}"
    if not _sha_present(workdir, base_point):
        base_point = base_ref  # fall back to a local ref of the same name

    merge_base = _git(workdir, ["merge-base", base_point, head_point], check=False)
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        base_sha = merge_base.stdout.strip()
    else:
        # No common ancestor reachable (e.g. base not fetched) — diff against the
        # base tip directly so we still produce a usable review.
        base_sha = base_point

    diff = _git(workdir, ["diff", f"{base_sha}...{head_point}"]).stdout
    names = _git(workdir, ["diff", "--name-only", f"{base_sha}...{head_point}"]).stdout
    changed_files = [line for line in names.splitlines() if line.strip()]

    return PRContext(
        number=pr,
        repo=repo,
        head_sha=head_sha,
        base_ref=base_ref,
        base_sha=base_sha,
        diff=diff,
        changed_files=changed_files,
        workdir=workdir,
    )
