"""State machine: scenario -> TaskState, plus check-rollup classification."""

from __future__ import annotations

import pytest
from release_core.prstate.model import PullContext, Review
from release_core.prstate.reviewers import by_name
from release_core.prstate.state import (
    ChecksState,
    TaskState,
    classify_checks,
    evaluate,
    no_pr,
)


def test_no_pr():
    status = no_pr()
    assert status.state is TaskState.NO_PR
    assert "create a draft PR" in status.next_action


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("gemini_eyes_copilot_requested", TaskState.REVIEWS_PENDING),
        ("copilot_stale_review", TaskState.REVIEWS_PENDING),
        ("copilot_changes_requested", TaskState.ADDRESSING),
        ("reviewed_mergeable_unknown", TaskState.REVIEWED),
        ("validating_checks_pending", TaskState.VALIDATING),
        ("ready_checks_green", TaskState.READY),
        ("copilot_clean_gemini_clean", TaskState.READY),
        ("copilot_done_all_resolved", TaskState.READY),
        ("blocked_checks_failing", TaskState.BLOCKED),
        ("blocked_merge_conflict", TaskState.BLOCKED),
    ],
)
def test_evaluate_states(context, fixture, expected):
    assert evaluate(context(fixture)).state is expected


def test_best_effort_gemini_does_not_gate_ready(context):
    # Gemini is NOT_REQUESTED here, yet Copilot (required) is done clean with
    # green checks -> READY. A best-effort reviewer must not hold it back.
    status = evaluate(context("ready_checks_green"))
    assert status.state is TaskState.READY
    assert status.reviewers["gemini"] == "done_clean"


def test_addressing_reports_open_thread_count(context):
    status = evaluate(context("copilot_changes_requested"))
    assert status.state is TaskState.ADDRESSING
    assert status.open_threads == 1
    assert "1 open thread" in status.next_action


def test_addressing_names_the_thread_reading_tool(context):
    # Discoverability (#564): the agent must learn HOW to read the threads from
    # the next action itself, not fall back to raw `gh api`.
    status = evaluate(context("copilot_changes_requested"))
    assert "release-core pr review show" in status.next_action
    assert "resolve" in status.next_action


# --- READY next-action: draft vs already-flipped (#564) ----------------------


def test_ready_draft_says_flip(context):
    status = evaluate(context("ready_checks_green"))  # isDraft: true
    assert status.state is TaskState.READY
    assert "release-core pr ready" in status.next_action


def test_ready_non_draft_says_done_not_flip(context):
    # Post-flip a READY PR is in the human's hands: the next action must say
    # done/await merge, never re-prescribe the flip the agent already made.
    ctx = context("ready_checks_green")
    ctx.is_draft = False
    status = evaluate(ctx)
    assert status.state is TaskState.READY
    assert "release-core pr ready" not in status.next_action
    assert "done" in status.next_action
    assert "merge" in status.next_action


def test_blocked_reasons_are_distinct(context):
    assert "conflict" in evaluate(context("blocked_merge_conflict")).next_action
    assert "failing" in evaluate(context("blocked_checks_failing")).next_action


def test_status_to_dict_round_trips(context):
    d = evaluate(context("ready_checks_green")).to_dict()
    assert d["state"] == "ready"
    assert d["checks"] == "green"
    assert d["mergeable"] == "MERGEABLE"
    assert set(d) == {
        "pr",
        "state",
        "next_action",
        "reviewers",
        "open_threads",
        "checks",
        "mergeable",
        "cycles",
        "breaker",
    }


# --- REVIEWS_PENDING next-action wording (request vs re-request vs wait) ----


def test_reviews_pending_never_requested_says_request(context):
    # No review ever landed and Copilot is not requested → the action is to
    # REQUEST (not wait), and it must NOT mention re-request/stale.
    status = evaluate(context("copilot_never_requested"))
    assert status.state is TaskState.REVIEWS_PENDING
    assert "request for the current head" in status.next_action
    assert "copilot" in status.next_action  # the reviewer is named in the clause
    assert "RE-REQUEST" not in status.next_action
    assert "stale" not in status.next_action


def test_reviews_pending_stale_after_push_says_rerequest(context):
    # Copilot reviewed an EARLIER commit; a push has moved the head and reset the
    # request to not_requested. The action must distinguish this from a fresh
    # request: RE-REQUEST for the current head, and name the staleness.
    status = evaluate(context("copilot_stale_needs_rerequest"))
    assert status.state is TaskState.REVIEWS_PENDING
    assert "RE-REQUEST for the current head" in status.next_action
    assert "stale after a push" in status.next_action
    assert "copilot" in status.next_action


def test_reviews_pending_already_requested_says_wait(context):
    # Copilot is REQUESTED on the current head (no review yet) → just wait; the
    # action must not tell the caller to (re-)request what is already pending.
    status = evaluate(context("gemini_eyes_copilot_requested"))
    assert status.state is TaskState.REVIEWS_PENDING
    assert "wait (already requested on the current head)" in status.next_action
    assert "RE-REQUEST" not in status.next_action


# --- parallel-required: BOTH reviewers gate (release#622) -------------------


def _green_checks() -> list[dict]:
    return [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]


def _ctx_with_reviews(*authors_on_head: str) -> PullContext:
    """A draft PR, green + mergeable, with an APPROVED review on the head per
    named author — everything but the review set held constant."""
    return PullContext(
        number=1,
        head_sha="h",
        is_draft=True,
        mergeable="MERGEABLE",
        reviews=[Review(i, a, "APPROVED", "h", "") for i, a in enumerate(authors_on_head, 1)],
        checks=_green_checks(),
    )


