"""post — map a review JSON to a GitHub "create review" payload and post it.

This is Phase 2.1 of the local code-review backend: take the structured review
that a backend produced (the :data:`release_core.review.schema.REVIEW_SCHEMA`
shape) and turn it into a single GitHub *grouped review* — one
``POST /repos/{owner}/{repo}/pulls/{n}/reviews`` carrying a summary body plus
inline comments anchored to changed lines.

Two functions, kept deliberately separable so the payload build is unit-testable
without any network:

* :func:`build_review_payload` — pure data transform (review + PRContext →
  GitHub payload dict). No I/O.
* :func:`post_review` — builds the payload and (unless ``dry_run``) POSTs it via
  the :mod:`release_core.gh` boundary.

Two GitHub constraints shape the mapping:

* **Self-review** — GitHub returns 422 if you ``APPROVE`` / ``REQUEST_CHANGES``
  your OWN PR. Phase 2.1 posts as the user's own account, so the caller passes
  ``event="COMMENT"`` to override the status-derived event. The override always
  wins (see :func:`build_review_payload`).
* **Diff-line anchoring** — an inline comment whose ``(file, line)`` is not part
  of the PR diff makes GitHub 422 the WHOLE review. We parse the diff once
  (:func:`commentable_lines`) and fold any unanchored finding into the review
  body instead of emitting it as an inline comment.
"""

from __future__ import annotations

import json

from .. import gh
from .diff import PRContext

