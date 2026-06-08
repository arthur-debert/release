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
orc watch <pr>... [--auto]           # poll PRs, act on lifecycle transitions
orc sessions list                    # show {repo_path: session_id}
orc sessions clear <repo-path>       # drop session id for repo
```

Add `-v` / `--verbose` to dump raw SDK messages to stderr — useful
while exploring the SDK surface.

## `orc watch` — the detached PR transport (release#338)

The poll-loop transport for the PR state engine. Instead of webhooks, a
long-running local process imports `release_core.prstate.state` and polls a few PRs
(~zero agent tokens — it's `gh` calls, not an awake agent), dispatching only
when a PR's lifecycle *state changes*.

```sh
orc watch 350 351                 # notify-only: ping you at the moments that matter
orc watch 350 --auto              # full auto-fix: spawn a fresh agent on ADDRESSING/BLOCKED
orc watch 350 --repo ~/h/dodot --interval 60
```

Two human gates are never automated: the **merge** (READY flips draft→ready and
pages you) and a fired **circuit breaker** (always pages, never acts).

| Transition | notify-only | `--auto` |
|---|---|---|
| `ADDRESSING` / `BLOCKED` (check/conflict) | ping you to drive | spawn a fresh fixer agent |
| `BLOCKED` (breaker) | page — never act | page — never act |
| `READY` | flip draft→ready, page | flip draft→ready, page |
| pending / validating | quiet | quiet |

**Why a fresh agent, not session-resume:** resume reloads the whole
implementation conversation (100–200k tokens) every wake, goes stale when
detached, and is defensive about its own code. The fixer is fresh and reads its
context — the `/handoff` note the PR-opening agent left, the linked spec/issue,
and `gh-task-status`. It runs `bypassPermissions` in a **throwaway clone** of the
PR branch (blast radius bounded, like `probe`), pushes fixups to the PR, and is
removed after. It never merges.

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
agent in evaluator mode rather than coder mode.

### Worked example

Validating a Component-model adoption against a fresh dodot clone:

```sh
clone=$(mktemp -d)/dodot
git clone ~/h/dodot "$clone"
( cd "$clone" && RELEASE_REF=take-iii release-sync )
orc probe --yes "$clone" "$(cat <<'EOF'
This is not a coding task — we are evaluating your environment setup.
1. From .release-sync-state.yaml, list Components and ref.
2. List the pre-commit command names from lefthook.yml.
3. Run `lefthook run pre-commit --all-files | head -80`. Report results.
4. Verdict: one line.
EOF
)"
```

A representative reply structures the answer as numbered Markdown
sections, ending with a verdict like:

> **Verdict:** Setup looks coherent — all tools installed, lefthook
> executes; the two lint failures are repo content issues, not
> environment misconfiguration.

The probe found a real bug on its first run against the Component
model: `templates/rust/.github/pull_request_template.md` (shipped by
release/) violates the canonical markdownlint config (MD041) — then
shipped by the `shell-quality` Component, now part of `templates/commons/`.
That's the value loop in one example — static review missed it; the
fresh agent's `lefthook run` surfaced it.

## Layout

- `orchestrator/cli.py` — `orc` entry point
- `orchestrator/session.py` — `run_session()` wrapping `ClaudeSDKClient`
- `orchestrator/watch.py` — `orc watch` poll-loop + pure `decide()` dispatch
  (imports `release_core.prstate.state`; lazy-imports the SDK so its logic
  unit-tests without it — `templates/commons/lib/release_core/tests/test_prstate_watch.py`)
- `orchestrator/state.py` — JSON-backed `{repo_path: session_id}` store
  at `~/.local/state/release-orchestrator/sessions.json`
- `tests/spike.sh` — end-to-end smoke check

## What's deliberately not here yet

- Multi-repo fan-out within a single `orc watch` (it watches PRs in one repo)
- Cloud/webhook transport for the "survive laptop-off" case — `orc watch`
  covers detached-while-machine-on; true routines are a later, Cloud-coupled add
- Persisting `orc watch` transition memory across restarts (in-memory today)
- Permission-callback gating per project
- Pruning of session state (text-only today; revisit when state grows)
