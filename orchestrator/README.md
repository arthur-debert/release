# orchestrator (🚧 spike)

Local Python harness for driving multi-repo work via the
[Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk).
Single-process, subscription-billed.

**Status.** Phase A spike. Validates that we can open, persist, and
resume sessions via the SDK. The gh-event-trigger layer (Phase B)
is the next research question.

## Run the spike

```
cd orchestrator
uv sync
unset ANTHROPIC_API_KEY    # subscription billing only
uv run tests/spike.sh
```

Read the output. Step 3 should reference step 1's content if
session persistence is working.

The CLI hard-fails at startup if `ANTHROPIC_API_KEY` is set, to
avoid accidentally billing API credits while iterating.

## CLI

```
orc run <repo-path> "<prompt>"       # open a fresh session for repo
orc resume <repo-path> "<prompt>"    # continue the last session for repo
orc sessions list                    # show {repo_path: session_id}
orc sessions clear <repo-path>       # drop session id for repo
```

Add `-v` / `--verbose` to dump raw SDK messages to stderr — useful
while exploring the SDK surface.

## Layout

- `orchestrator/cli.py` — `orc` entry point
- `orchestrator/session.py` — `run_session()` wrapping `ClaudeSDKClient`
- `orchestrator/state.py` — JSON-backed `{repo_path: session_id}` store
  at `~/.local/state/release-orchestrator/sessions.json`
- `tests/spike.sh` — end-to-end smoke check

## What's deliberately not here yet

- Multi-repo fan-out
- GH-event-driven session wake-up (Phase B)
- Permission-callback gating per project
- Pruning of session state (text-only today; revisit when state grows)
