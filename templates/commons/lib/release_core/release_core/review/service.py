"""service — the programmatic run-and-post path for a local review backend.

The `release-core review run` facade is the argv/CLI entry; this module is the
in-process equivalent that other code (notably the `prstate` reviewer adapters)
calls to GENERATE a review and POST it, without shelling back through argv.

Two functions, layered:

  * :func:`generate_review` — resolve the backend, preflight it, build the shared
    prompt over a resolved PR's diff, and run the backend → the parsed review
    dict. No GitHub posting.
  * :func:`run_and_post` — resolve the PR, generate the review, and post it via
    :func:`release_core.review.post.post_review`, returning a small result dict.

`prstate` may import this module (`prstate → review` is a one-way edge — `review`
never imports `prstate`), so the reviewer adapters' synchronous `request` can run
a local review here.
"""

from __future__ import annotations

from . import ghapp, post
from .backends import get_backend
from .diff import resolve_pr
from .instructions import load_instructions
from .prompt import build_prompt
from .schema import REVIEW_SCHEMA


def generate_review(
    agent: str,
    ctx,
    *,
    instructions_path: str | None = None,
    model: str = "pro",
) -> dict:
    """Run ``agent`` over ``ctx``'s diff and return the parsed review dict.

    Resolves the backend, preflights it (a missing CLI raises
    :class:`~release_core.review.backends.base.BackendUnavailable`, which is
    allowed to propagate — these are LOCAL backends and a missing binary must
    fail loud), loads the review instructions (bundled default unless
    ``instructions_path`` is given), builds the shared prompt over ``ctx.diff``
    (with the schema described in-prose only for ``agy``, which has no native
    schema enforcement), and runs the backend in ``ctx.workdir``.
    """
    backend = get_backend(agent, model=model)
    backend.preflight()
    instructions = load_instructions(instructions_path)
    prompt = build_prompt(instructions, ctx.diff, schema_inline=(agent == "agy"))
    return backend.run(prompt, REVIEW_SCHEMA, cwd=ctx.workdir)


def run_and_post(
    agent: str,
    pr: int,
    *,
    repo: str | None = None,
    model: str = "pro",
    instructions_path: str | None = None,
    event: str | None = "COMMENT",
    as_app: bool = True,
    dry_run: bool = False,
) -> dict:
    """Resolve ``pr``, generate a review with ``agent``, and post it.

    Returns ``{"review": <dict>, "post": <dict>, "ctx_repo": <str|None>,
    "pr": <int>}``.

    With ``as_app=True`` (the default), the review is posted AS the agent's
    GitHub App — which requires the app to have been registered locally
    (``release-core review app manifest`` / ``register``). If no app metadata is
    found for ``agent`` this raises a clear :class:`RuntimeError` telling the user
    to register first, rather than silently falling back to posting as the user.
    """
    if as_app and ghapp.load_app(agent) is None:
        raise RuntimeError(
            f"No GitHub App is registered for the {agent!r} review backend, so it "
            f"cannot post as itself. Run `release-core review app manifest {agent}` "
            f"then `release-core review app register {agent} --code <CODE>` first "
            f"(or pass as_app=False to post as your own gh account)."
        )

    ctx = resolve_pr(pr, repo=repo)
    review = generate_review(agent, ctx, instructions_path=instructions_path, model=model)
    result = post.post_review(
        review,
        ctx,
        agent_name=agent,
        event=event,
        dry_run=dry_run,
        as_app=as_app,
    )
    return {"review": review, "post": result, "ctx_repo": ctx.repo, "pr": pr}
