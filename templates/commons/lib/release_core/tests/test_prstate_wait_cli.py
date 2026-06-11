"""`pr wait` — the engine-owned wait (release#503).

One wait for the whole loop: it polls the state engine and returns the moment
the snapshot calls for agent action. The engine evaluation itself is covered
by test_prstate_state.py; here the loop is driven on scripted TaskStatus
sequences with an injected clock/sleep — immediate-exit states, the
REVIEWS_PENDING request-vs-wait split, polling through waiting-state
transitions, cadence (backoff + --poll/--timeout), the timeout exit, and the
landing-during-final-sleep regression carried over from the retired
`pr review wait`.
"""

from __future__ import annotations

import pytest
from release_core.prstate.cli import wait
from release_core.prstate.ghapi import GhError
from release_core.prstate.state import TaskState, TaskStatus


class _Reviewer:
    """The two adapter fields the wait classifier reads: name + required."""

    def __init__(self, name: str, *, required: bool = True):
        self.name = name
        self.required = required


REGISTRY = [_Reviewer("alpha"), _Reviewer("beta", required=False)]


def _status(state: TaskState, *, reviewers: dict[str, str] | None = None) -> TaskStatus:
    return TaskStatus(
        state=state,
        next_action=f"next action for {state.value}",
        pr=5,
        reviewers=reviewers or {},
    )


PENDING_WAITING = _status(TaskState.REVIEWS_PENDING, reviewers={"alpha": "requested"})
PENDING_NEEDS_REQUEST = _status(TaskState.REVIEWS_PENDING, reviewers={"alpha": "not_requested"})
VALIDATING = _status(TaskState.VALIDATING)
READY = _status(TaskState.READY)


class _Harness:
    """Scripted snapshots + a fake clock advanced only by sleeping."""

    def __init__(self, *statuses: TaskStatus):
        self.statuses = list(statuses)
        self.now = 0.0
        self.sleeps: list[float] = []

    def snapshot(self, pr: int) -> TaskStatus:
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def run(self, **kwargs) -> int:
        return wait.wait_for_action(
            5,
            registry=REGISTRY,
            snapshot=self.snapshot,
            sleep=self.sleep,
            clock=self.clock,
            **kwargs,
        )


# --- immediate exit: agent action is already available ------------------------


@pytest.mark.parametrize("state", [TaskState.ADDRESSING, TaskState.BLOCKED, TaskState.READY])
def test_agent_action_states_exit_immediately(state, capsys):
    h = _Harness(_status(state))
    assert h.run() == 0
    assert h.sleeps == []  # no waiting at all
    out = capsys.readouterr().out
    assert state.value.upper() in out  # the pr-status rendering
    assert "next action" in out


def test_reviews_pending_needing_a_request_is_agent_action(capsys):
    # The review still has to be (re-)requested on the head — placing the
    # request is the agent's move, so there is nothing to wait for.
    h = _Harness(PENDING_NEEDS_REQUEST)
    assert h.run() == 0
    assert h.sleeps == []


def test_done_required_reviewer_does_not_read_as_needs_request():
    # alpha done + nothing pending: not REVIEWS_PENDING territory at all, but
    # the classifier must also not trip on the done lifecycle value.
    status = _status(TaskState.REVIEWS_PENDING, reviewers={"alpha": "done_clean"})
    assert wait._needs_request(status, REGISTRY) is False


def test_best_effort_reviewer_never_forces_a_request():
    # beta (best-effort) absent/not_requested must not read as agent action.
    status = _status(
        TaskState.REVIEWS_PENDING,
        reviewers={"alpha": "in_progress", "beta": "not_requested"},
    )
    assert wait._needs_request(status, REGISTRY) is False
    assert wait._agent_action_needed(status, REGISTRY) is False


# --- the waiting loop ----------------------------------------------------------


def test_waits_through_pending_until_agent_action(capsys):
    h = _Harness(PENDING_WAITING, PENDING_WAITING, READY)
    assert h.run() == 0
    assert len(h.sleeps) == 2
    out = capsys.readouterr().out
    assert "waiting [reviews_pending]" in out
    assert "READY" in out


def test_crosses_waiting_states_in_one_call(capsys):
    # Review lands while CI still runs: REVIEWS_PENDING -> VALIDATING is a
    # state change but NOT agent action — the wait reports it and keeps going.
    h = _Harness(PENDING_WAITING, VALIDATING, VALIDATING, READY)
    assert h.run() == 0
    out = capsys.readouterr().out
    assert "waiting [reviews_pending]" in out
    assert "waiting [validating]" in out
    assert "READY" in out


def test_addressing_mid_wait_exits_zero(capsys):
    # A review that lands WITH comments flips to ADDRESSING — agent action.
    h = _Harness(PENDING_WAITING, _status(TaskState.ADDRESSING))
    assert h.run() == 0
    assert "ADDRESSING" in capsys.readouterr().out


def test_output_comes_from_the_engine_not_hardcoded_names(capsys):
    # The wait's own lines carry the engine's state + next_action verbatim —
    # it never composes reviewer/check names itself.
    h = _Harness(PENDING_WAITING, READY)
    assert h.run() == 0
    out = capsys.readouterr().out
    assert "next action for reviews_pending" in out
    assert "next action for ready" in out


# --- cadence: data, overridable ------------------------------------------------