def test_both_required_reviewers_reviewed_reaches_ready():
    # Copilot AND CodeRabbit both reviewed the current head → READY.
    status = evaluate(_ctx_with_reviews("Copilot", "coderabbitai[bot]"))
    assert status.state is TaskState.READY
    assert status.reviewers["copilot"].startswith("done")
    assert status.reviewers["coderabbit"].startswith("done")


def test_missing_coderabbit_review_is_not_ready_and_names_it_outstanding():
    # Copilot reviewed but CodeRabbit has not → still REVIEWS_PENDING, and the
    # engine names CodeRabbit as the outstanding required reviewer (the mocked
    # single-reviewer-outage case).
    status = evaluate(_ctx_with_reviews("Copilot"))
    assert status.state is TaskState.REVIEWS_PENDING
    assert "coderabbit" in status.next_action
    assert "copilot" not in status.next_action.split("—")[1]  # copilot is done, not pending


def test_missing_copilot_review_is_not_ready_and_names_it_outstanding():
    status = evaluate(_ctx_with_reviews("coderabbitai[bot]"))
    assert status.state is TaskState.REVIEWS_PENDING
    assert "copilot" in status.next_action


# --- the required SET is data-driven, not hard-coded to the two -------------


def test_required_set_is_data_driven_single_reviewer():
    # Drive the engine with a DIFFERENT required set — just CodeRabbit. With
    # only CodeRabbit's review present (no Copilot), it now reaches READY: the
    # gate follows the config, not a hard-coded pair.
    only_coderabbit = [by_name("coderabbit")]
    status = evaluate(_ctx_with_reviews("coderabbitai[bot]"), required=only_coderabbit)
    assert status.state is TaskState.READY


def test_required_set_is_data_driven_three_reviewers():
    # A three-reviewer required set proves the engine reads the SET generically
    # — no two-reviewer assumption. The third is a tiny FAKE requestable adapter
    # (not Gemini, which is non-requestable and may never be required): with no
    # review from it, the PR stays REVIEWS_PENDING and names it outstanding.
    from release_core.prstate.model import ReviewLifecycle
    from release_core.prstate.reviewers import ReviewerAdapter

    class _Falcon(ReviewerAdapter):
        name = "falcon"
        requestable = True

        def matches(self, login: str) -> bool:
            return "falcon" in login.lower()

        def detect(self, ctx) -> ReviewLifecycle:
            on_head = any(self.matches(r.author) for r in ctx.reviews_on_head())
            return ReviewLifecycle.DONE_CLEAN if on_head else ReviewLifecycle.NOT_REQUESTED

    three = [by_name("copilot"), by_name("coderabbit"), _Falcon()]
    status = evaluate(_ctx_with_reviews("Copilot", "coderabbitai[bot]"), required=three)
    assert status.state is TaskState.REVIEWS_PENDING
    assert "falcon" in status.next_action


def test_a_push_re_stales_both_required_reviewers():
    # Both reviewed an EARLIER head; a push moved the head. Both are now stale →
    # the engine asks to RE-REQUEST both for the current head.
    ctx = PullContext(
        number=1,
        head_sha="new",
        is_draft=True,
        mergeable="MERGEABLE",
        reviews=[
            Review(1, "Copilot", "APPROVED", "old", ""),
            Review(2, "coderabbitai[bot]", "APPROVED", "old", ""),
        ],
        checks=_green_checks(),
    )
    status = evaluate(ctx)
    assert status.state is TaskState.REVIEWS_PENDING
    assert "RE-REQUEST" in status.next_action
    assert "copilot" in status.next_action
    assert "coderabbit" in status.next_action


# --- classify_checks ------------------------------------------------------


def test_classify_empty_is_none():
    assert classify_checks([]) is ChecksState.NONE


def test_classify_all_success_is_green():
    rollup = [
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "StatusContext", "state": "SUCCESS"},
    ]
    assert classify_checks(rollup) is ChecksState.GREEN


def test_classify_pending_beats_green():
    rollup = [
        {"status": "COMPLETED", "conclusion": "SUCCESS"},
        {"status": "IN_PROGRESS", "conclusion": None},
    ]
    assert classify_checks(rollup) is ChecksState.PENDING


def test_classify_failing_beats_everything():
    rollup = [
        {"status": "IN_PROGRESS", "conclusion": None},
        {"status": "COMPLETED", "conclusion": "FAILURE"},
    ]
    assert classify_checks(rollup) is ChecksState.FAILING


def test_classify_status_context_error_is_failing():
    rollup = [{"__typename": "StatusContext", "state": "ERROR"}]
    assert classify_checks(rollup) is ChecksState.FAILING


def test_classify_expected_status_is_pending():
    # EXPECTED = a status that's expected but hasn't reported yet -> not green.
    rollup = [{"__typename": "StatusContext", "state": "EXPECTED"}]
    assert classify_checks(rollup) is ChecksState.PENDING


def test_classify_neutral_and_skipped_are_green():
    rollup = [
        {"status": "COMPLETED", "conclusion": "NEUTRAL"},
        {"status": "COMPLETED", "conclusion": "SKIPPED"},
    ]
    assert classify_checks(rollup) is ChecksState.GREEN