# Map the review summary.status enum → GitHub review `event`.
_STATUS_TO_EVENT = {
    "APPROVED": "APPROVE",
    "REQUEST_CHANGES": "REQUEST_CHANGES",
    "COMMENT": "COMMENT",
}


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Parse a unified diff → ``{path: set of RIGHT-side (new-file) line numbers}``.

    GitHub only accepts an inline review comment on a line that is part of the
    diff on the side you anchor to. We anchor every comment to the RIGHT (new)
    side, so the commentable positions are the new-file line numbers of the
    *added* and *context* lines in each hunk. Removed (``-``) lines exist only on
    the LEFT side and are excluded.

    The parser walks each ``diff --git`` file section, reads the new-file start
    line from each ``@@ -a,b +c,d @@`` hunk header, and increments a counter for
    every added/context line (skipping removed lines and the ``\\ No newline``
    marker). It is intentionally small: it understands the subset of unified-diff
    syntax `git diff` emits, which is all :func:`diff.resolve_pr` produces.
    """
    result: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    in_hunk = False

    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            path = None
            in_hunk = False
            continue
        if raw.startswith("+++ "):
            # "+++ b/path" (or "+++ /dev/null" for a deletion). Strip the b/.
            target = raw[4:].strip()
            if target == "/dev/null":
                path = None
            else:
                path = target[2:] if target.startswith(("a/", "b/")) else target
                result.setdefault(path, set())
            in_hunk = False
            continue
        if raw.startswith("@@"):
            # @@ -old_start,old_count +new_start,new_count @@
            new_line = _parse_hunk_new_start(raw)
            in_hunk = path is not None
            continue
        if not in_hunk or path is None:
            continue
        if raw.startswith("\\"):
            # "\ No newline at end of file" — not a content line.
            continue
        if raw.startswith("-"):
            # removed line: LEFT side only, no RIGHT-side number consumed.
            continue
        # added ("+") or context (" " / empty) line: both occupy a RIGHT-side
        # new-file line number, and GitHub accepts a comment on either.
        result[path].add(new_line)
        new_line += 1

    return result


def _parse_hunk_new_start(header: str) -> int:
    """Extract ``new_start`` from a ``@@ -a,b +c,d @@`` hunk header (defaults 1)."""
    # Find the "+c,d" token.
    for token in header.split():
        if token.startswith("+"):
            spec = token[1:]
            start = spec.split(",", 1)[0]
            try:
                return int(start)
            except ValueError:
                return 1
    return 1


def _comment_body(agent_name: str, severity: str, text: str, code_snippet: str | None) -> str:
    """The inline-comment body: an ``Agent: <name> [SEV]`` prefix, the text, and
    an optional fenced code snippet."""
    body = f"Agent: {agent_name} [{severity}]\n{text}"
    if code_snippet:
        body += f"\n\n```\n{code_snippet}\n```"
    return body


def build_review_payload(
    review: dict,
    ctx: PRContext,
    *,
    agent_name: str,
    event: str | None = None,
) -> dict:
    """Map a review JSON (``REVIEW_SCHEMA`` shape) to a GitHub create-review payload.

    Returns the body for ``POST /repos/{owner}/{repo}/pulls/{n}/reviews``:

    * ``commit_id`` is pinned to ``ctx.head_sha`` — anchoring to the head sha
      avoids GitHub rejecting comments whose lines moved since an earlier sha.
    * ``event`` is derived from ``review["summary"]["status"]``
      (APPROVED→APPROVE, REQUEST_CHANGES→REQUEST_CHANGES, COMMENT→COMMENT)
      UNLESS ``event`` is passed explicitly, in which case the override wins.
      **Self-review constraint:** GitHub 422s an ``APPROVE`` /
      ``REQUEST_CHANGES`` on your OWN PR. Phase 2.1 posts as the user's own
      account, so the caller passes ``event="COMMENT"`` to stay safe; this
      function honors that override.
    * ``body`` is a ``Agent: <name>`` header line followed by the summary's
      ``overall_feedback`` — the prefix is the Phase-2.1 way to tell agents apart
      before the bot-app phase. Any finding that is NOT anchored to a changed
      diff line is appended here under a "Findings not anchored to changed lines"
      section rather than emitted as an inline comment (an unanchored inline
      comment would 422 the entire review).
    * ``comments[]`` holds one ``{path, line, side: "RIGHT", body}`` entry per
      finding whose ``(file, line)`` IS a commentable RIGHT-side diff position.
    """
    summary = review.get("summary") or {}
    status = summary.get("status", "COMMENT")
    overall_feedback = summary.get("overall_feedback", "")

    resolved_event = event if event is not None else _STATUS_TO_EVENT.get(status, "COMMENT")

    anchorable = commentable_lines(ctx.diff)

    comments: list[dict] = []
    unanchored: list[str] = []
    for finding in review.get("comments") or []:
        file = finding.get("file", "")
        line = finding.get("line")
        text = finding.get("text", "")
        severity = finding.get("severity", "INFO")
        code_snippet = finding.get("code_snippet") or ""

        is_anchored = isinstance(line, int) and line in anchorable.get(file, set())
        if is_anchored:
            comments.append(
                {
                    "path": file,
                    "line": line,
                    "side": "RIGHT",
                    "body": _comment_body(agent_name, severity, text, code_snippet),
                }
            )
        else:
            snippet = f"\n\n```\n{code_snippet}\n```" if code_snippet else ""
            unanchored.append(f"- `{file}:{line}` [{severity}] {text}{snippet}")

    body = f"Agent: {agent_name}\n\n{overall_feedback}".rstrip()
    if unanchored:
        body += "\n\n### Findings not anchored to changed lines:\n" + "\n".join(unanchored)

    payload: dict = {
        "commit_id": ctx.head_sha,
        "event": resolved_event,
        "body": body,
    }
    if comments:
        payload["comments"] = comments
    return payload


def _resolve_repo(ctx: PRContext) -> str:
    """The ``OWNER/NAME`` slug to POST to: ``ctx.repo`` if set, else inferred via
    ``gh repo view``. Raises a clear error if it can't be determined."""
    if ctx.repo:
        return ctx.repo
    try:
        slug = gh.repo_view(json_fields=["nameWithOwner"], jq=".nameWithOwner")
    except gh.GhError as exc:
        raise RuntimeError(
            "Could not determine the repository to post the review to: ctx.repo is "
            f"unset and `gh repo view` failed ({exc}). Pass --repo OWNER/NAME."
        ) from exc
    slug = (slug or "").strip()
    if not slug:
        raise RuntimeError(
            "Could not determine the repository to post the review to (empty "
            "`gh repo view` result). Pass --repo OWNER/NAME."
        )
    return slug


def post_review(
    review: dict,
    ctx: PRContext,
    *,
    agent_name: str,
    event: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Build the grouped-review payload and (unless ``dry_run``) POST it.

    With ``dry_run=True``: prints the payload as pretty JSON and returns it,
    WITHOUT calling ``gh`` — safe to run anywhere.

    Otherwise POSTs via
    ``gh.rest("/repos/{repo}/pulls/{n}/reviews", method="POST", body=payload)``
    (``rest`` serializes the dict ``body`` to JSON and pipes it to
    ``gh api --input -``) and returns the parsed API response. Raises
    ``RuntimeError`` on a ``gh`` failure with an actionable message.
    """
    payload = build_review_payload(review, ctx, agent_name=agent_name, event=event)

    if dry_run:
        print(json.dumps(payload, indent=2))
        return payload

    repo = _resolve_repo(ctx)
    path = f"/repos/{repo}/pulls/{ctx.number}/reviews"
    try:
        response = gh.rest(path, method="POST", body=payload)
    except gh.GhError as exc:
        raise RuntimeError(f"Failed to post review to {repo}#{ctx.number}: {exc}") from exc
    return response if isinstance(response, dict) else {"response": response}
