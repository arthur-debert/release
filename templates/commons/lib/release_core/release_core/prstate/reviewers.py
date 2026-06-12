"""Reviewer adapters — the only place that knows reviewer-specific mechanics.

The state machine and the CLI consume the adapter interface (`required`,
`detect`, `open_threads` on the read side; `request`, `cancel`,
`instruction_files` on the act side) and never branch on a reviewer's name.
Adding a reviewer is adding an adapter to `REGISTRY`; nothing downstream
changes. This is what keeps the core stable as the coding-agent landscape
shifts.
"""

from __future__ import annotations

from . import ghapi
from .model import PullContext, ReviewLifecycle, Thread


class ReviewerAdapter:
    """Base adapter. Subclasses define the read side (`matches`, `detect`) and
    the act side (`request`, `cancel`); `instruction_files` declares where the
    reviewer's per-repo code-review instructions live."""

    name: str = ""
    # Whether this adapter HAS a request mechanism (a real `review_requested`
    # edge it can place + the #614 attach-verification). Best-effort
    # auto-triggering backends (Gemini) set this False and can never be a
    # required, gating reviewer. WHICH requestable adapters are *currently*
    # required is NOT decided here — it is the config knob in
    # `reviewers_config` (release#622); this flag only marks eligibility.
    requestable: bool = False
    # Repo-relative path(s) of this reviewer's code-review instruction file(s).
    # Structure only: the adapter declares the location; whether content ships
    # there is a per-reviewer onboarding decision.
    instruction_files: tuple[str, ...] = ()

    def matches(self, login: str) -> bool:
        raise NotImplementedError

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        raise NotImplementedError

    def request(self, pr: int) -> bool:
        """Request — or re-request, same call — this reviewer on `pr`.

        Returns True when a request was actually placed, False when the
        reviewer has no request mechanism (auto-triggering / best-effort
        backends). Re-request after a fixup push is not a separate verb:
        the state machine's never-requested vs stale-after-push distinction
        is a read-side concern (`state._has_stale_review`); the act is the
        same either way.

        Placement only: True means the call was accepted, not that the
        `review_requested` edge exists — GitHub can silently drop the attach
        (release#614). The `pr review request` verb verifies the edge for
        every adapter that returns True, generically; False-returning
        (no-mechanism) adapters are never verified.
        """
        raise NotImplementedError

    def cancel(self, pr: int) -> bool:
        """Withdraw a pending review request on `pr`.

        Returns True when a request was withdrawn, False when there is no
        request mechanism to withdraw from (no-op backends).
        """
        raise NotImplementedError

    def authored_threads(self, ctx: PullContext) -> list[Thread]:
        """All threads (resolved or not) rooted in a comment by this reviewer."""
        return [t for t in ctx.threads if t.author and self.matches(t.author)]

    def open_threads(self, ctx: PullContext) -> list[Thread]:
        """Unresolved threads by this reviewer — the ones still needing action."""
        return [t for t in self.authored_threads(ctx) if not t.is_resolved]

    def _done_state(self, ctx: PullContext) -> ReviewLifecycle:
        return (
            ReviewLifecycle.DONE_COMMENTS
            if self.authored_threads(ctx)
            else ReviewLifecycle.DONE_CLEAN
        )


class CopilotAdapter(ReviewerAdapter):
    """Copilot posts a discrete review object on the PR head SHA.

    The head-SHA filter is load-bearing: a review against an earlier commit is
    stale and must not count as done for the current head. Copilot has no
    observable mid-review signal, so it goes REQUESTED -> DONE.
    """

    name = "copilot"
    requestable = True
    instruction_files = (".github/copilot-instructions.md",)

    def matches(self, login: str) -> bool:
        return "copilot" in login.lower()

    def request(self, pr: int) -> bool:
        # `gh pr edit --add-reviewer @copilot` — GraphQL with the bot's real
        # node_id (via ghapi.pr_edit_reviewer; the REST requested_reviewers
        # POST silently no-ops for Copilot). Re-request is the same call.
        ghapi.pr_edit_reviewer(pr, "@copilot")
        return True

    def cancel(self, pr: int) -> bool:
        ghapi.pr_edit_reviewer(pr, "@copilot", remove=True)
        return True

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        # A DISMISSED review (cleared by an admin or the author) is no longer a
        # standing verdict — it must not count as done, or the PR reads REVIEWED
        # off a review that was explicitly retracted.
        if any(self.matches(r.author) and r.state != "DISMISSED" for r in ctx.reviews_on_head()):
            return self._done_state(ctx)
        if any(self.matches(login) for login in ctx.requested_logins):
            return ReviewLifecycle.REQUESTED
        return ReviewLifecycle.NOT_REQUESTED


