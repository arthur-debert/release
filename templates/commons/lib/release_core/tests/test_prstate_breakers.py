"""Circuit-breaker heuristics + their fold-in to the state machine."""

from __future__ import annotations

from itertools import count

from release_core.prstate.breakers import build_cycles, evaluate_breakers
from release_core.prstate.model import PullContext, Review, ReviewComment, Thread
from release_core.prstate.reviewers import by_name
from release_core.prstate.state import TaskState, evaluate


def review(rid: int, sha: str, author: str = "Copilot") -> Review:
    return Review(review_id=rid, author=author, state="COMMENTED", commit_id=sha, body="")


_FID = count(9000)  # unique comment/thread ids for synthetic findings


def finding(rid: int, path: str, line: int) -> Thread:
    """A review thread holding one finding submitted with review `rid`.

    Resolved on purpose: a resolved finding was still a finding of that cycle,
    so the breakers must count it (resolution clears the *open*-thread gate,
    not the cycle history).
    """
    cid = next(_FID)
    comment = ReviewComment(
        comment_id=cid, path=path, line=line, body="x", author="Copilot", review_id=rid
    )
    return Thread(thread_id=f"PRT_f{cid}", is_resolved=True, comments=(comment,))


def ctx(reviews, *, findings=None, threads=None, head=None, mergeable="MERGEABLE", checks=None):
    return PullContext(
        number=1,
        head_sha=head or (reviews[-1].commit_id if reviews else "h"),
        is_draft=True,
        base_ref="main",
        mergeable=mergeable,
        reviews=list(reviews),
        threads=[*(findings or []), *(threads or [])],
        checks=checks or [],
    )


def open_copilot_thread(path="a.py", line=1):
    comment = ReviewComment(comment_id=1, path=path, line=line, body="x", author="Copilot")
    return Thread(thread_id="PRT_1", is_resolved=False, comments=(comment,))


# --- cycle counting -------------------------------------------------------


def test_build_cycles_one_per_copilot_review_chronological():
    reviews = [review(10, "a"), review(20, "b"), review(5, "c", author="gemini-bot")]
    cycles = build_cycles(ctx(reviews))
    assert [c.index for c in cycles] == [1, 2]
    assert [c.commit_id for c in cycles] == ["a", "b"]  # gemini excluded, id-ordered


def test_build_cycles_matches_both_copilot_login_variants():
    # The review login is `copilot-pull-request-reviewer[bot]` but the comment
    # author renders as `Copilot` — both must group into cycles (release#455).
    reviews = [review(10, "a", author="copilot-pull-request-reviewer[bot]")]
    cycles = build_cycles(ctx(reviews, findings=[finding(10, "a.py", 1)]))
    assert len(cycles) == 1
    assert cycles[0].comment_keys == frozenset({("a.py", 1)})


def test_build_cycles_findings_come_from_threads_even_when_resolved():
    # Findings derive from review threads (the GraphQL source of truth) keyed
    # by review_id; a RESOLVED thread still counts toward its cycle's findings.
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [finding(1, "a.py", 1), finding(2, "b.py", 2)]
    cycles = build_cycles(ctx(reviews, findings=findings))
    assert cycles[0].comment_keys == frozenset({("a.py", 1)})
    assert cycles[1].comment_keys == frozenset({("b.py", 2)})


def test_cycle_cap_fires_on_fourth():
    reviews = [review(i, f"c{i}") for i in range(1, 5)]
    v = evaluate_breakers(ctx(reviews))
    assert v.stop and v.breaker == "cycle-cap" and v.cycles == 4


def test_three_cycles_under_cap_no_stop():
    reviews = [review(i, f"c{i}") for i in range(1, 4)]
    # disjoint findings each cycle -> no other breaker fires either
    findings = [finding(1, "a.py", 1), finding(2, "b.py", 2), finding(3, "c.py", 3)]
    assert not evaluate_breakers(ctx(reviews, findings=findings)).stop


def test_two_required_reviewers_across_two_heads_is_two_cycles_not_four():
    # The release#622 double-count bug: with TWO required reviewers, two human
    # iteration rounds (heads h1, h2) get four review objects (each reviewer
    # reviews each head). Cycles are ROUNDS, not reviews — so this is 2 cycles,
    # well under the cap of 3, and the cycle-cap breaker must NOT fire.
    reviews = [
        review(1, "h1", author="Copilot"),
        review(2, "h1", author="coderabbitai[bot]"),
        review(3, "h2", author="Copilot"),
        review(4, "h2", author="coderabbitai[bot]"),
    ]
    cycles = build_cycles(ctx(reviews))
    assert [c.commit_id for c in cycles] == ["h1", "h2"]  # one per head, not per review
    assert len(cycles) == 2
    v = evaluate_breakers(ctx(reviews))
    assert v.cycles == 2
    assert not v.stop


def test_a_cycle_unions_both_reviewers_findings_on_the_same_head():
    # Both required reviewers flag the same head: the cycle's findings are the
    # UNION of their thread comments, not one reviewer's.
    reviews = [review(1, "h1", author="Copilot"), review(2, "h1", author="coderabbitai[bot]")]
    findings = [finding(1, "a.py", 1), finding(2, "b.py", 2)]
    cycles = build_cycles(ctx(reviews, findings=findings))
    assert len(cycles) == 1
    assert cycles[0].comment_keys == frozenset({("a.py", 1), ("b.py", 2)})


