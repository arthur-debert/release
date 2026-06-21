"""The `pr ready` guarded flip (#456) — the engine owns draft<->ready.

The forward flip is gated on the state engine reading READY: every other
state refuses with exit 1, no mutation, and the engine's own status + next
action printed (the agent learns what to do instead). `--undo` is the
unconditional reverse flip. The gh boundary (`ghapi.pr_ready`) is faked
throughout, the same way the #558 act-side tests fake `pr_edit_reviewer`;
the engine input comes from the recorded scenario fixtures via `context`.
"""

from __future__ import annotations

import pytest
from release_core.prstate import ghapi
from release_core.prstate.cli import ready
from release_core.prstate.model import PullContext, Review, ReviewComment, Thread
from release_core.prstate.reviewers import by_name
from release_core.prstate.state import TaskState


def _capped_ctx(*, threads, is_draft=True, merge_state="CLEAN"):
    """An otherwise-ready PR that has reached the 6-round cap.

    Built directly (not from a recorded fixture) because the cap needs six
    review rounds (one head SHA each).
    """
    reviews = [
        Review(review_id=i, author="Copilot", state="COMMENTED", commit_id=f"c{i}", body="")
        for i in range(1, 7)
    ]
    findings = [
        Thread(
            thread_id=f"PRT_{i}",
            is_resolved=True,
            comments=(
                ReviewComment(
                    comment_id=i,
                    path=f"f{i}.py",
                    line=i,
                    body="real fix",
                    author="Copilot",
                    review_id=i,
                ),
            ),
        )
        for i in range(1, 7)
    ]
    return PullContext(
        number=738,
        head_sha="c6",
        is_draft=is_draft,
        base_ref="main",
        mergeable="MERGEABLE",
        merge_state=merge_state,
        reviews=reviews,
        threads=[*findings, *threads],
        checks=[{"status": "COMPLETED", "conclusion": "SUCCESS"}],
    )


@pytest.fixture
def capped(monkeypatch):
    """Point `ready` at a directly-built capped context + Copilot-only required."""
    monkeypatch.setattr(ready, "required_reviewers", lambda: [by_name("copilot")])

    def use(ctx):
        monkeypatch.setattr(ready, "gather", lambda pr: ctx)
        return ctx

    return use


@pytest.fixture
def flips(monkeypatch):
    """Record `gh pr ready` calls instead of shelling out."""
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(ghapi, "pr_ready", lambda pr, undo=False: calls.append((pr, undo)))
    return calls


@pytest.fixture
def engine(monkeypatch, context):
    """Point `ready`'s gather at a recorded scenario."""

    def use(name: str, *, draft: bool | None = None):
        ctx = context(name)
        if draft is not None:
            ctx.is_draft = draft
        monkeypatch.setattr(ready, "gather", lambda pr: ctx)
        return ctx

    return use


# --- argv contract (offline) ------------------------------------------------


def test_help_exits_zero(capsys):
    assert ready.main(["--help"]) == 0
    assert "pr ready" in capsys.readouterr().out


def test_nonnumeric_pr_is_usage_error(capsys):
    assert ready.main(["abc"]) == 64
    assert "numeric" in capsys.readouterr().err


def test_unknown_option_is_usage_error(capsys):
    assert ready.main(["--force"]) == 64
    assert "unknown option" in capsys.readouterr().err


def test_too_many_arguments_is_usage_error(capsys):
    assert ready.main(["1", "2"]) == 64
    assert "too many" in capsys.readouterr().err


def test_gh_failure_resolving_the_pr_is_exit_1_not_usage(monkeypatch, capsys):
    def _gh_down(args, **kwargs):
        raise ghapi.GhError("gh pr view failed (1): not logged in")

    monkeypatch.setattr(ghapi, "_gh", _gh_down)
    assert ready.main([]) == 1
    assert "not logged in" in capsys.readouterr().err


def test_omitted_pr_resolves_from_current_branch(monkeypatch, engine, flips, capsys):
    monkeypatch.setattr(ghapi, "_gh", lambda args, **kwargs: '{"number": 12}')
    engine("ready_checks_green")
    assert ready.main([]) == 0
    assert flips == [(12, False)]


# --- the guarded forward flip -------------------------------------------------


def test_ready_draft_pr_flips_and_hands_to_the_human(engine, flips, capsys):
    engine("ready_checks_green")  # READY, isDraft: true
    assert ready.main(["201"]) == 0
    assert flips == [(201, False)]
    out = capsys.readouterr().out
    assert "draft -> ready" in out
    assert "handed to the human" in out


def test_flip_warns_that_checks_rerun_on_the_ready_event(engine, flips, capsys):
    # GitHub re-runs checks on ready_for_review, so status reads VALIDATING
    # right after a successful flip. The flip output must set that expectation
    # itself (#564) — the engine stays a truthful clockless snapshot.
    engine("ready_checks_green")
    assert ready.main(["201"]) == 0
    out = capsys.readouterr().out
    assert "re-runs checks" in out
    assert "VALIDATING" in out
    assert "release-core pr wait" in out
    # #703: the VALIDATING note must read as expected/benign, not an error or a
    # mandatory extra round — frame it as normal and "no further action needed".
    assert "normal and expected" in out
    assert "No further action is needed" in out


