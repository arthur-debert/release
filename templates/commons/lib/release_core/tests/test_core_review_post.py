"""Tests for posting a review JSON to a PR (``review post`` / ``review.post``).

No test touches the network: ``gh.rest`` is monkeypatched (to capture args or to
raise if it must NOT be called). The proofs here are the payload-shape mapping
(status→event, override wins, Agent prefix, in-diff inline vs out-of-diff fold)
and that dry-run never calls gh.
"""

from __future__ import annotations

import pytest
from release_core import gh
from release_core.review import post
from release_core.review.diff import PRContext

# A small unified diff: file a.py gains lines, file b.py is context+add+remove.
SAMPLE_DIFF = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,4 @@
 import os
+import sys
+import json
 print("hi")
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -10,3 +10,3 @@ def f():
 ctx_line
-removed_line
+added_line
 trailing
"""


def _ctx(*, repo: str | None = "owner/name", diff: str = SAMPLE_DIFF) -> PRContext:
    return PRContext(
        number=42,
        repo=repo,
        head_sha="headsha123",
        base_ref="main",
        base_sha="basesha000",
        diff=diff,
        changed_files=["a.py", "b.py"],
        workdir="/tmp/checkout",
    )


# --- commentable_lines --------------------------------------------------------


def test_commentable_lines_parses_added_and_context_excludes_removed():
    lines = post.commentable_lines(SAMPLE_DIFF)
    # a.py hunk starts at new-line 1: "import os"(1), "import sys"(2),
    # "import json"(3), 'print("hi")'(4) — all RIGHT-side present.
    assert lines["a.py"] == {1, 2, 3, 4}
    # b.py hunk starts at new-line 10: ctx_line(10), added_line(11),
    # trailing(12). The removed line consumes NO right-side number.
    assert lines["b.py"] == {10, 11, 12}


def test_commentable_lines_empty_diff():
    assert post.commentable_lines("") == {}


# --- build_review_payload -----------------------------------------------------


def _review(status: str = "COMMENT", comments: list[dict] | None = None) -> dict:
    return {
        "summary": {"status": status, "overall_feedback": "Overall this looks fine."},
        "comments": comments or [],
    }


def test_status_maps_to_event():
    for status, event in (
        ("APPROVED", "APPROVE"),
        ("REQUEST_CHANGES", "REQUEST_CHANGES"),
        ("COMMENT", "COMMENT"),
    ):
        payload = post.build_review_payload(_review(status), _ctx(), agent_name="codex")
        assert payload["event"] == event


def test_event_override_wins():
    # Self-review safety: even an APPROVED review posts as COMMENT when overridden.
    payload = post.build_review_payload(
        _review("APPROVED"), _ctx(), agent_name="codex", event="COMMENT"
    )
    assert payload["event"] == "COMMENT"


def test_commit_id_pins_to_head_sha():
    payload = post.build_review_payload(_review(), _ctx(), agent_name="codex")
    assert payload["commit_id"] == "headsha123"


def test_body_carries_agent_prefix_and_feedback():
    payload = post.build_review_payload(_review(), _ctx(), agent_name="codex")
    assert payload["body"].startswith("Agent: codex")
    assert "Overall this looks fine." in payload["body"]


def test_in_diff_finding_becomes_inline_comment():
    review = _review(
        comments=[
            {
                "file": "a.py",
                "line": 2,
                "text": "avoid importing sys here",
                "severity": "WARNING",
                "code_snippet": "import sys",
            }
        ]
    )
    payload = post.build_review_payload(review, _ctx(), agent_name="codex")
    assert len(payload["comments"]) == 1
    comment = payload["comments"][0]
    assert comment["path"] == "a.py"
    assert comment["line"] == 2
    assert comment["side"] == "RIGHT"
    assert comment["body"].startswith("Agent: codex [WARNING]")
    assert "avoid importing sys here" in comment["body"]
    assert "```\nimport sys\n```" in comment["body"]


def test_out_of_diff_finding_folds_into_body_not_comments():
    review = _review(
        comments=[
            {
                "file": "a.py",
                "line": 999,  # not a commentable line
                "text": "unanchored note",
                "severity": "INFO",
                "code_snippet": "",
            }
        ]
    )
    payload = post.build_review_payload(review, _ctx(), agent_name="codex")
    # No inline comment emitted at all.
    assert "comments" not in payload
    assert "Findings not anchored to changed lines" in payload["body"]
    assert "a.py:999" in payload["body"]
    assert "unanchored note" in payload["body"]


def test_mixed_findings_split_correctly():
    review = _review(
        comments=[
            {
                "file": "a.py",
                "line": 3,
                "text": "in diff",
                "severity": "ERROR",
                "code_snippet": "",
            },
            {
                "file": "b.py",
                "line": 500,
                "text": "out of diff",
                "severity": "INFO",
                "code_snippet": "",
            },
        ]
    )
    payload = post.build_review_payload(review, _ctx(), agent_name="agy")
    assert len(payload["comments"]) == 1
    assert payload["comments"][0]["path"] == "a.py"
    assert "b.py:500" in payload["body"]


# --- post_review --------------------------------------------------------------


def test_post_review_dry_run_does_not_call_gh(monkeypatch, capsys):
    def _boom(*_a, **_k):
        raise AssertionError("gh.rest must not be called in dry-run")

    monkeypatch.setattr(gh, "rest", _boom, raising=True)

    payload = post.post_review(_review(), _ctx(), agent_name="codex", dry_run=True)
    assert payload["commit_id"] == "headsha123"
    # The payload was printed for eyeballing.
    out = capsys.readouterr().out
    assert "headsha123" in out
    assert '"event": "COMMENT"' in out


def test_post_review_posts_once_with_right_args(monkeypatch):
    captured = {}

    def _fake_rest(path, *, method=None, fields=None, body=None, paginate=False, token=None):
        captured["calls"] = captured.get("calls", 0) + 1
        captured["path"] = path
        captured["method"] = method
        captured["body"] = body
        return {"id": 7, "html_url": "https://example.test/r/7"}

    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    result = post.post_review(
        _review(), _ctx(repo="owner/name"), agent_name="codex", event="COMMENT"
    )
    assert captured["calls"] == 1
    assert captured["path"] == "/repos/owner/name/pulls/42/reviews"
    assert captured["method"] == "POST"
    assert captured["body"]["commit_id"] == "headsha123"
    assert captured["body"]["event"] == "COMMENT"
    # The API response is surfaced to the caller.
    assert result["id"] == 7
    assert result["html_url"] == "https://example.test/r/7"


def test_post_review_default_event_is_comment_self_review_safety(monkeypatch):
    captured = {}

    def _fake_rest(path, *, method=None, fields=None, body=None, paginate=False, token=None):
        captured["body"] = body
        return {"id": 1}

    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    # An APPROVED status with no override would map to APPROVE — but the CLI
    # default is COMMENT. Here we assert build maps as documented; the CLI's
    # COMMENT default is exercised via post_cli. Pass no event → status-derived.
    post.post_review(_review("APPROVED"), _ctx(), agent_name="codex")
    assert captured["body"]["event"] == "APPROVE"  # status-derived when no override


def test_post_review_resolves_repo_when_ctx_repo_none(monkeypatch):
    captured = {}

    def _fake_repo_view(*, json_fields=None, jq=None, **_k):
        return "inferred/repo"

    def _fake_rest(path, *, method=None, fields=None, body=None, paginate=False, token=None):
        captured["path"] = path
        return {"id": 1}

    monkeypatch.setattr(gh, "repo_view", _fake_repo_view, raising=True)
    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    post.post_review(_review(), _ctx(repo=None), agent_name="codex")
    assert captured["path"] == "/repos/inferred/repo/pulls/42/reviews"


def test_post_review_posts_to_canonical_slug_in_ctx(monkeypatch):
    """The POST path is built from ctx.repo. resolve_pr stores the CANONICAL slug
    there (alias→canonical), so the write path never carries the alias that would
    307 on POST."""
    captured = {}

    def _fake_rest(path, *, method=None, fields=None, body=None, paginate=False, token=None):
        captured["path"] = path
        return {"id": 1}

    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    # ctx.repo holds the canonical slug (as resolve_pr would have normalized it).
    post.post_review(_review(), _ctx(repo="phos-editor/core"), agent_name="codex")
    assert captured["path"] == "/repos/phos-editor/core/pulls/42/reviews"
    assert "phos-core" not in captured["path"]  # the alias must not appear


def test_post_review_raises_clear_error_on_gh_failure(monkeypatch):
    def _fake_rest(*_a, **_k):
        raise gh.GhError("422 unprocessable")

    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    with pytest.raises(RuntimeError, match="Failed to post review"):
        post.post_review(_review(), _ctx(), agent_name="codex", event="COMMENT")
