"""The `pr review` act surface (release#555) — registry-driven, no name branching.

Covers the CLI contract offline (usage errors, --reviewer selection, no-op
backends). The CLI layer is exercised against FAKE adapters to prove it never
branches on a reviewer's name — selection and mechanics flow entirely through
the adapter interface — plus one test through the REAL registry down to the
(mocked) gh boundary. The wait moved to the engine-owned `pr wait`
(release#503) — see test_prstate_wait_cli.py.
"""

from __future__ import annotations

import pytest
from release_core.prstate import ghapi
from release_core.prstate.cli import review
from release_core.prstate.model import PullContext, Review, ReviewLifecycle
from release_core.prstate.reviewers import ReviewerAdapter


class FakeAdapter(ReviewerAdapter):
    """A reviewer with scriptable act/read behavior — the CLI must treat it
    identically to any registered bot."""

    def __init__(self, name: str, *, required: bool = True, mechanism: bool = True):
        self.name = name
        self.required = required
        self.mechanism = mechanism  # False = auto-triggering/no-op backend
        self.requests: list[int] = []
        self.cancels: list[int] = []
        self.done = False

    def matches(self, login: str) -> bool:
        return self.name in login.lower()

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        return ReviewLifecycle.DONE_CLEAN if self.done else ReviewLifecycle.REQUESTED

    def request(self, pr: int) -> bool:
        self.requests.append(pr)
        return self.mechanism

    def cancel(self, pr: int) -> bool:
        self.cancels.append(pr)
        return self.mechanism


@pytest.fixture
def fakes(monkeypatch):
    """Two required adapters + one best-effort no-op, wired as THE registry."""
    alpha = FakeAdapter("alpha")
    beta = FakeAdapter("beta")
    gamma = FakeAdapter("gamma", required=False, mechanism=False)
    registry = [alpha, beta, gamma]
    monkeypatch.setattr(review, "REGISTRY", registry)
    monkeypatch.setattr(review, "required_reviewers", lambda: [a for a in registry if a.required])
    monkeypatch.setattr(
        review, "by_name", lambda name: next((a for a in registry if a.name == name.lower()), None)
    )
    # The bare-request path skips already-DONE reviewers, building a LIGHT
    # context via gather_reviews() (release#852) to run each adapter's detect.
    # Mock that offline — the FakeAdapters' detect keys off `.done` (set
    # per-test), ignoring ctx. The heavy gather() is wired to BLOW UP so any test
    # that accidentally drags it onto the skip path fails loudly.
    monkeypatch.setattr(
        review, "gather_reviews", lambda pr: PullContext(number=pr, head_sha="h", is_draft=True)
    )
    monkeypatch.setattr(
        review,
        "gather",
        lambda pr: pytest.fail(
            "bare-request skip path must use the light gather_reviews, not gather"
        ),
    )
    return alpha, beta, gamma


def _script_attach(monkeypatch, states):
    """Wire `attach_state` to return `states` in order (last repeats); record calls."""
    calls: list[int] = []

    def _state(pr: int):
        idx = min(len(calls), len(states) - 1)
        calls.append(pr)
        return states[idx]

    monkeypatch.setattr(review, "attach_state", _state)
    return calls


@pytest.fixture
def attach_ok(monkeypatch):
    """attach_state reports every reviewer's request edge as present (release#614:
    the verb verifies the attach, so dispatch tests need a healthy backend)."""
    return _script_attach(monkeypatch, [(["alpha[bot]", "beta[bot]", "gamma[bot]", "Copilot"], [])])


@pytest.fixture
def sleeps(monkeypatch):
    """Record (instead of perform) the verification poll's sleeps."""
    rec: list[int] = []
    monkeypatch.setattr(review.time, "sleep", rec.append)
    return rec


# --- argv contract (offline) ------------------------------------------------


@pytest.mark.parametrize("main", [review.request_main, review.cancel_main, review.show_main])
def test_help_exits_zero(main, capsys):
    assert main(["--help"]) == 0
    assert "pr review" in capsys.readouterr().out


