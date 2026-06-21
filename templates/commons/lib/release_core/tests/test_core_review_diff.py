"""Tests for PR resolution (``release_core.review.diff``).

No test hits the network or a real repo: ``gh.pr_view`` is monkeypatched to a
fixed JSON blob and every ``git`` invocation is intercepted at the ``proc.run``
boundary by a small fake that returns canned stdout per subcommand. The proof is
that ``resolve_pr`` assembles a correct :class:`PRContext` (base/head refs, the
three-dot diff, the changed-file list) and raises an actionable
:class:`ReviewError` on the failure paths.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from release_core import gh
from release_core.review import diff as diffmod
from release_core.review.diff import PRContext, ReviewError, resolve_pr

PR_META = {
    "number": 365,
    "headRefName": "feature-branch",
    "headRefOid": "headsha123456",
    "baseRefName": "main",
}

DIFF_TEXT = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
NAMES_TEXT = "a.py\nb/c.py\n"


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=""
    )


class _FakeGit:
    """Intercepts ``proc.run`` for git, returning canned stdout per subcommand.

    ``present`` is the set of object names that ``cat-file -e`` treats as
    reachable (so we can drive the 'head already local' vs 'must fetch' paths).
    """

    def __init__(self, *, present: set[str]) -> None:
        self.present = present
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        # cmd is ["git", "-C", workdir, <sub>, ...]
        sub = cmd[3:]
        if sub[:1] == ["rev-parse"] and "--is-inside-work-tree" in sub:
            return _completed("true\n")
        if sub[:1] == ["rev-parse"]:
            return _completed("headsha123456\n")
        if sub[:1] == ["cat-file"]:
            target = sub[-1].split("^")[0]
            return _completed(returncode=0 if target in self.present else 1)
        if sub[:1] == ["fetch"]:
            return _completed()
        if sub[:1] == ["merge-base"]:
            return _completed("mergebasesha\n")
        if sub[:1] == ["diff"]:
            if "--name-only" in sub:
                return _completed(NAMES_TEXT)
            return _completed(DIFF_TEXT)
        return _completed()


@pytest.fixture
def fake_pr_view(monkeypatch):
    monkeypatch.setattr(gh, "pr_view", lambda *_a, **_k: json.dumps(PR_META))


def test_resolve_pr_head_already_local(monkeypatch, fake_pr_view):
    """Happy path: head sha + origin/<base> already local → diff + names parsed."""
    fake = _FakeGit(present={"headsha123456", "origin/main"})
    monkeypatch.setattr(diffmod.proc, "run", fake)

    ctx = resolve_pr(365, workdir="/work")

    assert isinstance(ctx, PRContext)
    assert ctx.number == 365
    assert ctx.base_ref == "main"
    assert ctx.head_sha == "headsha123456"
    assert ctx.base_sha == "mergebasesha"
    assert ctx.diff == DIFF_TEXT
    assert ctx.changed_files == ["a.py", "b/c.py"]
    assert ctx.workdir == "/work"
    # No PR-head fetch needed when the sha is already present.
    fetched = [c for c in fake.calls if c[3:4] == ["fetch"]]
    assert not any("pull/365/head" in " ".join(c) for c in fetched)


def test_resolve_pr_fetches_head_when_absent(monkeypatch, fake_pr_view):
    """When the head sha isn't local, resolve_pr fetches pull/<n>/head (no switch)."""
    # cat-file reports the head present only AFTER the fetch; emulate by flipping.
    state = {"present": {"origin/main"}}

    fake = _FakeGit(present=state["present"])

    real_call = fake.__call__

    def _call(cmd, **kwargs):
        sub = cmd[3:]
        if sub[:1] == ["fetch"] and "pull/365/head" in sub:
            fake.present.add("headsha123456")
        return real_call(cmd, **kwargs)

    monkeypatch.setattr(diffmod.proc, "run", _call)

    ctx = resolve_pr(365, workdir="/work")
    assert ctx.head_sha == "headsha123456"
    assert any("pull/365/head" in " ".join(c) for c in fake.calls)
    # Never a checkout/switch — we must not mutate the working tree.
    assert not any(c[3:4] in (["checkout"], ["switch"]) for c in fake.calls)


def test_resolve_pr_not_a_git_checkout(monkeypatch, fake_pr_view):
    def _not_repo(cmd, **kwargs):
        if cmd[3:4] == ["rev-parse"] and "--is-inside-work-tree" in cmd:
            return _completed("", returncode=128)
        return _completed()

    monkeypatch.setattr(diffmod.proc, "run", _not_repo)
    with pytest.raises(ReviewError, match="not a git checkout"):
        resolve_pr(365, workdir="/not/a/repo")


def test_resolve_pr_unresolvable_pr(monkeypatch):
    def _boom(*_a, **_k):
        raise gh.GhError("no pull requests found")

    monkeypatch.setattr(gh, "pr_view", _boom)
    monkeypatch.setattr(diffmod.proc, "run", _FakeGit(present={"origin/main"}))
    with pytest.raises(ReviewError, match="Could not resolve PR"):
        resolve_pr(999, workdir="/work")


def test_resolve_pr_default_workdir_is_cwd(monkeypatch, fake_pr_view, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diffmod.proc, "run", _FakeGit(present={"headsha123456", "origin/main"}))
    ctx = resolve_pr(365)
    assert ctx.workdir == str(tmp_path)