def test_default_cadence_backs_off_to_the_cap():
    h = _Harness(PENDING_WAITING, *([VALIDATING] * 10), READY)
    assert h.run() == 0
    assert h.sleeps[0] == wait.POLL_INITIAL_S
    assert h.sleeps[1] == wait.POLL_INITIAL_S * wait.POLL_BACKOFF
    assert max(h.sleeps) <= wait.POLL_MAX_S
    assert wait.POLL_MAX_S in h.sleeps  # the cap is reached and held


def test_poll_flag_fixes_the_interval_no_backoff():
    h = _Harness(PENDING_WAITING, VALIDATING, VALIDATING, READY)
    assert h.run(poll=30.0) == 0
    assert h.sleeps == [30.0, 30.0, 30.0]


def test_timeout_cap_hits_exit_2(capsys):
    h = _Harness(VALIDATING)
    assert h.run(poll=30.0, timeout=100.0) == 2
    err = capsys.readouterr().err
    assert "timeout" in err
    assert "validating" in err  # says what it was still waiting on
    # The final sleep is capped to the remaining budget — the wall clock never
    # overshoots --timeout (Copilot review on #561).
    assert h.sleeps == [30.0, 30.0, 30.0, 10.0]
    assert h.now == 100.0


def test_sleep_never_exceeds_the_remaining_timeout():
    # --poll larger than --timeout must not block past the cap.
    h = _Harness(VALIDATING)
    assert h.run(poll=600.0, timeout=60.0) == 2
    assert h.sleeps == [60.0]


def test_wait_catches_state_landing_during_final_sleep(capsys):
    # Agent action that arrives during the sleep that carries the clock past
    # the deadline must NOT read as a timeout (the retired review-wait's
    # regression, carried over): the loop re-checks before declaring timeout.
    h = _Harness(VALIDATING)
    h.statuses = [VALIDATING]

    def snapshot(pr: int) -> TaskStatus:
        return READY if h.now >= 100.0 else VALIDATING

    assert (
        wait.wait_for_action(
            5,
            registry=REGISTRY,
            snapshot=snapshot,
            sleep=h.sleep,
            clock=h.clock,
            poll=30.0,
            timeout=100.0,
        )
        == 0
    )
    assert "READY" in capsys.readouterr().out


# --- main(): argv + resolution + errors -----------------------------------------


def test_help_exits_zero(capsys):
    assert wait.main(["--help"]) == 0
    assert "pr wait" in capsys.readouterr().out


def test_nonnumeric_pr_is_usage_error(capsys):
    assert wait.main(["abc"]) == 64
    assert "numeric" in capsys.readouterr().err


def test_unknown_option_is_usage_error(capsys):
    assert wait.main(["--nope"]) == 64
    assert "unknown option" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--poll", "--timeout"])
def test_cadence_flags_need_a_positive_number(flag, capsys):
    assert wait.main(["5", flag]) == 64
    assert wait.main(["5", f"{flag}=abc"]) == 64
    assert wait.main(["5", flag, "0"]) == 64
    assert wait.main(["5", f"{flag}=-3"]) == 64


def test_cadence_flags_reach_the_loop(monkeypatch):
    seen: dict = {}

    def fake_wait(pr, *, poll, timeout):
        seen.update(pr=pr, poll=poll, timeout=timeout)
        return 0

    monkeypatch.setattr(wait, "wait_for_action", fake_wait)
    assert wait.main(["7", "--poll", "15", "--timeout=600"]) == 0
    assert seen == {"pr": 7, "poll": 15.0, "timeout": 600.0}


def test_no_pr_for_branch_is_agent_action_exit_0(monkeypatch, capsys):
    monkeypatch.setattr(wait, "_resolve_pr", lambda: None)
    assert wait.main([]) == 0
    out = capsys.readouterr().out
    assert "NO_PR" in out
    assert "create a draft PR" in out


def test_resolver_maps_only_the_no_pr_answer_to_none(monkeypatch):
    # gh's "no pull requests found" -> None (the NO_PR state)...
    def no_pr_found(args, **kwargs):
        raise GhError('gh pr view failed (1): no pull requests found for branch "x"')

    monkeypatch.setattr(wait.ghapi, "_gh", no_pr_found)
    assert wait._resolve_pr() is None

    # ...but any other gh failure propagates (auth, missing gh, API flake).
    def auth_down(args, **kwargs):
        raise GhError("gh pr view failed (1): not logged in")

    monkeypatch.setattr(wait.ghapi, "_gh", auth_down)
    with pytest.raises(GhError, match="not logged in"):
        wait._resolve_pr()


def test_gh_failure_resolving_the_pr_is_exit_1_not_no_pr(monkeypatch, capsys):
    # With <pr> omitted, a real gh failure must surface as exit 1 with gh's
    # message — never read as NO_PR/exit 0 (Copilot review on #561).
    def auth_down(args, **kwargs):
        raise GhError("gh pr view failed (1): not logged in")

    monkeypatch.setattr(wait.ghapi, "_gh", auth_down)
    assert wait.main([]) == 1
    assert "not logged in" in capsys.readouterr().err


def test_gh_failure_is_exit_1(monkeypatch, capsys):
    def boom(pr: int) -> TaskStatus:
        raise GhError("gh exploded")

    monkeypatch.setattr(wait, "_snapshot", boom)
    assert wait.main(["5"]) == 1
    assert "gh exploded" in capsys.readouterr().err