def test_nonnumeric_pr_is_usage_error(capsys):
    assert review.request_main(["abc"]) == 64
    assert "numeric" in capsys.readouterr().err


def test_unknown_option_is_usage_error(capsys):
    assert review.request_main(["--nope"]) == 64
    assert "unknown option" in capsys.readouterr().err


def test_reviewer_flag_needs_a_value(capsys):
    assert review.request_main(["1", "--reviewer"]) == 64
    assert "--reviewer" in capsys.readouterr().err


def test_positional_reviewer_error_names_the_flag(capsys):
    # `pr review request <pr> <reviewer>` is the observed misuse (#564): the
    # only positional is the PR number, and the error must teach the flag
    # instead of a bare "too many arguments".
    assert review.request_main(["1", "somebot"]) == 64
    err = capsys.readouterr().err
    assert "'somebot'" in err
    assert "--reviewer <name>" in err


def test_unknown_reviewer_is_usage_error(fakes, capsys):
    assert review.request_main(["1", "--reviewer", "nosuchbot"]) == 64
    err = capsys.readouterr().err
    assert "unknown reviewer" in err
    assert "alpha" in err  # the known set is named


def test_gh_failure_resolving_the_pr_is_exit_1_not_usage(monkeypatch, fakes, capsys):
    # With <pr> omitted, a gh failure (auth, no PR for branch, missing gh)
    # surfaces as a gh failure with its real message — never as exit 64.
    def _gh_down(args, **kwargs):
        raise ghapi.GhError("gh pr view failed (1): not logged in")

    monkeypatch.setattr(ghapi, "_gh", _gh_down)
    assert review.request_main([]) == 1
    assert "not logged in" in capsys.readouterr().err


def test_omitted_pr_resolves_from_current_branch(monkeypatch, fakes, attach_ok, capsys):
    alpha, beta, _ = fakes
    monkeypatch.setattr(ghapi, "_gh", lambda args, **kwargs: '{"number": 12}')
    assert review.request_main([]) == 0
    assert alpha.requests == [12]
    assert beta.requests == [12]


# --- request / cancel dispatch ----------------------------------------------


def test_request_defaults_to_all_required_reviewers(fakes, attach_ok, capsys):
    alpha, beta, gamma = fakes
    assert review.request_main(["7"]) == 0
    assert alpha.requests == [7]
    assert beta.requests == [7]
    assert gamma.requests == []  # best-effort: not in the default scope
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out


def test_reviewer_flag_selects_one_adapter(fakes, attach_ok, capsys):
    alpha, beta, _ = fakes
    assert review.request_main(["7", "--reviewer", "beta"]) == 0
    assert beta.requests == [7]
    assert alpha.requests == []


def test_noop_backend_reports_noop_and_succeeds(fakes, attach_ok, sleeps, capsys):
    # No request mechanism → no request edge to verify: the no-op contract is
    # untouched by attach verification (release#614) — no poll, no failure.
    _, _, gamma = fakes
    assert review.request_main(["7", "--reviewer", "gamma"]) == 0
    assert gamma.requests == [7]
    out = capsys.readouterr().out
    assert "no-op" in out
    assert "verified" not in out
    assert sleeps == []
    assert attach_ok == [7]  # the pre-request baseline read only — no poll


# --- bare request skips already-done reviewers; --reviewer forces (rerun config) --


def test_bare_request_skips_a_reviewer_already_done(fakes, attach_ok, capsys):
    # review-once: a required reviewer that has already reviewed (detect → DONE)
    # is NOT re-requested on the bare path — re-running it would cost a token /
    # model run for a review it already gave.
    alpha, beta, _ = fakes
    alpha.done = True  # alpha already reviewed
    assert review.request_main(["7"]) == 0
    assert alpha.requests == []  # skipped
    assert beta.requests == [7]  # not done → requested
    out = capsys.readouterr().out
    assert "alpha: already reviewed #7 (review-once) — skip" in out


