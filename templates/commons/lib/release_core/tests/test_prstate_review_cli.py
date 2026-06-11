"""The `pr review` act surface (release#555) — registry-driven, no name branching.

Covers the CLI contract offline (usage errors, --reviewer selection, no-op
backends) and the wait loop with injected clock/sleep. The CLI layer is
exercised against FAKE adapters to prove it never branches on a reviewer's
name — selection and mechanics flow entirely through the adapter interface —
plus one test through the REAL registry down to the (mocked) gh boundary.
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


# --- argv contract (offline) ------------------------------------------------


@pytest.mark.parametrize(
    "main", [review.request_main, review.cancel_main, review.wait_main, review.show_main]
)
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


def test_unknown_reviewer_is_usage_error(fakes, capsys):
    assert review.request_main(["1", "--reviewer", "nosuchbot"]) == 64
    err = capsys.readouterr().err
    assert "unknown reviewer" in err
    assert "alpha" in err  # the known set is named


# --- request / cancel dispatch ----------------------------------------------


def test_request_defaults_to_all_required_reviewers(fakes, capsys):
    alpha, beta, gamma = fakes
    assert review.request_main(["7"]) == 0
    assert alpha.requests == [7]
    assert beta.requests == [7]
    assert gamma.requests == []  # best-effort: not in the default scope
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out


def test_reviewer_flag_selects_one_adapter(fakes, capsys):
    alpha, beta, _ = fakes
    assert review.request_main(["7", "--reviewer", "beta"]) == 0
    assert beta.requests == [7]
    assert alpha.requests == []


def test_noop_backend_reports_noop_and_succeeds(fakes, capsys):
    _, _, gamma = fakes
    assert review.request_main(["7", "--reviewer", "gamma"]) == 0
    assert gamma.requests == [7]
    assert "no-op" in capsys.readouterr().out


def test_cancel_dispatches_through_the_same_interface(fakes, capsys):
    alpha, beta, _ = fakes
    assert review.cancel_main(["9"]) == 0
    assert alpha.cancels == [9]
    assert beta.cancels == [9]


def test_request_through_real_registry_reaches_gh_boundary(monkeypatch, capsys):
    # End-to-end through the REAL registry: the CLI selects the required set
    # (copilot) and the adapter places the request via ghapi.pr_edit_reviewer.
    calls: list[tuple] = []
    monkeypatch.setattr(
        ghapi, "pr_edit_reviewer", lambda pr, reviewer, remove=False: calls.append((pr, reviewer))
    )
    assert review.request_main(["42"]) == 0
    assert calls == [(42, "@copilot")]
    assert "copilot" in capsys.readouterr().out


# --- wait --------------------------------------------------------------------


def _wait_harness(monkeypatch, fakes, *, done_after_polls: int | None):
    """Drive wait_for_reviews with a fake clock; flip the adapters to done
    after `done_after_polls` gathers (None = never)."""
    alpha, beta, _ = fakes
    state = {"now": 0.0, "gathers": 0}

    def gather(pr: int) -> PullContext:
        state["gathers"] += 1
        if done_after_polls is not None and state["gathers"] > done_after_polls:
            alpha.done = beta.done = True
        return PullContext(number=pr, head_sha="h", is_draft=True)

    monkeypatch.setattr(review, "gather", gather)
    return state, (lambda s: state.__setitem__("now", state["now"] + s)), (lambda: state["now"])


def test_wait_returns_immediately_when_already_done(monkeypatch, fakes, capsys):
    alpha, beta, _ = fakes
    alpha.done = beta.done = True
    state, sleep, clock = _wait_harness(monkeypatch, fakes, done_after_polls=None)
    assert review.wait_for_reviews(5, [alpha, beta], sleep=sleep, clock=clock) == 0
    assert state["now"] == 0  # no sleeping at all
    assert "already in" in capsys.readouterr().out


def test_wait_polls_until_done(monkeypatch, fakes, capsys):
    alpha, beta, _ = fakes
    state, sleep, clock = _wait_harness(monkeypatch, fakes, done_after_polls=2)
    assert review.wait_for_reviews(5, [alpha, beta], sleep=sleep, clock=clock) == 0
    # initial 7m sleep happened, then 2m polls until the flip
    assert state["now"] >= review.INITIAL_SLEEP_S


def test_wait_times_out_with_exit_2(monkeypatch, fakes, capsys):
    alpha, beta, _ = fakes
    state, sleep, clock = _wait_harness(monkeypatch, fakes, done_after_polls=None)
    assert review.wait_for_reviews(5, [alpha, beta], sleep=sleep, clock=clock) == 2
    captured = capsys.readouterr()
    assert "timeout" in captured.err
    assert "alpha" in captured.err and "beta" in captured.err


def test_wait_only_blocks_on_pending_reviewers(monkeypatch, fakes, capsys):
    # One required reviewer already done: the wait names only the pending one.
    alpha, beta, _ = fakes
    alpha.done = True
    state, sleep, clock = _wait_harness(monkeypatch, fakes, done_after_polls=None)
    assert review.wait_for_reviews(5, [alpha, beta], sleep=sleep, clock=clock) == 2
    captured = capsys.readouterr()
    assert "beta" in captured.out
    assert "alpha" not in captured.err  # alpha is not among the timed-out set


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
