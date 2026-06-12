"""Adapter detection over recorded PR scenarios.

Each test asserts where the Copilot/Gemini adapters place a reviewer in the
lifecycle, exercising the load-bearing rules: head-SHA filtering, the
resolved-thread filter, and Gemini's weak (reaction/comment) signals.
"""

from __future__ import annotations

from release_core.prstate.model import ReviewLifecycle
from release_core.prstate.reviewers import (
    REGISTRY,
    CodeRabbitAdapter,
    CopilotAdapter,
    GeminiAdapter,
    required_reviewers,
)

COPILOT = CopilotAdapter()
CODERABBIT = CodeRabbitAdapter()
GEMINI = GeminiAdapter()


def test_registry_catalogs_copilot_coderabbit_and_gemini():
    # The registry is the CATALOG; which entries gate is the config knob.
    assert [r.name for r in REGISTRY] == ["copilot", "coderabbit", "gemini"]
    # `requestable` marks eligibility to be a required gate (a real request
    # edge + the #614 attach-verification), NOT the current required set.
    assert COPILOT.requestable is True
    assert CODERABBIT.requestable is True
    assert GEMINI.requestable is False


def test_default_required_set_is_copilot_and_coderabbit():
    # The shipped default config: both gate Ready (parallel-required, #622).
    assert [r.name for r in required_reviewers()] == ["copilot", "coderabbit"]


def test_copilot_done_with_open_comment(context):
    ctx = context("copilot_changes_requested")
    assert COPILOT.detect(ctx) == ReviewLifecycle.DONE_COMMENTS
    assert GEMINI.detect(ctx) == ReviewLifecycle.NOT_REQUESTED
    assert len(COPILOT.open_threads(ctx)) == 1


def test_both_done_clean(context):
    ctx = context("copilot_clean_gemini_clean")
    assert COPILOT.detect(ctx) == ReviewLifecycle.DONE_CLEAN
    assert GEMINI.detect(ctx) == ReviewLifecycle.DONE_CLEAN
    assert ctx.open_threads() == []


def test_gemini_eyes_is_in_progress_copilot_requested(context):
    ctx = context("gemini_eyes_copilot_requested")
    assert GEMINI.detect(ctx) == ReviewLifecycle.IN_PROGRESS
    assert COPILOT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_stale_copilot_review_does_not_count_as_done(context):
    ctx = context("copilot_stale_review")
    # A review against an earlier commit must not read as done on this head.
    assert COPILOT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_gemini_review_on_earlier_head_still_counts_as_done():
    # The exact #345-fixup case: Gemini reviewed the OLD head, a fixup made a new
    # head, and the lingering eyes reaction must NOT downgrade Gemini to
    # in_progress — it reviews once and won't re-review the push.
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        reviews=[Review(1, "gemini-code-assist[bot]", "COMMENTED", "old", "")],
        reactions=[{"content": "eyes", "user": {"login": "gemini-code-assist[bot]"}}],
    )
    assert GEMINI.detect(ctx) == ReviewLifecycle.DONE_CLEAN


def test_copilot_review_on_earlier_head_does_NOT_count_done():
    # Contrast: Copilot is head-strict — a review on an old head is stale.
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        reviews=[Review(1, "Copilot", "COMMENTED", "old", "")],
        requested_logins=["Copilot"],
    )
    assert COPILOT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_dismissed_copilot_review_on_head_does_NOT_count_done():
    # A DISMISSED review (cleared by an admin/author) is retracted — even on the
    # current head it must not read as done; the PR falls back to REQUESTED.
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        reviews=[Review(1, "Copilot", "DISMISSED", "new", "")],
        requested_logins=["Copilot"],
    )
    assert COPILOT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_dismissed_gemini_review_does_NOT_count_done():
    # Same for best-effort Gemini: a dismissed review is not a standing verdict.
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        reviews=[Review(1, "gemini-code-assist[bot]", "DISMISSED", "old", "")],
    )
    assert GEMINI.detect(ctx) == ReviewLifecycle.NOT_REQUESTED


def test_resolved_thread_clears_open_but_keeps_authored(context):
    ctx = context("copilot_done_all_resolved")
    assert COPILOT.detect(ctx) == ReviewLifecycle.DONE_COMMENTS
    assert COPILOT.open_threads(ctx) == []
    assert len(COPILOT.authored_threads(ctx)) == 1


# --- the act side (request / cancel / instruction files; release#555) -------


def test_by_name_resolves_registry_adapters():
    from release_core.prstate.reviewers import by_name

    assert by_name("copilot") is not None and by_name("copilot").name == "copilot"
    assert by_name("GEMINI") is not None and by_name("GEMINI").name == "gemini"
    assert by_name("coderabbit") is not None and by_name("coderabbit").name == "coderabbit"
    assert by_name("nosuchbot") is None