def test_bare_request_skip_path_uses_light_fetch_not_heavy_gather(monkeypatch, fakes, attach_ok):
    # release#852: the frequently-run skip decision builds its context via the
    # LIGHT gather_reviews (head sha + reviews + requested + rerun) — NOT the
    # full gather (threads-cursor walk + reactions/issue-comment pagination).
    # The fixture wires gather() to pytest.fail, so reaching it would blow up;
    # here we additionally assert gather_reviews IS the fetch that runs.
    alpha, beta, _ = fakes
    calls: list[int] = []
    monkeypatch.setattr(
        review,
        "gather_reviews",
        lambda pr: calls.append(pr) or PullContext(number=pr, head_sha="h", is_draft=True),
    )
    assert review.request_main(["7"]) == 0
    assert calls == [7]  # the light fetch ran exactly once for the skip decision


def test_bare_request_when_all_done_requests_nothing(fakes, capsys):
    alpha, beta, _ = fakes
    alpha.done = True
    beta.done = True
    assert review.request_main(["7"]) == 0
    assert alpha.requests == [] and beta.requests == []
    out = capsys.readouterr().out
    assert "all required reviewers already reviewed #7 — nothing to request" in out


def test_explicit_reviewer_forces_request_even_when_done(fakes, attach_ok, capsys):
    # --reviewer X is the manual FORCE / re-run escape hatch: it requests X
    # regardless of current state (does not consult detect / skip).
    alpha, _, _ = fakes
    alpha.done = True
    assert review.request_main(["7", "--reviewer", "alpha"]) == 0
    assert alpha.requests == [7]  # forced despite being done


def test_cancel_dispatches_through_the_same_interface(fakes, capsys):
    alpha, beta, _ = fakes
    assert review.cancel_main(["9"]) == 0
    assert alpha.cancels == [9]
    assert beta.cancels == [9]


def test_request_through_real_registry_reaches_gh_boundary(monkeypatch, capsys):
    # End-to-end through the REAL registry: a bare request selects the
    # config-resolved required SET (default [copilot]); an explicit
    # `--reviewer coderabbit` (the phos-pilot opt-in path) dispatches through
    # the SAME generic ghapi.pr_edit_reviewer edge — proving a second
    # requestable reviewer attaches as a real review_requested edge.
    monkeypatch.setattr(
        review,
        "attach_state",
        lambda pr: (["Copilot", "coderabbitai[bot]"], []),
    )
    # The bare path skips done reviewers via the light gather_reviews; a context
    # with NO reviews means copilot is not-yet-done, so it IS requested (the
    # behaviour this test asserts).
    monkeypatch.setattr(
        review, "gather_reviews", lambda pr: PullContext(number=pr, head_sha="h", is_draft=True)
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        ghapi, "pr_edit_reviewer", lambda pr, reviewer, remove=False: calls.append((pr, reviewer))
    )
    assert review.request_main(["42"]) == 0
    assert calls == [(42, "@copilot")]
    assert review.request_main(["42", "--reviewer", "coderabbit"]) == 0
    assert calls == [(42, "@copilot"), (42, "coderabbitai[bot]")]
    out = capsys.readouterr().out
    assert "copilot" in out
    assert "coderabbit" in out


# --- attach verification (release#614) ----------------------------------------
#
# GitHub can accept the request call yet silently drop the review_requested
# edge (service stall / quota). The verb's postcondition is "the edge exists":
# it polls after placing, and a dropped attach fails loud instead of letting
# the loop wait on a review that was never requested.


def test_attach_verified_on_first_check_exits_zero_without_sleeping(
    monkeypatch, fakes, sleeps, capsys
):
    calls = _script_attach(monkeypatch, [([], []), (["alpha[bot]", "beta[bot]"], [])])
    assert review.request_main(["7"]) == 0
    assert sleeps == []  # the edge was there on the first check
    assert calls == [7, 7]  # baseline + one verification check
    out = capsys.readouterr().out
    assert "verified: alpha request attached on #7" in out
    assert "verified: beta request attached on #7" in out


