"""Tests for the review facade (instructions + PR diff + backend).

No test invokes the real codex/agy binaries or real git/gh: ``diff.resolve_pr``
is monkeypatched to a fixed PRContext. The user-visible proof here is dry-run
parity — the shared prompt body (instructions + diff) is identical in what each
agent would receive.
"""

from __future__ import annotations

import pytest
from release_core.review import facade
from release_core.review.diff import PRContext, ReviewError
from release_core.review.instructions import default_instructions, load_instructions

FIXTURE_DIFF = "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-old\n+new\n"


def _fixture_ctx() -> PRContext:
    return PRContext(
        number=365,
        repo=None,
        head_sha="abc123def456",
        base_ref="main",
        base_sha="000base",
        diff=FIXTURE_DIFF,
        changed_files=["x.py"],
        workdir="/tmp/fixture-checkout",
    )


# --- instructions -------------------------------------------------------------


def test_default_instructions_non_empty():
    text = default_instructions()
    assert text.strip()


def test_default_instructions_has_no_phos():
    text = default_instructions()
    assert "phos" not in text.lower()


def test_default_instructions_covers_review_dimensions():
    text = default_instructions().lower()
    for dimension in ("correctness", "completeness", "code quality", "robustness"):
        assert dimension in text
    assert "ranked list" in text


def test_load_instructions_reads_custom_file(tmp_path):
    custom = tmp_path / "custom.txt"
    custom.write_text("Custom review guidance.\n", encoding="utf-8")
    assert load_instructions(str(custom)) == "Custom review guidance.\n"


def test_load_instructions_none_returns_default():
    assert load_instructions(None) == default_instructions()


# --- dry-run parity -----------------------------------------------------------


def _run_dry(monkeypatch, capsys, agent: str) -> str:
    monkeypatch.setattr(facade, "resolve_pr", lambda _pr, **_k: _fixture_ctx())
    rc = facade.run_review([agent, "--pr", "365", "--dry-run"])
    assert rc == 0
    return capsys.readouterr().out


def test_dry_run_parity_shares_prompt_body(monkeypatch, capsys):
    """Both agents' dry-run renderings must carry the same shared prompt body
    (instructions + diff) — the user-visible proof of 'what gets sent matches'."""
    codex_out = _run_dry(monkeypatch, capsys, "codex")
    agy_out = _run_dry(monkeypatch, capsys, "agy")

    instructions = default_instructions()
    # The instructions body and the diff appear verbatim in both renderings.
    assert instructions.strip() in codex_out
    assert instructions.strip() in agy_out
    assert FIXTURE_DIFF.strip() in codex_out
    assert FIXTURE_DIFF.strip() in agy_out


def test_dry_run_codex_uses_stdin(monkeypatch, capsys):
    out = _run_dry(monkeypatch, capsys, "codex")
    assert "stdin:" in out
    assert "codex" in out


def test_dry_run_agy_uses_file(monkeypatch, capsys):
    out = _run_dry(monkeypatch, capsys, "agy")
    assert "files:" in out
    assert "agy" in out


def test_dry_run_does_not_preflight_or_run(monkeypatch, capsys):
    """Dry-run must NOT touch the backend: even if preflight/run would raise,
    dry-run still returns 0."""
    monkeypatch.setattr(facade, "resolve_pr", lambda _pr, **_k: _fixture_ctx())

    def _boom(*_a, **_k):
        raise AssertionError("backend must not be invoked in dry-run")

    # Patch the methods on the backend classes that get_backend instantiates.
    monkeypatch.setattr(
        "release_core.review.backends.codex.CodexBackend.preflight", _boom, raising=True
    )
    monkeypatch.setattr("release_core.review.backends.codex.CodexBackend.run", _boom, raising=True)

    rc = facade.run_review(["codex", "--pr", "365", "--dry-run"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


# --- --pr surface -------------------------------------------------------------


def test_pr_is_required(capsys):
    """Omitting --pr is an argparse usage error (SystemExit(2))."""
    with pytest.raises(SystemExit) as exc:
        facade.run_review(["codex", "--dry-run"])
    assert exc.value.code == 2


def test_resolve_error_returns_1(monkeypatch, capsys):
    def _raise(_pr, **_k):
        raise ReviewError("not a git checkout")

    monkeypatch.setattr(facade, "resolve_pr", _raise)
    rc = facade.run_review(["codex", "--pr", "999"])
    assert rc == 1
    assert "not a git checkout" in capsys.readouterr().err


def test_run_passes_workdir_as_cwd(monkeypatch, capsys):
    """The backend runs in the PR checkout (ctx.workdir) so it can read files."""
    monkeypatch.setattr(facade, "resolve_pr", lambda _pr, **_k: _fixture_ctx())
    monkeypatch.setattr(
        "release_core.review.backends.codex.CodexBackend.preflight",
        lambda self: None,
        raising=True,
    )
    seen = {}

    def _fake_run(self, prompt, schema, *, cwd=None):
        seen["cwd"] = cwd
        return {"summary": "ok", "comments": []}

    monkeypatch.setattr(
        "release_core.review.backends.codex.CodexBackend.run", _fake_run, raising=True
    )
    rc = facade.run_review(["codex", "--pr", "365"])
    assert rc == 0
    assert seen["cwd"] == "/tmp/fixture-checkout"