# --- diff trajectory ------------------------------------------------------


def test_diff_trajectory_growing_stops():
    reviews = [review(1, "c1"), review(2, "c2"), review(3, "c3")]
    sizes = {"c1": 100, "c2": 200, "c3": 410}
    findings = [finding(1, "a", 1), finding(2, "b", 2), finding(3, "c", 3)]  # disjoint
    v = evaluate_breakers(ctx(reviews, findings=findings), diff_sizer=sizes.get)
    assert v.stop and v.breaker == "diff-trajectory"


def test_diff_trajectory_shrinking_no_stop():
    reviews = [review(1, "c1"), review(2, "c2"), review(3, "c3")]
    sizes = {"c1": 410, "c2": 200, "c3": 100}
    findings = [finding(1, "a", 1), finding(2, "b", 2), finding(3, "c", 3)]
    assert not evaluate_breakers(ctx(reviews, findings=findings), diff_sizer=sizes.get).stop


def test_diff_trajectory_skipped_without_sizer():
    # No diff_sizer -> diff breaker can't run; only 2 cycles so nothing else fires.
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [finding(1, "a", 1), finding(2, "b", 2)]
    assert not evaluate_breakers(ctx(reviews, findings=findings)).stop


def test_diff_trajectory_below_floor_no_stop():
    # Growing but tiny (1 -> 2 -> 3 lines) is below MIN_DIFF_LINES -> no false stop.
    reviews = [review(1, "c1"), review(2, "c2"), review(3, "c3")]
    sizes = {"c1": 1, "c2": 2, "c3": 3}
    findings = [finding(1, "a", 1), finding(2, "b", 2), finding(3, "c", 3)]
    assert not evaluate_breakers(ctx(reviews, findings=findings), diff_sizer=sizes.get).stop


# --- comment-set / repeat -------------------------------------------------


def test_comment_fixed_point_identical_stops():
    # Exact same findings two cycles running -> true fixed point.
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [
        finding(1, "a.py", 1),
        finding(1, "b.py", 2),
        finding(2, "a.py", 1),
        finding(2, "b.py", 2),
    ]
    v = evaluate_breakers(ctx(reviews, findings=findings))
    assert v.stop and v.breaker == "comment-set"


def test_comment_fixed_point_strict_subset_is_progress_no_stop():
    # cycle2 is a STRICT subset (b.py:2 got resolved) -> progress, not a fixed point.
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [finding(1, "a.py", 1), finding(1, "b.py", 2), finding(2, "a.py", 1)]
    assert not evaluate_breakers(ctx(reviews, findings=findings)).stop


def test_repeat_finding_three_consecutive_cycles_stops():
    # a.py:1 persists across all 3 cycles (each cycle's set differs, so it's not a
    # fixed point) -> repeat-finding after two failed fix attempts.
    reviews = [review(1, "c1"), review(2, "c2"), review(3, "c3")]
    findings = [
        finding(1, "a.py", 1),
        finding(1, "x.py", 1),
        finding(2, "a.py", 1),
        finding(2, "y.py", 2),
        finding(3, "a.py", 1),
        finding(3, "z.py", 3),
    ]
    v = evaluate_breakers(ctx(reviews, findings=findings))
    assert v.stop and v.breaker == "repeat-finding"


def test_repeat_finding_two_cycles_allows_second_attempt():
    # Same location flagged twice is allowed (a 2nd attempt is normal) -> no stop.
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [finding(1, "a.py", 1), finding(2, "a.py", 1), finding(2, "c.py", 3)]
    assert not evaluate_breakers(ctx(reviews, findings=findings)).stop


def test_disjoint_consecutive_findings_no_stop():
    reviews = [review(1, "c1"), review(2, "c2")]
    findings = [finding(1, "a.py", 1), finding(2, "b.py", 2)]
    assert not evaluate_breakers(ctx(reviews, findings=findings)).stop


# --- fold-in to state -----------------------------------------------------


# These scenarios model Copilot review CYCLES (the breaker subject); the second
# required reviewer is irrelevant here, so they pin the required set to Copilot.
_COPILOT_ONLY = [by_name("copilot")]


def test_breaker_overrides_addressing_with_blocked():
    reviews = [review(i, f"c{i}") for i in range(1, 5)]  # 4 cycles -> cap
    c = ctx(reviews, threads=[open_copilot_thread()], head="c4")
    status = evaluate(c, required=_COPILOT_ONLY)
    assert status.state is TaskState.BLOCKED
    assert status.breaker == "cycle-cap"
    assert "STOP" in status.next_action


def test_converged_pr_not_stopped_despite_many_cycles():
    # 4 cycles but every thread resolved + green + mergeable -> READY, not BLOCKED.
    reviews = [review(i, f"c{i}") for i in range(1, 5)]
    rollup = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]
    status = evaluate(ctx(reviews, threads=[], head="c4", checks=rollup), required=_COPILOT_ONLY)
    assert status.state is TaskState.READY
    assert status.cycles == 4
    assert status.breaker is None
