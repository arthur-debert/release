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
from release_core.prstate import ghapi, gitstat
from release_core.prstate.cli import ready
from release_core.prstate.state import TaskState


@pytest.fixture
def flips(monkeypatch):
    """Record `gh pr ready` calls instead of shelling out."""
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(ghapi, "pr_ready", lambda pr, undo=False: calls.append((pr, undo)))
    return calls


@pytest.fixture
def engine(monkeypatch, context):
    """Point `ready`'s gather at a recorded scenario; skip the git diff-sizer."""
    monkeypatch.setattr(gitstat, "diff_sizer", lambda base_ref: None)

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
    # gather must not even run: re-work is allowed from ANY state.
    def _no_gather(pr):
        raise AssertionError("--undo must not read the engine")

    monkeypatch.setattr(ready, "gather", _no_gather)
    assert ready.main(["33", "--undo"]) == 0
    assert flips == [(33, True)]
    assert "ready -> draft" in capsys.readouterr().out


def test_undo_gh_failure_is_exit_1(monkeypatch, capsys):
    def _down(pr, undo=False):
        raise ghapi.GhError("gh pr ready failed (1): nope")

    monkeypatch.setattr(ghapi, "pr_ready", _down)
    assert ready.main(["33", "--undo"]) == 1
    assert "nope" in capsys.readouterr().err


# --- the READY next-action names this command ----------------------------------


def test_ready_next_action_names_the_command(context):
    from release_core.prstate.state import evaluate

    status = evaluate(context("ready_checks_green"))
    assert status.state is TaskState.READY
    assert "release-core pr ready" in status.next_action
