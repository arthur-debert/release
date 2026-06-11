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
    """Scripted snapshots + a fake clock advanced only by sleeping.

    A scripted entry may be an exception instance — the snapshot raises it,
    modelling a poll that fails (transient gh/network blip).
    """

    def __init__(self, *statuses: TaskStatus | Exception):
        self.statuses = list(statuses)
        self.now = 0.0
        self.sleeps: list[float] = []

    def snapshot(self, pr: int) -> TaskStatus:
        item = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(item, Exception):
            raise item
        return item

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


# --- transient poll failures are retried, persistent ones give up (#582) --------


def _blip() -> GhError:
    return GhError("gh api graphql failed (1): net/http: TLS handshake timeout")


def test_transient_poll_failures_are_retried_and_the_wait_survives(capsys):
    # Two consecutive poll failures, then the API recovers: the wait must ride
    # through them and still land on agent action — not die on the first blip.
    h = _Harness(PENDING_WAITING, _blip(), _blip(), READY)
    assert h.run() == 0
    out, err = capsys.readouterr()
    assert "READY" in out
    # Each retried failure is logged to stderr — visible, never silent.
    assert err.count("TLS handshake timeout") == 2
    assert "retry 1/3" in err
    assert "retry 2/3" in err
    # Linear retry backoff (5s, 10s) interleaves with the poll cadence.
    assert wait.POLL_RETRY_BACKOFF_S in h.sleeps
    assert wait.POLL_RETRY_BACKOFF_S * 2 in h.sleeps


def test_persistent_poll_failure_exhausts_retries_and_raises(capsys):
    # More consecutive failures than POLL_RETRIES: the wait gives up and the
    # GhError propagates (main maps it to exit 1, covered below).
    blips = [_blip() for _ in range(wait.POLL_RETRIES + 1)]
    h = _Harness(PENDING_WAITING, *blips, READY)
    with pytest.raises(GhError, match="TLS handshake timeout"):
        h.run()
    err = capsys.readouterr().err
    assert err.count("retry") == wait.POLL_RETRIES  # logged each attempt, then gave up


def test_retry_budget_resets_after_a_successful_poll(capsys):
    # The retry cap is per-poll, not per-wait: a success resets it, so a long
    # wait can absorb more than POLL_RETRIES blips total as long as no single
    # outage exceeds the cap.
    h = _Harness(PENDING_WAITING, _blip(), VALIDATING, _blip(), _blip(), READY)
    assert h.run() == 0
    err = capsys.readouterr().err
    assert "retry 1/3" in err
    assert "retry 2/3" in err
    assert "retry 3/3" not in err


def test_first_fetch_failure_fails_fast_without_retry(capsys):
    # Real errors (bad PR number, auth) surface on the FIRST fetch — retrying
    # them only delays the loud failure, so there is no retry there.
    h = _Harness(_blip(), READY)
    with pytest.raises(GhError, match="TLS handshake timeout"):
        h.run()
    assert h.sleeps == []
    assert "retry" not in capsys.readouterr().err


def test_failure_during_final_post_deadline_look_is_retried(capsys):
    # The last look before declaring timeout goes through the same retry path:
    # a blip there must not kill a wait that is about to report cleanly.
    h = _Harness(VALIDATING, VALIDATING, VALIDATING, VALIDATING, _blip(), READY)
    assert h.run(poll=40.0, timeout=100.0) == 0
    out, err = capsys.readouterr()
    assert "READY" in out
    assert err.count("retry") == 1


# --- cadence: data, overridable ------------------------------------------------


def test_entering_the_wait_surfaces_the_cadence_and_knobs(capsys):
    # The cadence must not be opaque (#564): the first waiting output names
    # the schedule in effect and the --poll/--timeout overrides, once.
    h = _Harness(PENDING_WAITING, READY)
    assert h.run() == 0
    out = capsys.readouterr().out
    assert out.count("cadence:") == 1
    assert "polling every 20s, backing off x1.5 to a 1m30s cap" in out
    assert "--poll" in out
    assert "giving up after 45m" in out
    assert "--timeout" in out


def test_cadence_line_reflects_a_fixed_poll_and_timeout(capsys):
    h = _Harness(PENDING_WAITING, READY)
    assert h.run(poll=30.0, timeout=600.0) == 0
    out = capsys.readouterr().out
    assert "polling every 30s (fixed via --poll)" in out
    assert "giving up after 10m" in out


def test_immediate_exit_prints_no_cadence_line(capsys):
    h = _Harness(READY)
    assert h.run() == 0
    assert "cadence:" not in capsys.readouterr().out


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


def test_sleep_never_exceeds_the_remaining_timeout(capsys):
    # --poll larger than --timeout must not block past the cap — and the
    # progress line reports the clamped next poll, not the raw interval.
    h = _Harness(VALIDATING)
    assert h.run(poll=600.0, timeout=60.0) == 2
    out = capsys.readouterr().out
    assert h.sleeps == [60.0]
    assert "next poll in 1m" in out
    assert "600" not in out


def test_clock_drift_past_deadline_clamps_sleep_at_zero(capsys):
    # A real monotonic clock can advance past the deadline between the loop
    # condition and the remaining-time computation; the sleep must clamp at
    # zero (time.sleep raises ValueError on negatives), then time out cleanly.
    # Readings are scripted (last one repeats): the loop condition reads 50
    # (inside the budget), then the clock has jumped to 150 (past it).
    readings = [0.0, 0.0, 50.0, 150.0]

    def clock() -> float:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    sleeps: list[float] = []
    rc = wait.wait_for_action(
        5,
        registry=REGISTRY,
        snapshot=lambda pr: VALIDATING,
        sleep=sleeps.append,
        clock=clock,
        poll=30.0,
        timeout=100.0,
    )
    assert rc == 2
    assert sleeps == [0.0]
    assert "timeout" in capsys.readouterr().err


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
def test_cadence_flags_need_a_positive_finite_number(flag, capsys):
    assert wait.main(["5", flag]) == 64
    assert wait.main(["5", f"{flag}=abc"]) == 64
    assert wait.main(["5", flag, "0"]) == 64
    assert wait.main(["5", f"{flag}=-3"]) == 64
    assert wait.main(["5", flag, "nan"]) == 64
    assert wait.main(["5", f"{flag}=inf"]) == 64


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