def test_dropped_attach_fails_loud(monkeypatch, fakes, sleeps, capsys):
    # The live outage: every channel returns success, reviewRequests stays [].
    calls = _script_attach(monkeypatch, [([], [])])
    assert review.request_main(["7"]) == 1
    err = capsys.readouterr().err
    assert "alpha: request dropped by GitHub: no review_requested edge created" in err
    assert "service stall / quota" in err and "retry later" in err
    # The poll exhausts: baseline + ATTACH_VERIFY_CHECKS checks, sleeping between checks.
    assert calls == [7] * (1 + review.ATTACH_VERIFY_CHECKS)
    assert sleeps == [review.ATTACH_VERIFY_INTERVAL_SECONDS] * (review.ATTACH_VERIFY_CHECKS - 1)


def test_attach_appearing_on_a_later_check_verifies_after_sleeping(
    monkeypatch, fakes, sleeps, capsys
):
    _script_attach(monkeypatch, [([], []), ([], []), (["alpha[bot]"], [])])
    assert review.request_main(["7", "--reviewer", "alpha"]) == 0
    assert sleeps == [review.ATTACH_VERIFY_INTERVAL_SECONDS]
    assert "verified: alpha request attached on #7" in capsys.readouterr().out


def test_review_consumed_before_poll_counts_as_verified(monkeypatch, fakes, sleeps, capsys):
    # A fast bot can consume the request before the poll sees the edge: a
    # review NOT in the pre-request baseline counts as a verified attach.
    _script_attach(
        monkeypatch,
        [
            ([], [(1, "alpha[bot]")]),  # baseline: one old review
            ([], [(1, "alpha[bot]"), (2, "alpha[bot]")]),  # request already consumed
        ],
    )
    assert review.request_main(["7", "--reviewer", "alpha"]) == 0
    assert "verified: alpha request attached on #7" in capsys.readouterr().out
    assert sleeps == []


def test_stale_baseline_review_does_not_verify(monkeypatch, fakes, sleeps, capsys):
    # A review that predates the request must NOT count — only a fresh one can
    # have consumed the request.
    _script_attach(monkeypatch, [([], [(1, "alpha[bot]")])])
    assert review.request_main(["7", "--reviewer", "alpha"]) == 1
    assert "request dropped by GitHub" in capsys.readouterr().err


def test_partial_drop_fails_and_names_only_the_dropped_reviewer(monkeypatch, fakes, sleeps, capsys):
    _script_attach(monkeypatch, [([], []), (["alpha[bot]"], [])])
    assert review.request_main(["7"]) == 1
    out, err = capsys.readouterr()
    assert "verified: alpha request attached on #7" in out
    assert "beta: request dropped by GitHub" in err
    assert "alpha: request dropped" not in err


def test_help_documents_the_verified_contract(capsys):
    assert review.request_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    assert "review_requested edge" in out


# --- show ---------------------------------------------------------------------


def _show_ctx() -> PullContext:
    return PullContext(
        number=3,
        head_sha="head",
        is_draft=True,
        reviews=[
            Review(1, "alpha[bot]", "COMMENTED", "old", "alpha says: earlier pass"),
            Review(2, "alpha[bot]", "COMMENTED", "head", "alpha says: current pass"),
            Review(3, "beta[bot]", "COMMENTED", "head", "beta says: hi"),
        ],
    )


def test_show_prints_only_selected_reviewers_reviews(fakes, capsys):
    alpha, _, _ = fakes
    review.render_show(_show_ctx(), [alpha])
    out = capsys.readouterr().out
    assert "alpha says: current pass" in out
    assert "beta says" not in out
    assert "[stale: earlier head]" in out  # the old-head review is marked


def test_show_prints_all_threads_regardless_of_author(fakes, context, capsys):
    # Thread accounting is reviewer-agnostic (post-#515): every thread prints,
    # whoever opened it, even when scoped to one reviewer's reviews.
    alpha, _, _ = fakes
    ctx = context("multi_bot_threads")
    review.render_show(ctx, [alpha])
    out = capsys.readouterr().out
    assert out.count("graphql thread id ") == len(ctx.threads)


