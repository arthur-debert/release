"""The PR lifecycle state machine — the stable core.

`evaluate()` is a pure function from a `PullContext` snapshot to one
`TaskStatus`: where the PR stands and the single next action. It never mutates
(it *reports* READY; the caller does the draft->ready flip) and never branches
on a reviewer's name — it consumes the adapter interface only.

Two definitions anchor it:
  Reviewed = every required reviewer done + every thread resolved.
  Ready    = Reviewed + CI green + a CLEAN merge state. "Mergeable" here means
             `mergeStateStatus == CLEAN` — the authoritative, merge-obeyed
             signal — NOT GitHub's async-stale `mergeable` verdict (it reads
             MERGEABLE optimistically before a recompute lands). Gate order once
             Reviewed: a conflict (DIRTY) or a BEHIND base surfaces first (a
             moved base re-stales CI); then failing/pending CI (BLOCKED /
             VALIDATING); then CLEAN -> READY; an uncomputed (UNKNOWN) merge
             state re-polls; any remaining computed non-CLEAN state
             (BLOCKED/UNSTABLE/HAS_HOOKS) is BLOCKED (release#675).

Best-effort reviewers (Gemini) never gate: an absent or in-progress best-effort
reviewer does not hold the PR in REVIEWS_PENDING. The *skip-after-timeout*
decision is the polling caller's, not the snapshot's — the snapshot is
stateless and has no clock.

Review cycles repeat until done: a review counts only against the current
head, so any push stales the prior review and the snapshot advises RE-REQUEST
(the engine is the arbiter — no minor-round exception, #565).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .breakers import DiffSizer, evaluate_breakers
from .model import PullContext, ReviewLifecycle
from .reviewers import REGISTRY, ReviewerAdapter, required_reviewers

_DONE = {ReviewLifecycle.DONE_CLEAN, ReviewLifecycle.DONE_COMMENTS}

# CheckRun conclusions / StatusContext states that count as failures.
_FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_FAIL_STATES = {"FAILURE", "ERROR"}
_PENDING_STATUSES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}


class TaskState(StrEnum):
    NO_PR = "no_pr"
    REVIEWS_PENDING = "reviews_pending"
    ADDRESSING = "addressing"
    REVIEWED = "reviewed"
    VALIDATING = "validating"
    READY = "ready"
    BLOCKED = "blocked"


class ChecksState(StrEnum):
    NONE = "none"  # no checks configured
    GREEN = "green"
    PENDING = "pending"
    FAILING = "failing"


@dataclass
class TaskStatus:
    """The snapshot result: lifecycle position + the one next action."""

    state: TaskState
    next_action: str
    pr: int | None = None
    reviewers: dict[str, str] = field(default_factory=dict)
    open_threads: int = 0
    checks: ChecksState = ChecksState.NONE
    mergeable: str | None = None
    cycles: int = 0  # completed required-reviewer review cycles
    breaker: str | None = None  # which circuit breaker fired, if any

    def to_dict(self) -> dict:
        return {
            "pr": self.pr,
            "state": self.state.value,
            "next_action": self.next_action,
            "reviewers": self.reviewers,
            "open_threads": self.open_threads,
            "checks": self.checks.value,
            "mergeable": self.mergeable,
            "cycles": self.cycles,
            "breaker": self.breaker,
        }


def no_pr() -> TaskStatus:
    """No PR exists for the branch — the entry state."""
    return TaskStatus(
        state=TaskState.NO_PR,
        next_action="no PR for this branch — create a draft PR to start the review loop",
    )


def evaluate(
    ctx: PullContext,
    registry: list[ReviewerAdapter] | None = None,
    diff_sizer: DiffSizer | None = None,
    required: list[ReviewerAdapter] | None = None,
) -> TaskStatus:
    """Compute the PR's lifecycle state from a snapshot.

    Pure when `required` is supplied: a function of `ctx` + the given reviewer
    set, modulo `diff_sizer` (an optional git-backed callable for the
    diff-trajectory breaker; without it that one breaker is skipped). The CLI
    entrypoints resolve the required set once and pass it in, so the production
    paths stay pure — config resolution lives at the edge, not in the engine.

    `required` is the gating reviewer SET; every reviewer in it gates Ready
    (parallel-required, release#622), reviewers outside it are best-effort and
    never block. A test passes a DIFFERENT set to prove the engine is
    data-driven, not hard-coded to any reviewer. The `None` default is a
    convenience for REPL/ad-hoc callers ONLY — it resolves the config-default
    set (`reviewers.required_reviewers()`, which reads `.release-sync.yaml`),
    the one impurity, which is why the CLI never relies on it.
    """
    registry = registry if registry is not None else REGISTRY
    required = required if required is not None else required_reviewers()
    # Detect over the union of the catalog and the required set so a required
    # reviewer is always evaluated even if (in a test) it isn't in `registry`.
    to_detect = {r.name: r for r in (*registry, *required)}.values()
    lifecycles = {r.name: r.detect(ctx) for r in to_detect}
    reviewers = {name: lc.value for name, lc in lifecycles.items()}
    open_threads = len(ctx.open_threads())
    checks = classify_checks(ctx.checks)
    # Breakers count cycles against the SAME required set the engine gates on —
    # passed through so an override repo's breaker math matches its reviewers.
    breaker = evaluate_breakers(ctx, diff_sizer, required=required)

    status = TaskStatus(
        state=TaskState.REVIEWS_PENDING,  # provisional; set below
        next_action="",
        pr=ctx.number,
        reviewers=reviewers,
        open_threads=open_threads,
        checks=checks,
        mergeable=ctx.mergeable,
        cycles=breaker.cycles,
    )

    # 1. Required reviewers must all be done. Best-effort ones never gate. The
    #    required SET is config-driven (release#622): every reviewer in it gates,
    #    so a missing review by ANY required reviewer holds the PR in
    #    REVIEWS_PENDING and names that reviewer as outstanding.
    pending_required = [r for r in required if lifecycles[r.name] not in _DONE]
    if pending_required:
        status.state = TaskState.REVIEWS_PENDING
        status.next_action = _reviews_pending_action(ctx, pending_required, lifecycles)
        return status

    # 2. Required reviews in; any open thread (from any reviewer) must be addressed
    #    — UNLESS a circuit breaker says the loop is diverging: then STOP, don't
    #    open another cycle. A converged PR (no open threads) is never stopped.
    if open_threads:
        if breaker.stop:
            status.state = TaskState.BLOCKED
            status.breaker = breaker.breaker
            status.next_action = (
                f"STOP — circuit breaker '{breaker.breaker}' fired: {breaker.reason}. "
                "Do not iterate; surface to the human."
            )
            return status
        status.state = TaskState.ADDRESSING
        status.next_action = (
            f"triage {open_threads} open thread(s): read them with "
            "`release-core pr review show`, then fix-or-reply + resolve each"
        )
        return status

    # 3. Reviewed. Now gate on mergeability + CI.
    #
    # GitHub exposes mergeability through TWO fields, and they disagree often
    # enough to matter (release#675):
    #   - `mergeable`        MERGEABLE / CONFLICTING / UNKNOWN — computed
    #                        ASYNCHRONOUSLY; the first read after an open / push
    #                        / base move returns the STALE prior value (usually
    #                        the optimistic MERGEABLE) until the recompute lands.
    #   - `mergeStateStatus` CLEAN / DIRTY / BEHIND / BLOCKED / UNSTABLE /
    #                        HAS_HOOKS / UNKNOWN — the richer, fresher signal,
    #                        and the one the merge actually obeys.
    # READY therefore requires the authoritative `mergeStateStatus == CLEAN`,
    # not just a (stale-able) MERGEABLE verdict. Every other COMPUTED state is a
    # real reason the PR is not merge-ready and must NOT hand off:
    #   DIRTY    → conflict          BEHIND → base moved, head out of date
    #   BLOCKED  → branch protection / a required status not satisfied
    #   UNSTABLE → a (non-required) check is failing/pending
    # An UNKNOWN / null merge state means GitHub is still computing — re-poll
    # (that loop is `release-core pr wait`'s job: gather()+evaluate() until a
    # terminal state), never flip on it. We do NOT special-case approval-pending
    # because this fleet requires 0 approving reviews — a reviewed + green PR
    # reaches CLEAN without a human, so a non-CLEAN computed state is always a
    # genuine block, not a waiting-on-the-human handoff point.

    # A real conflict — from EITHER signal. DIRTY is the authoritative flag;
    # CONFLICTING is its slower, sometimes-stale mirror. Checked first: a
    # conflict must be resolved regardless of CI.
    if ctx.mergeable == "CONFLICTING" or ctx.merge_state == "DIRTY":
        status.state = TaskState.BLOCKED
        status.next_action = "merge conflict — rebase/resolve against the base branch"
        return status

    # Behind the base branch: the head no longer contains the base tip, so it
    # cannot merge cleanly. Checked BEFORE CI because a moved base re-stales the
    # branch's review + checks — reporting VALIDATING/CI-blocked here would give
    # a misleading next action; "update the branch" is the actionable one. The
    # agent updates and re-evaluates — not a human handoff.
    if ctx.merge_state == "BEHIND":
        status.state = TaskState.BLOCKED
        status.next_action = (
            "branch is behind its base — update it (merge/rebase the base) before this can be Ready"
        )
        return status

    if checks == ChecksState.FAILING:
        status.state = TaskState.BLOCKED
        status.next_action = "CI check(s) failing — fix and push before this can be Ready"
        return status

    if checks == ChecksState.PENDING:
        status.state = TaskState.VALIDATING
        status.next_action = "reviews done; CI check(s) running — wait for checks"
        return status

    # CLEAN is the ONLY merge-ready state — mergeable, current, all contexts
    # green. This is the single hand-off point.
    if ctx.merge_state == "CLEAN":
        status.state = TaskState.READY
        if ctx.is_draft:
            status.next_action = (
                "reviewed + threads resolved + CI green + CLEAN merge state — run "
                "`release-core pr ready` to flip draft->ready and page the human"
            )
        else:
            status.next_action = (
                "reviewed + threads resolved + CI green + CLEAN merge state, already "
                "ready-for-review — done; await the human's verify + merge"
            )
        return status

    # Merge state not yet computed (UNKNOWN / null) — GitHub is working; re-poll.
    if ctx.merge_state in (None, "UNKNOWN"):
        status.state = TaskState.REVIEWED
        status.next_action = "reviews done; mergeability not yet determined — re-check shortly"
        return status

    # Computed, but a non-CLEAN merge state (BLOCKED / UNSTABLE / HAS_HOOKS):
    # GitHub is blocking the merge for a real reason — a status check or
    # branch-protection rule (UNSTABLE = a non-required check failing/pending).
    # Surface it; don't flip.
    status.state = TaskState.BLOCKED
    status.next_action = (
        f"merge blocked by GitHub (mergeStateStatus={ctx.merge_state}) — a status "
        "check or branch-protection rule is unsatisfied; resolve before this can be Ready"
    )
    return status


def _reviews_pending_action(
    ctx: PullContext,
    pending: list[ReviewerAdapter],
    lifecycles: dict[str, ReviewLifecycle],
) -> str:
    """Build the REVIEWS_PENDING next-action, distinguishing the two cases a
    bare "request if not yet requested, else wait" conflates:

      • never-requested — no review by this reviewer has ever landed → request.
      • stale-after-push — a review landed on an EARLIER commit but the current
        head is `not_requested` (a fixup push resets Copilot's request) → the
        action is to *re-request* the reviewer for the new head, not to wait.

    The distinction is cheap: a review on a non-head commit means a prior cycle
    existed. A reviewer already REQUESTED / IN_PROGRESS on the head is simply
    pending — wait.
    """
    request_names: list[str] = []  # never reviewed → request
    rerequest_names: list[str] = []  # reviewed an earlier head → re-request
    waiting_names: list[str] = []  # already requested/in-progress on head → wait

    for adapter in pending:
        lc = lifecycles[adapter.name]
        if lc in (ReviewLifecycle.REQUESTED, ReviewLifecycle.IN_PROGRESS):
            waiting_names.append(adapter.name)
        elif _has_stale_review(ctx, adapter):
            rerequest_names.append(adapter.name)
        else:
            request_names.append(adapter.name)

    clauses: list[str] = []
    if request_names:
        clauses.append(f"request for the current head: {', '.join(request_names)}")
    if rerequest_names:
        clauses.append(
            "RE-REQUEST for the current head (a prior review is stale after a push): "
            f"{', '.join(rerequest_names)}"
        )
    if waiting_names:
        clauses.append(f"wait (already requested on the current head): {', '.join(waiting_names)}")

    all_names = [a.name for a in pending]
    return f"waiting on required review(s): {', '.join(all_names)} — " + "; ".join(clauses)


def _has_stale_review(ctx: PullContext, adapter: ReviewerAdapter) -> bool:
    """True iff this reviewer has a review on some commit OTHER than the current
    head — i.e. it reviewed an earlier commit and a push has since moved the head
    (the request reset to not_requested). DISMISSED reviews don't count."""
    return any(
        adapter.matches(r.author) and r.state != "DISMISSED" and r.commit_id != ctx.head_sha
        for r in ctx.reviews
    )


def classify_checks(rollup: list[dict]) -> ChecksState:
    """Reduce a gh `statusCheckRollup` to one state.

    Handles both CheckRun entries (status/conclusion) and legacy StatusContext
    entries (state). Failing dominates pending dominates green.
    """
    if not rollup:
        return ChecksState.NONE
    saw_pending = False
    saw_green = False
    for entry in rollup:
        if _is_failing(entry):
            return ChecksState.FAILING
        if _is_pending(entry):
            saw_pending = True
        else:
            saw_green = True
    if saw_pending:
        return ChecksState.PENDING
    return ChecksState.GREEN if saw_green else ChecksState.NONE


def _is_failing(entry: dict) -> bool:
    if entry.get("conclusion") in _FAIL_CONCLUSIONS:
        return True
    return entry.get("state") in _FAIL_STATES


def _is_pending(entry: dict) -> bool:
    # CheckRun: any status other than COMPLETED is still running.
    # StatusContext (no `status` field): a pending-ish `state`.
    status = entry.get("status")
    if status is not None:
        return status != "COMPLETED"
    return entry.get("state") in _PENDING_STATUSES
