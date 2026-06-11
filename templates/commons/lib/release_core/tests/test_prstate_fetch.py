"""The requested-reviewers fetch path — the gh-CLI Bot-omission regression.

`gh pr view --json reviewRequests` silently omits Bot-typed requested
reviewers: after `gh pr edit --add-reviewer @copilot`, REST shows
`requested_reviewers: [{login: "Copilot", type: "Bot"}]` while gh's JSON field
returns `[]`. Sourced from that field, `CopilotAdapter.detect()` could NEVER
read REQUESTED — `pr status` kept demanding "request for the current head"
even with the request already pending. Requested reviewers therefore come from
GraphQL `reviewRequests` (whose union includes Bots), riding along on the
review-threads query. These tests pin `gather()`'s assembly of that path with
the network mocked at the ghapi boundary.
"""

from __future__ import annotations

from release_core.prstate import fetch
from release_core.prstate.model import ReviewLifecycle
from release_core.prstate.reviewers import CopilotAdapter


def _graphql_page(review_requests: list[dict], threads: list[dict] | None = None) -> dict:
    return {
        "repository": {
            "pullRequest": {
                "reviewRequests": {"nodes": review_requests},
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": threads or [],
                },
            }
        }
    }


def _wire(monkeypatch, review_requests: list[dict]):
    monkeypatch.setattr(fetch.ghapi, "repo_slug", lambda: ("owner", "repo"))
    monkeypatch.setattr(
        fetch.ghapi,
        "pr_meta",
        lambda pr: {
            # The live gh-view payload: no reviewRequests key at all (pr_meta
            # no longer asks for the field gh renders wrong for Bots).
            "number": 558,
            "headRefOid": "abc1234",
            "isDraft": True,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [],
        },
    )
    monkeypatch.setattr(
        fetch.ghapi, "graphql", lambda query, **vars: _graphql_page(review_requests)
    )
    monkeypatch.setattr(fetch.ghapi, "rest", lambda *args, **kwargs: [])


def test_bot_typed_request_yields_copilot_requested(monkeypatch):
    # The regression: a Bot-typed requested reviewer (login "Copilot") must
    # surface in requested_logins and read as REQUESTED through the adapter.
    _wire(monkeypatch, [{"requestedReviewer": {"login": "Copilot"}}])
    ctx = fetch.gather(558)
    assert ctx.requested_logins == ["Copilot"]
    assert CopilotAdapter().detect(ctx) is ReviewLifecycle.REQUESTED


def test_team_request_surfaces_by_slug(monkeypatch):
    # Team nodes carry `slug`, not `login`; a null requestedReviewer (e.g. a
    # deleted account) is skipped rather than crashing the fetch.
    _wire(
        monkeypatch,
        [
            {"requestedReviewer": {"slug": "platform-team"}},
            {"requestedReviewer": None},
        ],
    )
    ctx = fetch.gather(558)
    assert ctx.requested_logins == ["platform-team"]


def test_no_pending_requests_reads_not_requested(monkeypatch):
    _wire(monkeypatch, [])
    ctx = fetch.gather(558)
    assert ctx.requested_logins == []
    assert CopilotAdapter().detect(ctx) is ReviewLifecycle.NOT_REQUESTED
