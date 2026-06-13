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
    assert out.count("(thread ") == len(ctx.threads)
