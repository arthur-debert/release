"""ProjectSession spike — exercise the SDK and surface what we see.

This is deliberately exploratory. The exact Python SDK shape (option
names, message types, attribute names) gets discovered on first run;
the `--verbose` flag dumps raw messages to stderr so we can iterate.
"""

import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from . import state


async def run_session(
    repo_path: str,
    prompt: str,
    resume: bool = False,
    verbose: bool = False,
    permission_mode: str = "acceptEdits",
) -> str | None:
    """Send `prompt` to a session pinned at `repo_path`.

    `permission_mode` controls how the subordinate agent's tool calls are
    gated. Defaults to `acceptEdits` (auto-accept file edits, prompt for
    command execution) — safe for sessions running against a user-owned
    repo. The `probe` subcommand passes `bypassPermissions` so eval prompts
    can execute lint/test commands; that mode is only safe against a
    throwaway clone.

    Returns the discovered session_id (if any), and persists it so future
    `resume=True` calls pick it up.
    """
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
        await client.query(prompt)
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

        print()  # final newline after streamed content

    if discovered_session_id:
        state.set_(repo_path, discovered_session_id)

    return discovered_session_id
