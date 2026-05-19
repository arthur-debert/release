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
orc run <repo-path> "<prompt>"       # open a fresh session for repo (acceptEdits)
orc resume <repo-path> "<prompt>"    # continue the last session for repo
orc probe <repo-path> "<prompt>"     # evaluate via fresh agent (bypassPermissions)
orc sessions list                    # show {repo_path: session_id}
orc sessions clear <repo-path>       # drop session id for repo
```

Add `-v` / `--verbose` to dump raw SDK messages to stderr — useful
while exploring the SDK surface.

## `orc probe` — verification by proxy

The probe verb spins a fresh subordinate agent in `<repo-path>` and
sends it an eval prompt. The agent reports back what it sees, runs
the lint/test commands you ask it to, and gives a verdict. This is how
release/ changes get from "implemented" to "pilot-running": a fresh
agent's empirical experience IS the test.

**Important:** probe uses `bypassPermissions`. Run it only against a
throwaway clone of a consumer repo, never against your working tree.
The clone bounds the blast radius; the widened permissions let the
agent actually execute lint/test commands rather than just describing
them.

### Eval prompt convention

Probe prompts begin with the marker line:

> This is not a coding task — we are evaluating your environment setup.

Followed by numbered, answerable items:

```
1. Show the contents of <file>. <specific question about it>.
2. Run `<command>`. Report the output.
3. Is <tool> installed (`command -v <tool>`)?
4. Anything that looks broken or unexpected?
End with a one-line verdict: "Setup looks coherent / Setup has issues: <brief>".
```

The marker line + "report findings, not commentary" pattern keeps the
agent in evaluator mode rather than coder mode. See the PR that
introduced this verb for a worked example (Component-model validation
against a dodot clone).

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
