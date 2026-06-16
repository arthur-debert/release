"""Circuit breakers — detect a diverging review loop and STOP before iterating.

Heuristics for the review-loop circuit breakers. A *cycle* is one ITERATION
ROUND — one head SHA that got re-reviewed — NOT one review object. This is the
load-bearing distinction once there are multiple required reviewers
(release#622): N required reviewers each reviewing a head would otherwise count
as N cycles, so two normal review rounds with two reviewers would read as four
and trip the cycle cap. A cycle is keyed by commit SHA: every required
reviewer's findings on the same head fold into ONE cycle. Its findings are the
review-thread comments attached to those reviews (GraphQL `reviewThreads`, the
single source of truth for inline comments — the REST `/pulls/{n}/comments`
fetch surfaced only a subset and is gone; release#515). All inputs derive from
gh review history, except diff sizes, which come from git and are injected via
`diff_sizer` — omitted in pure evaluation, in which case the diff-trajectory
breaker is skipped rather than guessed.

The verdict folds into the state machine as the STOP form of BLOCKED, and only
when the loop would otherwise iterate (open threads remain). A converged PR is
never stopped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .model import PullContext
from .reviewers import ReviewerAdapter, required_reviewers

CYCLE_CAP = 3
DIFF_GROWTH_TOLERANCE = 1.1  # allow 10% jitter before calling it "growing"
MIN_DIFF_LINES = 50  # below this the diff is too small for "growing" to mean anything
REPEAT_WINDOW = 3  # a location must persist this many consecutive cycles to stop

CommentKey = tuple[str, "int | None"]
DiffSizer = Callable[[str], "int | None"]


@dataclass(frozen=True)
class Cycle:
    index: int  # 1-based, chronological
    commit_id: str
    comment_keys: frozenset  # {(path, line)} of this review's findings
    diff_size: int | None = None


@dataclass(frozen=True)
class BreakerVerdict:
    stop: bool
    breaker: str | None
    reason: str
    cycles: int


def build_cycles(
    ctx: PullContext,
    diff_sizer: DiffSizer | None = None,
    required: list[ReviewerAdapter] | None = None,
) -> list[Cycle]:
    """One Cycle per HEAD SHA reviewed by a required reviewer, chronological.

    Cycles are iteration ROUNDS, not review objects: all required reviewers'
    reviews on the same head fold into a single cycle, so N required reviewers
    don't multiply the cycle count (release#622 — that double-count tripped the
    cap on normal PRs). A cycle's findings are the UNION of those reviews'
    thread comments (resolved or not — a resolved finding was still a finding of
    that round), keyed by the review each comment was submitted with
    (`ReviewComment.review_id`). Login matching is the adapter's job — never
    re-roll an author filter here (a reviewer's review login and comment author
    can render differently; release#455).

    `required` is the gating set; defaults to the config-resolved one but the
    engine threads its own set in so breakers count against the SAME reviewers
    everything else gates on (keeps `evaluate` honest under an override).
    """
    required = required if required is not None else required_reviewers()
    reviews = sorted(
        (r for r in ctx.reviews if any(a.matches(r.author) for a in required)),
        key=lambda r: r.review_id,
    )
    thread_comments = [c for t in ctx.threads for c in t.comments]

    # Group by head SHA, preserving first-seen (chronological) order. Each head
    # is one cycle; its findings union every required review on that head.
    review_ids_by_head: dict[str, list[int]] = {}
    for review in reviews:
        review_ids_by_head.setdefault(review.commit_id, []).append(review.review_id)

    cycles: list[Cycle] = []
    for index, (commit_id, review_ids) in enumerate(review_ids_by_head.items(), start=1):
        id_set = set(review_ids)
        keys = frozenset((c.path, c.line) for c in thread_comments if c.review_id in id_set)
        size = diff_sizer(commit_id) if diff_sizer else None
        cycles.append(Cycle(index, commit_id, keys, size))
    return cycles


def divergent_cycle_count(cycles: list[Cycle]) -> int:
    """How many cycles advanced the *divergence* counter — the cap's metric.

    A cycle counts only if it introduced at least one finding LOCATION
    (`(path, line)`) not seen in any earlier cycle. A round whose findings were
    all already-seen locations — or that produced none at all (a cosmetic /
    false-positive / fully-resolved round) — adds NO new divergence signal, so
    it does NOT advance the counter (release#738).

    This is the persistence model the `repeat-finding` breaker already keys on
    (`REPEAT_WINDOW`): the divergence signal is NEW, accumulating finding
    locations, not raw round count. A genuinely diverging loop keeps surfacing
    fresh problems and still trips the cap; a converging loop whose later rounds
    only re-touch known areas (or nitpick cosmetically) is not guillotined.

    Conservative by construction: it can only ever return <= len(cycles), so it
    never makes the cap fire EARLIER than the raw round count would have.
    """
    seen: set = set()
    count = 0
    for cycle in cycles:
        new_locations = set(cycle.comment_keys) - seen
        if new_locations:
            count += 1
            seen |= new_locations
    return count


def evaluate_breakers(
    ctx: PullContext,
    diff_sizer: DiffSizer | None = None,
    required: list[ReviewerAdapter] | None = None,
) -> BreakerVerdict:
    """Run the breaker stack (priority order); first to fire wins.

    `required` is threaded through to `build_cycles` so cycle counting uses the
    SAME required set the engine gates on (release#622).

    The reported `cycles` is the raw round count (what the human sees), but the
    cycle-cap fires on the DIVERGENT count — rounds that introduced a new
    finding location — so a converging loop whose later rounds were
    cosmetic/false-positive/fully-resolved is not capped (release#738)."""
    cycles = build_cycles(ctx, diff_sizer, required=required)
    n = len(cycles)
    divergent = divergent_cycle_count(cycles)

    if divergent > CYCLE_CAP:
        return BreakerVerdict(
            True,
            "cycle-cap",
            f"{divergent} divergent review cycles exceeds the cap of {CYCLE_CAP}",
            n,
        )

    for check in (_diff_trajectory, _comment_fixed_point, _repeat_finding):
        verdict = check(cycles, n)
        if verdict is not None:
            return verdict

    return BreakerVerdict(False, None, "", n)


def _diff_trajectory(cycles: list[Cycle], n: int) -> BreakerVerdict | None:
    """Diff growing two consecutive cycles signals divergence (would catch #118).

    Guarded by an absolute floor so a 1 -> 2 -> 3 line PR doesn't trip it — only
    diffs that are both growing *and* non-trivial count.
    """
    sized = [c for c in cycles if c.diff_size is not None]
    if len(sized) < 3:
        return None
    a, b, c = sized[-3], sized[-2], sized[-1]
    if c.diff_size < MIN_DIFF_LINES:
        return None
    if b.diff_size > a.diff_size * DIFF_GROWTH_TOLERANCE and (
        c.diff_size > b.diff_size * DIFF_GROWTH_TOLERANCE
    ):
        return BreakerVerdict(
            True,
            "diff-trajectory",
            f"diff growing across cycles: {a.diff_size} -> {b.diff_size} -> {c.diff_size}",
            n,
        )
    return None


def _comment_fixed_point(cycles: list[Cycle], n: int) -> BreakerVerdict | None:
    """Cycle N+1's findings are *identical* to cycle N's — a true fixed point.

    Equality, not subset: a strict subset means some findings were resolved,
    which is progress, not a stuck loop. A persisting location is the
    repeat-finding breaker's job, not this one's.
    """
    if n < 2:
        return None
    prev, last = cycles[-2], cycles[-1]
    if last.comment_keys and last.comment_keys == prev.comment_keys:
        return BreakerVerdict(
            True,
            "comment-set",
            "latest review's findings are identical to the previous cycle's (fixed point)",
            n,
        )
    return None


def _repeat_finding(cycles: list[Cycle], n: int) -> BreakerVerdict | None:
    """The same (path, line) flagged across REPEAT_WINDOW consecutive cycles.

    Requires the location to survive two fix attempts (3 cycles by default), not
    one — a second attempt at a stubborn comment is normal; a third is the
    signal that it needs redesign rather than another fix.
    """
    if n < REPEAT_WINDOW:
        return None
    window = cycles[-REPEAT_WINDOW:]
    recurring = set.intersection(*(set(c.comment_keys) for c in window))
    if recurring:
        where = ", ".join(
            f"{p}:{ln}" for p, ln in sorted(recurring, key=lambda k: (k[0], k[1] or 0))
        )
        return BreakerVerdict(
            True,
            "repeat-finding",
            f"same location flagged in {REPEAT_WINDOW} consecutive cycles ({where}) — "
            "redesign, not another fix",
            n,
        )
    return None