def test_already_ready_pr_is_idempotent_success(engine, flips, capsys):
    engine("ready_checks_green", draft=False)  # READY, flag already flipped
    assert ready.main(["201"]) == 0
    assert flips == []  # nothing to mutate
    assert "already ready-for-review" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("fixture", "state"),
    [
        ("gemini_eyes_copilot_requested", TaskState.REVIEWS_PENDING),
        ("copilot_changes_requested", TaskState.ADDRESSING),
        ("reviewed_mergeable_unknown", TaskState.REVIEWED),
        ("validating_checks_pending", TaskState.VALIDATING),
        ("blocked_checks_failing", TaskState.BLOCKED),
        ("blocked_merge_conflict", TaskState.BLOCKED),
    ],
)
def test_every_non_ready_state_refuses_with_exit_1(engine, flips, capsys, fixture, state):
    ctx = engine(fixture)
    assert ready.main([str(ctx.number)]) == 1
    assert flips == []  # refusal never mutates
    captured = capsys.readouterr()
    assert "refusing to flip" in captured.err
    assert state.value.upper() in captured.err


def test_refusal_surfaces_the_engines_next_action(engine, flips, capsys):
    engine("copilot_changes_requested")  # ADDRESSING: 1 open thread
    assert ready.main(["101"]) == 1
    out = capsys.readouterr().out
    # the engine's own rendering: state + the one next action
    assert "ADDRESSING" in out
    assert "1 open thread" in out


def test_gh_failure_during_the_flip_is_exit_1(monkeypatch, engine, capsys):
    engine("ready_checks_green")

    def _down(pr, undo=False):
        raise ghapi.GhError("gh pr ready failed (1): boom")

    monkeypatch.setattr(ghapi, "pr_ready", _down)
    assert ready.main(["201"]) == 1
    assert "boom" in capsys.readouterr().err


# --- --undo: the unconditional reverse flip ------------------------------------


def test_undo_flips_back_without_consulting_the_engine(monkeypatch, flips, capsys):
    # gather must not even run: re-work is allowed from ANY state. Only the
    # cheap draft-flag metadata read happens (output truthfulness, not gating).
    def _no_gather(pr):
        raise AssertionError("--undo must not read the engine")

    monkeypatch.setattr(ready, "gather", _no_gather)
    monkeypatch.setattr(ghapi, "pr_meta", lambda pr: {"isDraft": False})
    assert ready.main(["33", "--undo"]) == 0
    assert flips == [(33, True)]
    assert "ready -> draft" in capsys.readouterr().out


def test_undo_on_an_already_draft_pr_is_a_truthful_noop(monkeypatch, flips, capsys):
    monkeypatch.setattr(ghapi, "pr_meta", lambda pr: {"isDraft": True})
    assert ready.main(["33", "--undo"]) == 0
    assert flips == []  # no mutation, and no false "ready -> draft" claim
    assert "already a draft" in capsys.readouterr().out


def test_undo_gh_failure_is_exit_1(monkeypatch, capsys):
    def _down(pr, undo=False):
        raise ghapi.GhError("gh pr ready failed (1): nope")

    monkeypatch.setattr(ghapi, "pr_meta", lambda pr: {"isDraft": False})
    monkeypatch.setattr(ghapi, "pr_ready", _down)
    assert ready.main(["33", "--undo"]) == 1
    assert "nope" in capsys.readouterr().err


# --- the READY next-action names this command ----------------------------------


def test_ready_next_action_names_the_command(context):
    from release_core.prstate.state import evaluate

    status = evaluate(context("ready_checks_green"))
    assert status.state is TaskState.READY
    assert "release-core pr ready" in status.next_action


# --- the stopping rule: a capped PR flips to READY with no ack flag -----------


def test_no_ack_flag_in_help(capsys):
    # The --ack-cycle-cap escape is gone; the stopping rule routes to READY.
    assert ready.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--ack-cycle-cap" not in out
    assert "--undo" in out


def test_ack_flag_is_now_an_unknown_option(capsys):
    assert ready.main(["--ack-cycle-cap"]) == 64
    assert "unknown option" in capsys.readouterr().err


def test_capped_pr_flips_to_ready_without_any_flag(capped, flips, capsys):
    # 6 rounds reached on an otherwise-ready PR: the engine routes to READY, so
    # a plain `pr ready` flips it — no acknowledgement step.
    capped(_capped_ctx(threads=[]))
    assert ready.main(["738"]) == 0
    assert flips == [(738, False)]
    assert "draft -> ready" in capsys.readouterr().out


def test_capped_pr_with_open_nitpick_thread_still_flips(capped, flips, capsys):
    # An open thread that the stopping rule chose not to open a 7th round for
    # does NOT block the flip on an otherwise-ready PR.
    open_thread = Thread(
        thread_id="PRT_open",
        is_resolved=False,
        comments=(
            ReviewComment(comment_id=99, path="z.py", line=1, body="nit: tidy", author="Copilot"),
        ),
    )
    capped(_capped_ctx(threads=[open_thread]))
    assert ready.main(["738"]) == 0
    assert flips == [(738, False)]


def test_capped_pr_with_merge_conflict_refuses(capped, flips, capsys):
    # The stopping rule never bypasses a real blocker: a DIRTY merge state still
    # refuses the flip.
    capped(_capped_ctx(threads=[], merge_state="DIRTY"))
    assert ready.main(["738"]) == 1
    assert flips == []
    assert "refusing to flip" in capsys.readouterr().err


def test_capped_pr_already_ready_is_a_noop(capped, flips, capsys):
    capped(_capped_ctx(threads=[], is_draft=False))
    assert ready.main(["738"]) == 0
    assert flips == []
    assert "already ready-for-review" in capsys.readouterr().out