def test_show_labels_the_ids_so_the_consumable_one_is_unambiguous(fakes, context, capsys):
    # #687: each thread prints a GraphQL node id (PRRT_…) AND a numeric comment
    # id; the output must LABEL them so it's clear the numeric comment-id is the
    # one `pr resolve-thread` / `pr review reply` consume.
    alpha, _, _ = fakes
    ctx = context("multi_bot_threads")
    review.render_show(ctx, [alpha])
    out = capsys.readouterr().out
    assert "graphql thread id " in out
    assert "comment-id " in out
    # the legend points at the consumable verbs + the numeric comment id
    assert "resolve-thread" in out
    assert "reply" in out


# --- reply (#695) -------------------------------------------------------------


@pytest.fixture
def reply_calls(monkeypatch):
    """Record `ghapi.pr_review_reply(pr, comment_id, body)` calls."""
    calls: list[tuple[int, int, str]] = []
    monkeypatch.setattr(
        ghapi, "pr_review_reply", lambda pr, comment_id, body: calls.append((pr, comment_id, body))
    )
    return calls


def test_reply_help_exits_zero(capsys):
    assert review.reply_main(["--help"]) == 0
    assert "pr review reply" in capsys.readouterr().out


def test_reply_posts_to_resolved_pr_with_explicit_pr(reply_calls, capsys):
    assert review.reply_main(["3418556677", "I disagree, here's why", "--pr", "42"]) == 0
    assert reply_calls == [(42, 3418556677, "I disagree, here's why")]
    assert "replied to comment 3418556677 on #42" in capsys.readouterr().out


def test_reply_resolves_pr_from_current_branch(monkeypatch, reply_calls, capsys):
    monkeypatch.setattr(ghapi, "_gh", lambda args, **kwargs: '{"number": 12}')
    assert review.reply_main(["999", "ack"]) == 0
    assert reply_calls == [(12, 999, "ack")]


def test_reply_needs_comment_id_and_body(capsys):
    assert review.reply_main(["999"]) == 64
    assert "needs a <comment-id> and a <body>" in capsys.readouterr().err


def test_reply_comment_id_must_be_numeric(capsys):
    assert review.reply_main(["PRRT_kwDO", "body"]) == 64
    assert "comment id must be numeric" in capsys.readouterr().err


def test_reply_too_many_args_is_usage_error(capsys):
    assert review.reply_main(["1", "body", "extra"]) == 64
    assert "too many arguments" in capsys.readouterr().err


def test_reply_gh_failure_is_exit_1(monkeypatch, capsys):
    def _boom(pr, comment_id, body):
        raise ghapi.GhError("gh api failed (1): nope")

    monkeypatch.setattr(ghapi, "pr_review_reply", _boom)
    assert review.reply_main(["1", "body", "--pr", "5"]) == 1
    assert "nope" in capsys.readouterr().err


def test_reply_body_starting_with_dash_is_not_an_option(reply_calls):
    # A reply body that begins with `-` (a markdown list item) is body, not an
    # unknown option — the dash-prefixed token follows the <comment-id> (#733).
    assert review.reply_main(["7", "- one\n- two", "--pr", "9"]) == 0
    assert reply_calls == [(9, 7, "- one\n- two")]


def test_reply_body_starting_with_double_dash_is_not_an_option(reply_calls):
    # A `--`-prefixed body (a diff hunk header) is body once the comment-id is set.
    assert review.reply_main(["7", "--- a/file.py", "--pr", "9"]) == 0
    assert reply_calls == [(9, 7, "--- a/file.py")]


def test_reply_leading_unknown_option_before_comment_id_still_errors(capsys):
    # Before the positional <comment-id>, an unknown dash option is still rejected.
    assert review.reply_main(["--bogus", "7", "body"]) == 64
    assert "unknown option --bogus" in capsys.readouterr().err