class CodeRabbitAdapter(ReviewerAdapter):
    """CodeRabbit is a requestable GitHub App that posts a discrete review on the
    PR head SHA — structurally the same model as Copilot, and the second
    REQUIRED reviewer (release#622).

    Parallel-required, not fallback: Copilot and CodeRabbit each gate Ready, so
    a PR is reviewed only when BOTH have a fresh review on the current head. The
    accepted trade-off is availability — one required reviewer's outage holds
    Ready until it recovers — in exchange for always-on dual coverage and no
    single point of failure on review *quality*.

    Like Copilot, CodeRabbit re-reviews each push, so it is head-strict: a
    review against an earlier commit is stale and must not count as done for the
    current head. The request goes through `gh pr edit --add-reviewer` (the
    GraphQL path that resolves the App's real node id and creates a real
    `review_requested` edge) — so the generic #614 attach-verification in
    `pr review request` applies unchanged: a silently dropped attach fails loud.
    """

    name = "coderabbit"
    requestable = True
    instruction_files = (".coderabbit.yaml",)
    # The reviewer handle `gh pr edit --add-reviewer` resolves to the App's node
    # id. CodeRabbit's bot login on submitted reviews / pending requests is
    # `coderabbitai[bot]`; `matches` keys off the stable `coderabbit` substring.
    _REVIEWER_HANDLE = "coderabbitai[bot]"

    def matches(self, login: str) -> bool:
        return "coderabbit" in login.lower()

    def request(self, pr: int) -> bool:
        # Same GraphQL add-reviewer path Copilot uses: it resolves the App's
        # real node id and creates a real review_requested edge (the REST
        # requested_reviewers POST silently no-ops for App reviewers).
        ghapi.pr_edit_reviewer(pr, self._REVIEWER_HANDLE)
        return True

    def cancel(self, pr: int) -> bool:
        ghapi.pr_edit_reviewer(pr, self._REVIEWER_HANDLE, remove=True)
        return True

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        # Head-strict, DISMISSED-aware — identical lifecycle shape to Copilot.
        if any(self.matches(r.author) and r.state != "DISMISSED" for r in ctx.reviews_on_head()):
            return self._done_state(ctx)
        if any(self.matches(login) for login in ctx.requested_logins):
            return ReviewLifecycle.REQUESTED
        return ReviewLifecycle.NOT_REQUESTED


class GeminiAdapter(ReviewerAdapter):
    """Gemini signals weakly and is best-effort.

    The app triggers automatically (no discrete request event); an eyes reaction
    from the bot means it is looking; a review or issue comment means it is done.
    It goes over quota silently, so the state machine treats a timed-out Gemini
    as skipped rather than blocking Ready — that timing decision lives in the
    state machine, not here.

    Crucially, **Gemini reviews a PR once and does not re-review pushes** — so a
    review on *any* commit of this PR counts as done, unlike Copilot's
    head-strict model. (The eyes reaction is not commit-scoped and lingers after
    the review, so a fixup that creates a new head would otherwise read as a
    fresh "in_progress" forever.) This per-reviewer difference is exactly what
    the adapter layer exists to hold.
    """

    name = "gemini"
    requestable = False  # auto-triggers; no request edge, so never a required gate
    # Declared location only — no content shipped until Gemini is onboarded
    # as a required reviewer.
    instruction_files = (".gemini/styleguide.md",)

    def matches(self, login: str) -> bool:
        return "gemini" in login.lower()

    def request(self, pr: int) -> bool:
        # The Gemini app auto-triggers on PR open; there is no request
        # mechanism, and it is best-effort anyway — a no-op, not an error.
        return False

    def cancel(self, pr: int) -> bool:
        return False

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        # Any-head, not head-strict: Gemini won't review the new head again.
        # A DISMISSED review is retracted, so it doesn't count as done.
        if any(self.matches(r.author) and r.state != "DISMISSED" for r in ctx.reviews):
            return self._done_state(ctx)
        if any(self.matches((c.get("user") or {}).get("login", "")) for c in ctx.issue_comments):
            return ReviewLifecycle.DONE_COMMENTS
        if self._is_looking(ctx):
            return ReviewLifecycle.IN_PROGRESS
        return ReviewLifecycle.NOT_REQUESTED

    def _is_looking(self, ctx: PullContext) -> bool:
        return any(
            r.get("content") == "eyes" and self.matches((r.get("user") or {}).get("login", ""))
            for r in ctx.reactions
        )


# The adapter CATALOG: every reviewer the engine knows how to read/request. This
# is the registry (#558) — adding a backend is adding an adapter here. WHICH of
# these gate Ready is NOT decided here: that is the config knob in
# `reviewers_config` (release#622), default [copilot, coderabbit].
REGISTRY: list[ReviewerAdapter] = [CopilotAdapter(), CodeRabbitAdapter(), GeminiAdapter()]


def required_reviewers() -> list[ReviewerAdapter]:
    """The currently-required reviewer adapters, resolved from config.

    The required SET is data (`reviewers_config`: a shipped default plus a
    per-repo `.release-sync.yaml` override), not the registry's structure — so
    swapping/re-ordering required reviewers is a one-line config edit. Names map
    back to these adapters; an unknown name fails loud.
    """
    from . import reviewers_config

    override = reviewers_config.load_override()
    names = reviewers_config.resolve_required_names(override)
    return reviewers_config.required_reviewers(names)


def by_name(name: str) -> ReviewerAdapter | None:
    """Look an adapter up by its registry name (the `--reviewer` selector)."""
    for r in REGISTRY:
        if r.name == name.lower():
            return r
    return None
