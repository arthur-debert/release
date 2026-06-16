"""ProjectSession spike — exercise the SDK and surface what we see.

This is deliberately exploratory. The exact Python SDK shape (option
names, message types, attribute names) gets discovered on first run;
the `--verbose` flag dumps raw messages to stderr so we can iterate.
"""

import contextlib
import os
import sys
from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from . import state


def _force_blocking_stdio() -> None:
    """Force stdout/stderr to BLOCKING mode before the SDK stream loop.

    Under a piped or backgrounded parent (CI, `orc livefire --all`, any captured
    subprocess) these fds can be non-blocking — and a heavy `--verbose` /
    streamed-text write then raises ``BlockingIOError: [Errno 35]`` (EAGAIN) and
    aborts an otherwise-successful run (caught live-firing rustloc, release#663).
    Blocking mode makes the write WAIT for the reader to drain instead of failing.
    """
    for stream in (sys.stdout, sys.stderr):
        # not a real fd (captured StringIO, closed stream, …) → nothing to set
        with contextlib.suppress(OSError, ValueError, AttributeError):
            os.set_blocking(stream.fileno(), True)


async def run_session(
    repo_path: str,
    prompt: str,
    resume: bool = False,
    verbose: bool = False,
    permission_mode: str = "acceptEdits",
    persist_session: bool = True,
    text_sink: list[str] | None = None,
    followup_prompt: str | None = None,
    needs_followup: Callable[[str], bool] | None = None,
) -> str | None:
    """Send `prompt` to a session pinned at `repo_path`.

    `permission_mode` controls how the subordinate agent's tool calls are
    gated. Defaults to `acceptEdits` (auto-accept file edits, prompt for
    command execution) — safe for sessions running against a user-owned
    repo. The `probe` subcommand passes `bypassPermissions` so eval prompts
    can execute lint/test commands; that mode is only safe against a
    throwaway clone.

    `persist_session` controls whether the discovered session_id is saved
    to the sessions store. Defaults to True for normal `run`/`resume`
    flows. `probe` passes False because probes are one-shot fresh-agent
    evaluations — picking one up later via `orc resume` would surprise
    the user.

    `text_sink`, when provided, accumulates the agent's streamed text blocks
    (the same text printed to stdout) so a caller can harvest the full
    transcript — `orc livefire` uses it to extract the structured feedback
    block from the agent's final answer.

    Returns the discovered session_id (if any).
    """
    if bool(followup_prompt) != bool(needs_followup):
        raise ValueError(
            "pass BOTH followup_prompt and needs_followup, or neither — one "
            "without the other silently disables the prod."
        )
    if followup_prompt and text_sink is None:
        raise ValueError(
            "followup_prompt/needs_followup require text_sink (the prod predicate "
            "reads the accumulated transcript) — pass a text_sink list."
        )
    _force_blocking_stdio()
    repo_path = str(Path(repo_path).expanduser().resolve())
    resume_id = state.get(repo_path) if resume else None

    opts_kwargs: dict = {
        "cwd": repo_path,
        "setting_sources": ["project"],
        "permission_mode": permission_mode,
    }
    if resume_id:
        opts_kwargs["resume"] = resume_id

    try:
        opts = ClaudeAgentOptions(**opts_kwargs)
    except TypeError as e:
        # SDK may use a different parameter name for resume; degrade gracefully.
        if resume_id and "resume" in str(e):
            print(
                f"[warn] SDK rejected resume= ({e}); starting fresh session.",
                file=sys.stderr,
            )
            opts_kwargs.pop("resume")
            opts = ClaudeAgentOptions(**opts_kwargs)
        else:
            raise

    discovered_session_id: str | None = None

    async with ClaudeSDKClient(options=opts) as client:

        async def _drain() -> None:
            nonlocal discovered_session_id
            async for msg in client.receive_response():
                if verbose:
                    print(f"[msg] {type(msg).__name__}: {msg!r}", file=sys.stderr)
                sid = getattr(msg, "session_id", None)
                if sid:
                    discovered_session_id = sid
                content = getattr(msg, "content", None)
                if content:
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            print(text, end="", flush=True)
                            if text_sink is not None:
                                text_sink.append(text)
            print()  # final newline after streamed content

        await client.query(prompt)
        await _drain()

        # Prod ONCE for a required deliverable the agent skipped. After a long
        # task an agent often stops without its final structured output (e.g.
        # livefire's feedback block); a single same-session nudge recovers it
        # without re-running the whole task (release#683).
        if (
            followup_prompt
            and needs_followup
            and text_sink is not None
            and needs_followup("".join(text_sink))
        ):
            print("[orc] required output missing — prodding once", file=sys.stderr)
            await client.query(followup_prompt)
            await _drain()

    if discovered_session_id and persist_session:
        state.set_(repo_path, discovered_session_id)

    return discovered_session_id
