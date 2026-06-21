"""Tests for the programmatic run-and-post service.

No real backend, git, or gh: ``get_backend``, ``resolve_pr``, ``post_review``,
and ``ghapp.load_app`` are monkeypatched. The proof is wiring — the right agent,
prompt, cwd, and ``as_app`` flag thread through generate → post.
"""

from __future__ import annotations

import pytest
from release_core.review import service
from release_core.review.diff import PRContext


def _ctx() -> PRContext:
    return PRContext(
        number=42,
        repo="o/r",
        head_sha="head",
        base_ref="main",
        base_sha="base",
        diff="diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n",
        changed_files=["x"],
        workdir="/tmp/checkout",
    )


class _FakeBackend:
    def __init__(self):
        self.preflighted = False
        self.run_args = None

    def preflight(self):
        self.preflighted = True

    def run(self, prompt, schema, *, cwd=None):
        self.run_args = {"prompt": prompt, "schema": schema, "cwd": cwd}
        return {"summary": {"status": "COMMENT", "overall_feedback": "ok"}, "comments": []}


# --- generate_review ---------------------------------------------------------


def test_generate_review_preflights_and_runs(monkeypatch):
    backend = _FakeBackend()
    seen = {}

    def _get_backend(agent, **kwargs):
        seen["agent"] = agent
        seen["kwargs"] = kwargs
        return backend

    monkeypatch.setattr(service, "get_backend", _get_backend)
    ctx = _ctx()
    review = service.generate_review("codex", ctx, model="pro")

    assert seen["agent"] == "codex"
    assert seen["kwargs"] == {"model": "pro"}
    assert backend.preflighted is True
    # Ran in the PR checkout, with the diff embedded in the prompt.
    assert backend.run_args["cwd"] == "/tmp/checkout"
    assert ctx.diff in backend.run_args["prompt"]
    assert review["summary"]["status"] == "COMMENT"


def test_generate_review_agy_embeds_schema_inline(monkeypatch):
    # agy has no native schema enforcement → the schema prose is in the prompt.
    backend = _FakeBackend()
    monkeypatch.setattr(service, "get_backend", lambda agent, **k: backend)
    service.generate_review("agy", _ctx())
    assert "JSON Schema:" in backend.run_args["prompt"]


def test_generate_review_codex_omits_schema_inline(monkeypatch):
    backend = _FakeBackend()
    monkeypatch.setattr(service, "get_backend", lambda agent, **k: backend)
    service.generate_review("codex", _ctx())
    assert "JSON Schema:" not in backend.run_args["prompt"]


def test_generate_review_propagates_backend_unavailable(monkeypatch):
    from release_core.review.backends.base import BackendUnavailable

    class _Unavailable:
        def preflight(self):
            raise BackendUnavailable("codex CLI not found")

        def run(self, *a, **k):  # pragma: no cover - never reached
            raise AssertionError("run must not be called when preflight fails")

    monkeypatch.setattr(service, "get_backend", lambda agent, **k: _Unavailable())
    with pytest.raises(BackendUnavailable, match="not found"):
        service.generate_review("codex", _ctx())


# --- run_and_post ------------------------------------------------------------


def test_run_and_post_wires_resolve_generate_post(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(service.ghapp, "load_app", lambda agent: {"slug": "adr-codex-review"})
    monkeypatch.setattr(service, "resolve_pr", lambda pr, repo=None: ctx)

    gen_calls = {}

    def _fake_generate(agent, c, *, instructions_path=None, model="pro"):
        gen_calls["agent"] = agent
        gen_calls["ctx"] = c
        return {"summary": {"status": "COMMENT", "overall_feedback": "x"}, "comments": []}

    monkeypatch.setattr(service, "generate_review", _fake_generate)

    post_calls = {}

    def _fake_post(review, c, *, agent_name, event=None, dry_run=False, as_app=False):
        post_calls.update(
            {
                "review": review,
                "ctx": c,
                "agent_name": agent_name,
                "event": event,
                "dry_run": dry_run,
                "as_app": as_app,
            }
        )
        return {"id": 123}

    monkeypatch.setattr(service.post, "post_review", _fake_post)

    result = service.run_and_post("codex", 42)

    assert gen_calls["agent"] == "codex"
    assert gen_calls["ctx"] is ctx
    # agent_name + as_app thread through to the post.
    assert post_calls["agent_name"] == "codex"
    assert post_calls["as_app"] is True
    assert post_calls["event"] == "COMMENT"
    assert result == {
        "review": {"summary": {"status": "COMMENT", "overall_feedback": "x"}, "comments": []},
        "post": {"id": 123},
        "ctx_repo": "o/r",
        "pr": 42,
    }


def test_run_and_post_raises_when_app_missing(monkeypatch):
    # as_app=True (default) with no registered app → clear RuntimeError, no
    # silent fallback to posting as the user.
    monkeypatch.setattr(service.ghapp, "load_app", lambda agent: None)

    def _no_resolve(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("resolve_pr must not run when the app is missing")

    monkeypatch.setattr(service, "resolve_pr", _no_resolve)
    with pytest.raises(RuntimeError, match="No GitHub App is registered"):
        service.run_and_post("codex", 42)


def test_run_and_post_app_check_skipped_on_dry_run(monkeypatch):
    # as_app=True + dry_run=True posts nothing (and mints no token), so the
    # app-registration precheck is skipped — dry-run stays useful for previewing
    # generation before the app is registered.
    ctx = _ctx()

    def _load_app(agent):  # pragma: no cover - must not be reached
        raise AssertionError("load_app must not be consulted on a dry run")

    monkeypatch.setattr(service.ghapp, "load_app", _load_app)
    monkeypatch.setattr(service, "resolve_pr", lambda pr, repo=None: ctx)
    monkeypatch.setattr(
        service,
        "generate_review",
        lambda *a, **k: {"summary": {"status": "COMMENT", "overall_feedback": "x"}, "comments": []},
    )
    seen = {}
    monkeypatch.setattr(
        service.post,
        "post_review",
        lambda review, c, **kwargs: seen.update(kwargs) or {"dry": True},
    )
    result = service.run_and_post("codex", 42, dry_run=True)
    assert seen["as_app"] is True
    assert seen["dry_run"] is True
    assert result["post"] == {"dry": True}


def test_run_and_post_app_check_skipped_when_not_as_app(monkeypatch):
    # as_app=False posts as the user — no app required.
    ctx = _ctx()
    monkeypatch.setattr(service, "resolve_pr", lambda pr, repo=None: ctx)
    monkeypatch.setattr(service, "generate_review", lambda *a, **k: {"comments": []})

    def _load_app(agent):  # pragma: no cover - must not be reached
        raise AssertionError("load_app must not be consulted when as_app=False")

    monkeypatch.setattr(service.ghapp, "load_app", _load_app)
    seen = {}
    monkeypatch.setattr(
        service.post,
        "post_review",
        lambda review, c, **kwargs: seen.update(kwargs) or {"ok": True},
    )
    result = service.run_and_post("codex", 42, as_app=False)
    assert seen["as_app"] is False
    assert result["post"] == {"ok": True}
