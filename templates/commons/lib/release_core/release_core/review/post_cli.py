"""post_cli — ``release-core review post``: post a review JSON to a PR.

The companion to ``review run`` (which only *generates* a review). This leaf
takes a review JSON (the :data:`release_core.review.schema.REVIEW_SCHEMA` shape,
on stdin or ``--input``), resolves the PR for its head sha + diff, and posts a
single GitHub grouped review via :mod:`release_core.review.post`.

``run`` and ``post`` stay separate on purpose — Phase 3 composes them; this
phase keeps "generate" and "publish" as two explicit steps.

The default ``--event`` is ``COMMENT`` because Phase 2.1 posts as the user's own
account and GitHub 422s an APPROVE / REQUEST_CHANGES on your own PR.

``--as-app`` switches the auth: instead of the user's ``gh`` login, the review
is posted AS the agent's GitHub App installation (``<slug>[bot]``) by minting a
short-lived installation token. The default stays user-auth.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import post
from .diff import ReviewError, resolve_pr


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="release-core review post",
        description="Post a review JSON to a GitHub PR as one inline grouped review.",
    )
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="PR number to post the review to (resolved via `gh pr view`).",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="OWNER/NAME of the PR's repo (default: inferred from the current checkout).",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Path to the review JSON file (default: read from stdin).",
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Agent label prefixed on the review body + each comment (e.g. 'codex').",
    )
    parser.add_argument(
        "--event",
        choices=["COMMENT", "REQUEST_CHANGES", "APPROVE"],
        default="COMMENT",
        help="GitHub review event (default: COMMENT — safe for self-review).",
    )
    parser.add_argument(
        "--as-app",
        action="store_true",
        help=(
            "Post AS the agent's GitHub App installation (authored by "
            "<slug>[bot]) instead of the user's gh login. Default: post as the user."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the GitHub payload that would be posted; do not call gh.",
    )
    return parser.parse_args(argv)


def _load_review(input_path: str | None) -> dict:
    """Read + parse the review JSON from ``--input`` (or stdin)."""
    if input_path is None:
        raw = sys.stdin.read()
    else:
        with open(input_path, encoding="utf-8") as fh:
            raw = fh.read()
    return json.loads(raw)


def main(argv: list[str]) -> int:
    """Entry point for ``release-core review post``. Returns a process exit code."""
    args = _parse_args(argv)

    try:
        review = _load_review(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read review JSON: {exc}", file=sys.stderr)
        return 1

    try:
        ctx = resolve_pr(args.pr, repo=args.repo)
    except ReviewError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        result = post.post_review(
            review,
            ctx,
            agent_name=args.agent,
            event=args.event,
            dry_run=args.dry_run,
            as_app=args.as_app,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.dry_run:
        url = result.get("html_url") if isinstance(result, dict) else None
        print(f"Posted review to PR #{args.pr}" + (f": {url}" if url else ""))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