def test_copilot_request_goes_through_gh_pr_edit_graphql(monkeypatch):
    # The GraphQL `gh pr edit --add-reviewer @copilot` path is load-bearing:
    # the REST requested_reviewers POST silently no-ops for Copilot.
    from release_core.prstate import ghapi

    calls: list[tuple] = []
    monkeypatch.setattr(
        ghapi,
        "pr_edit_reviewer",
        lambda pr, reviewer, remove=False: calls.append((pr, reviewer, remove)),
    )
    assert COPILOT.request(91) is True
    assert calls == [(91, "@copilot", False)]


def test_copilot_cancel_removes_the_reviewer(monkeypatch):
    from release_core.prstate import ghapi

    calls: list[tuple] = []
    monkeypatch.setattr(
        ghapi,
        "pr_edit_reviewer",
        lambda pr, reviewer, remove=False: calls.append((pr, reviewer, remove)),
    )
    assert COPILOT.cancel(91) is True
    assert calls == [(91, "@copilot", True)]


def test_gemini_request_and_cancel_are_noops(monkeypatch):
    # Gemini auto-triggers and is best-effort: no request mechanism, no gh call.
    from release_core.prstate import ghapi

    def _boom(*a, **k):  # any gh traffic is a bug
        raise AssertionError("gemini must not touch gh")

    monkeypatch.setattr(ghapi, "pr_edit_reviewer", _boom)
    monkeypatch.setattr(ghapi, "_gh", _boom)
    assert GEMINI.request(91) is False
    assert GEMINI.cancel(91) is False


def test_adapters_declare_their_instruction_files():
    # Structure only (#555): the adapter declares where its review-instruction
    # file lives; shipping content there is a separate onboarding decision.
    assert COPILOT.instruction_files == (".github/copilot-instructions.md",)
    assert CODERABBIT.instruction_files == (".coderabbit.yaml",)
    assert GEMINI.instruction_files == (".gemini/styleguide.md",)


# --- CodeRabbit adapter (release#622) ---------------------------------------


def test_coderabbit_matches_its_bot_login():
    assert CODERABBIT.matches("coderabbitai[bot]") is True
    assert CODERABBIT.matches("CodeRabbit") is True
    assert CODERABBIT.matches("Copilot") is False


def test_coderabbit_done_on_head_with_open_comment():
    # Head-strict + leaves a thread → DONE_COMMENTS, with the open thread tracked.
    from release_core.prstate.model import PullContext, Review, ReviewComment, Thread

    thread = Thread(
        thread_id="PRT_cr1",
        is_resolved=False,
        comments=(ReviewComment(1, "a.py", 3, "nit", "coderabbitai[bot]"),),
    )
    ctx = PullContext(
        number=1,
        head_sha="h",
        is_draft=True,
        reviews=[Review(1, "coderabbitai[bot]", "COMMENTED", "h", "")],
        threads=[thread],
    )
    assert CODERABBIT.detect(ctx) == ReviewLifecycle.DONE_COMMENTS
    assert len(CODERABBIT.open_threads(ctx)) == 1


def test_coderabbit_is_head_strict_like_copilot():
    # A review on an earlier head is stale — must NOT read as done on this head.
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        reviews=[Review(1, "coderabbitai[bot]", "COMMENTED", "old", "")],
        requested_logins=["coderabbitai[bot]"],
    )
    assert CODERABBIT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_dismissed_coderabbit_review_does_not_count_done():
    from release_core.prstate.model import PullContext, Review

    ctx = PullContext(
        number=1,
        head_sha="h",
        is_draft=True,
        reviews=[Review(1, "coderabbitai[bot]", "DISMISSED", "h", "")],
        requested_logins=["coderabbitai[bot]"],
    )
    assert CODERABBIT.detect(ctx) == ReviewLifecycle.REQUESTED


def test_coderabbit_request_and_cancel_go_through_gh_pr_edit(monkeypatch):
    # The same GraphQL add-reviewer path Copilot uses — it creates a real
    # review_requested edge, so the generic #614 attach-verification applies.
    from release_core.prstate import ghapi

    calls: list[tuple] = []
    monkeypatch.setattr(
        ghapi,
        "pr_edit_reviewer",
        lambda pr, reviewer, remove=False: calls.append((pr, reviewer, remove)),
    )
    assert CODERABBIT.request(55) is True
    assert CODERABBIT.cancel(55) is True
    assert calls == [(55, "coderabbitai[bot]", False), (55, "coderabbitai[bot]", True)]
