"""gh — the GitHub/git chokepoint. Mocked at the proc boundary; no network.

These tests cover the additive helpers landed for the remaining Bucket-A verbs
(`secret_set`/`secret_list` for install-release-{secrets,token}; the `body=`
arm of `rest()` for apply-ruleset's nested-payload PUT/POST). The existing
rest/graphql/git/issue_list surface is exercised elsewhere; here we assert the
exact `gh` argv each helper builds and how it parses the reply.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from release_core import gh
from release_core.gh import GhError


class _Recorder:
    """Stands in for proc.run: records the argv + stdin, replays a canned reply."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, *, input=None, check=False):  # noqa: A002 — mirrors proc.run
        self.calls.append((cmd, input))
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


@pytest.fixture
def gh_on_path(monkeypatch):
    """Pretend `gh` is installed so _gh doesn't short-circuit."""
    monkeypatch.setattr(gh.shutil, "which", lambda _: "/usr/bin/gh")


# ─── secret_set ──────────────────────────────────────────────────────────────


def test_secret_set_builds_argv_and_pipes_value(gh_on_path, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(gh.proc, "run", rec)
    gh.secret_set("CRATES_IO_KEY", "s3cr3t", repo="arthur-debert/dodot")
    cmd, stdin = rec.calls[0]
    assert cmd == ["gh", "secret", "set", "CRATES_IO_KEY", "-R", "arthur-debert/dodot"]
    assert stdin == "s3cr3t"


def test_secret_set_raises_on_failure(gh_on_path, monkeypatch):
    monkeypatch.setattr(gh.proc, "run", _Recorder(returncode=1, stderr="HTTP 403"))
    with pytest.raises(GhError):
        gh.secret_set("X", "v", repo="o/r")


# ─── secret_list ─────────────────────────────────────────────────────────────


def test_secret_list_returns_names_only(gh_on_path, monkeypatch):
    table = "RELEASE_TOKEN\tUpdated 2026-05-31\nCRATES_IO_KEY\tUpdated 2026-05-30\n"
    monkeypatch.setattr(gh.proc, "run", _Recorder(stdout=table))
    assert gh.secret_list("o/r") == ["RELEASE_TOKEN", "CRATES_IO_KEY"]


def test_secret_list_empty(gh_on_path, monkeypatch):
    monkeypatch.setattr(gh.proc, "run", _Recorder(stdout="\n"))
    assert gh.secret_list("o/r") == []


def test_secret_list_builds_argv(gh_on_path, monkeypatch):
    rec = _Recorder(stdout="A\tx\n")
    monkeypatch.setattr(gh.proc, "run", rec)
    gh.secret_list("o/r")
    assert rec.calls[0][0] == ["gh", "secret", "list", "-R", "o/r"]


# ─── rest(body=...) ──────────────────────────────────────────────────────────


def test_rest_body_pipes_json_via_input(gh_on_path, monkeypatch):
    rec = _Recorder(stdout='{"id": 99}')
    monkeypatch.setattr(gh.proc, "run", rec)
    payload = {"name": "main-branch-protection", "rules": [{"type": "x"}]}
    result = gh.rest("repos/o/r/rulesets", method="POST", body=payload)
    assert result == {"id": 99}
    cmd, stdin = rec.calls[0]
    assert cmd == ["gh", "api", "-X", "POST", "--input", "-", "repos/o/r/rulesets"]
    assert json.loads(stdin) == payload


def test_rest_body_put_returns_none_on_empty(gh_on_path, monkeypatch):
    monkeypatch.setattr(gh.proc, "run", _Recorder(stdout=""))
    assert gh.rest("repos/o/r/rulesets/5", method="PUT", body={"a": 1}) is None


def test_rest_fields_and_body_are_mutually_exclusive(gh_on_path, monkeypatch):
    monkeypatch.setattr(gh.proc, "run", _Recorder())
    with pytest.raises(GhError):
        gh.rest("x", fields={"a": "b"}, body={"c": "d"})


def test_rest_plain_get_still_works(gh_on_path, monkeypatch):
    rec = _Recorder(stdout='{"default_branch": "main"}')
    monkeypatch.setattr(gh.proc, "run", rec)
    assert gh.rest("repos/o/r") == {"default_branch": "main"}
    cmd, stdin = rec.calls[0]
    assert cmd == ["gh", "api", "repos/o/r"]
    assert stdin is None
