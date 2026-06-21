"""`orc watch` — poll a few PRs and act on lifecycle transitions.

The detached, machine-stays-on transport for the PR state engine (release#338).
NOT webhooks: a long-running local loop imports `release_core.prstate.state` and
polls each tracked PR (~zero agent tokens), dispatching only when a PR's
lifecycle *state changes*. See issue #338 for the design.

This module holds the pure decision logic (`decide`, `poll_once`) with all side
effects behind an injected `Sink`, so the transition/dispatch behaviour is
unit-tested with no `gh`, no network, and no Claude Agent SDK. The SDK is
imported lazily, only when an auto-fix agent is actually spawned.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from release_core.prstate.fetch import gather
from release_core.prstate.state import TaskState, TaskStatus, evaluate


class Action(StrEnum):
    """What to do on a transition into a given state."""

    WAIT = "wait"  # log only; keep polling (reviews/checks still in flight)
    NOTIFY = "notify"  # ping the human to come drive (notify-only mode)
    SPAWN_FIXER = "spawn_fixer"  # spawn a fresh auto-fix agent (--auto mode)
    FLIP_READY = "flip_ready"  # gh pr ready + page the human (merge is theirs)


def decide(status: TaskStatus, *, auto: bool) -> Action:
    """Map a PR's lifecycle state to the watcher's action. Pure.

    The merge is the only gate never automated: READY pages the human. Under the
    stopping rule, BLOCKED always means a REAL, fixable blocker (failing CI,
    merge conflict, behind base) — never a "stop everything" breaker. The
    stopping rule (6 rounds / all-nitpick) routes an otherwise-ready PR to READY,
    so `status.breaker` may be set on a BLOCKED status only as recorded metadata
    (`state.evaluate` stamps it before the CI/merge gates); it is NOT a signal to
    page. So a BLOCKED PR spawns a fresh fixer in --auto mode (or notifies in
    pager mode), exactly like ADDRESSING.
    """
    state = status.state
    if state is TaskState.READY:
        return Action.FLIP_READY
    if state in (TaskState.ADDRESSING, TaskState.BLOCKED):
        # BLOCKED is a failing check / merge conflict — fixable; any `breaker`
        # set here is leftover metadata, not a reason to halt.
        return Action.SPAWN_FIXER if auto else Action.NOTIFY
    # REVIEWS_PENDING, VALIDATING, REVIEWED, NO_PR — nothing to do but wait.
    return Action.WAIT


@dataclass
class Sink:
    """Side-effect surface for the watcher — injected so the loop is testable.

    The default no-op base records nothing; `cli` supplies a concrete sink that
    logs, posts desktop notifications, flips draft→ready, and spawns agents.
    """

    def log(self, pr: int, prev: str | None, status: TaskStatus) -> None: ...
    def notify(self, pr: int, status: TaskStatus) -> None: ...
    def flip_ready(self, pr: int, status: TaskStatus) -> None: ...
    def spawn_fixer(self, pr: int, status: TaskStatus) -> None: ...
    def error(self, pr: int, exc: Exception) -> None: ...


def poll_once(
    prs: list[int],
    *,
    get_status: Callable[[int], TaskStatus],
    last_states: dict[int, str],
    sink: Sink,
    auto: bool,
) -> None:
    """One polling pass over `prs`; dispatch only on a state transition.

    `last_states` is mutated in place (the persistent transition memory), so a
    state that hasn't changed since the previous pass is skipped — no
    double-dispatch while a PR sits in the same state.
    """
    for pr in prs:
        try:
            status = get_status(pr)
        except Exception as exc:  # noqa: BLE001
            # A detached daemon must survive transient gh/network/rate-limit
            # failures: log this PR's error, skip it, keep polling the rest.
            sink.error(pr, exc)
            continue
        if last_states.get(pr) == status.state.value:
            continue
        prev = last_states.get(pr)
        sink.log(pr, prev, status)
        _dispatch(pr, status, sink, auto=auto)
        last_states[pr] = status.state.value


def _dispatch(pr: int, status: TaskStatus, sink: Sink, *, auto: bool) -> None:
    action = decide(status, auto=auto)
    if action is Action.NOTIFY:
        sink.notify(pr, status)
    elif action is Action.SPAWN_FIXER:
        sink.spawn_fixer(pr, status)
    elif action is Action.FLIP_READY:
        sink.flip_ready(pr, status)
    # Action.WAIT: the log line in poll_once is enough.


def build_fixer_prompt(pr: int) -> str:
    """Prompt for a FRESH auto-fix agent (not a session-resume — see #338).

    It reads its context (handoff note + spec/issue + gh-task-status), so it
    needs no carried-over session. Pure so the contract is unit-tested.
    """
    return (
        f"Drive PR #{pr} through its review round. This is an unattended "
        "auto-fix run.\n\n"
        f"1. Orient: run `gh-task-status {pr}`. A BLOCKED status is a real, "
        "fixable blocker (failing CI, merge conflict, behind base) — fix it; it "
        "is NOT a stop signal. The stopping rule (6 rounds / all-nitpick) routes "
        "an otherwise-ready PR to READY on its own, so there is no breaker to "
        "halt on here.\n"
        "2. Read context before changing anything: the linked issue/spec and the "
        f"handoff note if present (`.release/handoff-{pr}.md` or a `## Context` "
        "block in the PR body). It records the original author's reasoning and "
        "scope — respect it; don't undo deliberate decisions to satisfy a "
        "comment.\n"
        "3. Triage each open thread per the gh-pr-review-loop skill: fix-and-push "
        "real issues, reply-with-rationale on out-of-scope/wrong ones, resolve "
        "each thread you act on.\n"
        "4. Do NOT merge and do NOT flip draft→ready — the human owns the merge "
        "gate. End with a short status when the threads are handled."
    )


def _status_for(pr: int) -> TaskStatus:
    """Live status for a PR: gather + evaluate."""
    ctx = gather(pr)
    return evaluate(ctx)


def run(
    prs: list[int],
    *,
    sink: Sink,
    auto: bool,
    interval: float,
    last_states: dict[int, str] | None = None,
    get_status: Callable[[int], TaskStatus] = _status_for,
    max_passes: int | None = None,
) -> None:
    """The watch loop: poll every `interval` seconds until interrupted.

    `max_passes` bounds the loop for tests/one-shot use; None runs forever
    (Ctrl-C to stop). `get_status` is injectable for the same reason.
    """
    states = last_states if last_states is not None else {}
    passes = 0
    while max_passes is None or passes < max_passes:
        poll_once(prs, get_status=get_status, last_states=states, sink=sink, auto=auto)
        passes += 1
        if max_passes is not None and passes >= max_passes:
            break
        time.sleep(interval)
