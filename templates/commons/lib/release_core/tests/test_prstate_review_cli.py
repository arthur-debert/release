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


def test_omitted_pr_resolves_from_current_branch(monkeypatch, fakes, capsys):
    alpha, beta, _ = fakes
    monkeypatch.setattr(ghapi, "_gh", lambda args, **kwargs: '{"number": 12}')
    assert review.request_main([]) == 0
    assert alpha.requests == [12]
    assert beta.requests == [12]


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
