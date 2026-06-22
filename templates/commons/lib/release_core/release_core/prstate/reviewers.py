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
    PR head SHA — structurally the same model as Copilot. It is being PILOTED on
    the phos-org repos (the only place the App is installed); a pilot repo opts
    in via `required_reviewers:` in its `.release-sync.yaml`. It is NOT in the
    default required set: on a repo without the App, the request edge silently
    drops (#613-style) and a required gate would park every PR at
    REVIEWS_PENDING. Whether it gates is a config decision, not an adapter
    property — this adapter only declares CodeRabbit *requestable* (it has a
    real request edge + the #614 attach-verification, so it is ELIGIBLE to be
    required wherever the App is installed).

    When a repo requires both Copilot and CodeRabbit, the policy is
    parallel-required, not fallback: each gates Ready, so a PR is reviewed only
    when BOTH have a fresh review on the current head. The accepted trade-off is
    availability — one required reviewer's outage holds Ready until it recovers —
    in exchange for always-on dual coverage and no single point of failure on
    review *quality*.

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


class _LocalReviewAdapter(ReviewerAdapter):
    """A LOCAL review backend (codex / agy) surfaced as a reviewer adapter.

    Unlike the GitHub-App reviewers (Copilot / CodeRabbit), these reviewers do
    not exist as an installed App that GitHub auto-triggers or that an
    `--add-reviewer` edge addresses. The review is GENERATED locally — the agent
    CLI runs in the PR checkout — and POSTED as the agent's own bot identity via
    `release_core.review.service.run_and_post`. So `request` here is SYNCHRONOUS
    (it runs the review + posts it now), and there is no `review_requested` edge
    to place or withdraw: `cancel` is a no-op.

    Detection is head-strict like Copilot: a non-dismissed review by `matches`
    on the current head reads as done; otherwise NOT_REQUESTED (there is no
    requested edge for a local reviewer, so `requested_logins` is never
    consulted). The bot login is matched on BOTH the GitHub App `[bot]` suffix
    AND a stable slug fragment (`codex-review` / `agy-review`) — so a future
    prefix (`adr-codex-review[bot]`) still matches, but a bare human login that
    merely contains `codex` / `agy` (e.g. `codexdev`, `agytron`) does NOT, which
    would otherwise misread as a bot review and falsely report DONE. The
    user-specific app-name prefix (e.g. `adr-`) is never hardcoded.
    """

    requestable = True
    # The stable bot-login slug fragment this reviewer matches (set by each
    # subclass). `matches` requires the `[bot]` suffix AND this fragment.
    bot_slug_fragment: str = ""

    def matches(self, login: str) -> bool:
        # Require the GitHub App `[bot]` SUFFIX (not just the substring
        # anywhere) AND the stable slug fragment. `adr-codex-review[bot]` /
        # `adr-agy-review[bot]` end with `[bot]`, so they still match; a login
        # that merely contains `[bot]` mid-string (e.g. `x[bot]y`) does not.
        low = login.lower()
        return low.endswith("[bot]") and self.bot_slug_fragment in low

    def request(self, pr: int) -> bool:
        """Generate the review locally and POST it now (synchronous).

        These are LOCAL reviewers: they execute where the agent CLI and the
        review App's signing key live. `request` delegates to
        `release_core.review.service.run_and_post(self.name, pr, as_app=True)`,
        which runs the agent over the PR diff and posts the result AS the bot.

        Errors are NOT swallowed, but they ARE normalized: a missing agent CLI
        (`BackendUnavailable`), a diff/review failure (`ReviewError`), an auth
        failure (`ReviewAuthError`), or a missing/unregistered review App (a
        clear `RuntimeError` from `run_and_post`) is re-raised as
        `ghapi.GhError` — the prstate error type the `pr review request` CLI
        already catches and renders as a clean message + exit 1. Without this,
        `--reviewer codex|agy` would crash with an unhandled traceback (the CLI
        only handles `GhError`). On success returns True.
        """
        # Imported lazily: `prstate` must not pull the `review` engine (and its
        # backend/gh machinery) at import time — only when a local review is
        # actually requested. `review` never imports `prstate`, so this one-way
        # edge is cycle-free. (Same reason the failure types are imported here.)
        from release_core.review.backends.base import BackendUnavailable
        from release_core.review.diff import ReviewError
        from release_core.review.ghauth import ReviewAuthError
        from release_core.review.service import run_and_post

        try:
            run_and_post(self.name, pr, as_app=True)
        except (BackendUnavailable, ReviewError, ReviewAuthError, RuntimeError) as exc:
            # Re-raise the local-review failure modes as the one error type the
            # CLI's `except ghapi.GhError` handles, so a failed local request is
            # a clean error + nonzero exit, never an unhandled traceback. The
            # caught set is SPECIFIC (not bare Exception); BackendUnavailable /
            # ReviewError already subclass RuntimeError, ReviewAuthError does not
            # (so it is listed explicitly), and the trailing RuntimeError covers
            # the app-not-registered error raised by run_and_post itself.
            raise ghapi.GhError(f"{self.name} review failed: {exc}") from exc
        return True

    def cancel(self, pr: int) -> bool:
        """No-op: a posted review can't be withdrawn.

        A local reviewer leaves a real, submitted review rather than a pending
        `review_requested` edge — there is nothing to cancel. Returns False, the
        same shape a no-mechanism backend uses.
        """
        return False

    def detect(self, ctx: PullContext) -> ReviewLifecycle:
        # Head-strict, DISMISSED-aware. There is no requested edge for a local
        # reviewer, so we never check `requested_logins` — either a fresh review
        # on head exists (done) or the reviewer hasn't run yet (NOT_REQUESTED).
        if any(self.matches(r.author) and r.state != "DISMISSED" for r in ctx.reviews_on_head()):
            return self._done_state(ctx)
        return ReviewLifecycle.NOT_REQUESTED


class CodexAdapter(_LocalReviewAdapter):
    """Codex — a LOCAL review backend posted as the `adr-codex-review[bot]`
    identity. See :class:`_LocalReviewAdapter` for the synchronous-request /
    no-cancel / head-strict contract."""

    name = "codex"
    instruction_files = (".github/codex-review-instructions.md",)
    bot_slug_fragment = "codex-review"


class AgyAdapter(_LocalReviewAdapter):
    """Agy — a LOCAL review backend posted as the `adr-agy-review[bot]` identity.

    Matches on the `agy-review` slug fragment + `[bot]` suffix (NOT `gemini`:
    the bot login is `adr-agy-review`, and `gemini` belongs to the separate
    auto-triggering GeminiAdapter). See :class:`_LocalReviewAdapter` for the
    request/cancel/detect contract."""

    name = "agy"
    instruction_files = (".github/agy-review-instructions.md",)
    bot_slug_fragment = "agy-review"


# The adapter CATALOG: every reviewer the engine knows how to read/request. This
# is the registry (#558) — adding a backend is adding an adapter here. WHICH of
# these gate Ready is NOT decided here: that is the config knob in
# `reviewers_config` (release#622), default [copilot] (coderabbit is a
# phos-org pilot, opted in per-repo). codex / agy are LOCAL review backends
# (generated + posted locally), unified under the same adapter interface.
REGISTRY: list[ReviewerAdapter] = [
    CopilotAdapter(),
    CodeRabbitAdapter(),
    GeminiAdapter(),
    CodexAdapter(),
    AgyAdapter(),
]


# Process-lifetime cache of the resolved required set. Resolving reads the
# consumer's `.release-sync.yaml` via yq (a subprocess); `pr wait` calls
# `required_reviewers()` on EVERY poll, so without this a long wait would spawn
# a yq process each tick — needless overhead, and a transient yq/PATH blip could
# break an otherwise-healthy wait. The config cannot change mid-command, so
# caching for the process lifetime is safe. Held as an IMMUTABLE tuple so a
# caller mutating the returned list can't corrupt the cache; tests reset it via
# `_reset_required_cache()`.
_REQUIRED_CACHE: tuple[ReviewerAdapter, ...] | None = None


def required_reviewers() -> list[ReviewerAdapter]:
    """The currently-required reviewer adapters, resolved from config (cached).

    The required SET is data (`reviewers_config`: a shipped default plus a
    per-repo `.release-sync.yaml` override), not the registry's structure — so
    swapping/re-ordering required reviewers is a one-line config edit. Names map
    back to these adapters; an unknown name fails loud. Resolved once per
    process (see `_REQUIRED_CACHE`); each call returns a FRESH list copy, so a
    caller may mutate it freely without disturbing the cache.
    """
    global _REQUIRED_CACHE
    if _REQUIRED_CACHE is None:
        from . import reviewers_config

        override = reviewers_config.load_override()
        names = reviewers_config.resolve_required_names(override)
        _REQUIRED_CACHE = tuple(reviewers_config.required_reviewers(names))
    return list(_REQUIRED_CACHE)


def _reset_required_cache() -> None:
    """Clear the resolved-required-set cache — for tests that vary the config."""
    global _REQUIRED_CACHE
    _REQUIRED_CACHE = None


def by_name(name: str) -> ReviewerAdapter | None:
    """Look an adapter up by its registry name (the `--reviewer` selector)."""
    for r in REGISTRY:
        if r.name == name.lower():
            return r
    return None
