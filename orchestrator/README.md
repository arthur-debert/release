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
orc livefire <owner/name> --yes      # live-fire verification on one consumer (release#663)
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

One human gate is never automated: the **merge** (READY flips draft→ready and
pages you). Under the stopping rule (6 rounds / all-nitpick) an otherwise-ready
PR routes to READY on its own, so a `BLOCKED` status is always a real, fixable
blocker (failing CI, merge conflict, behind base) — never a "stop everything"
breaker. The watcher fixes it like any other block.

| Transition | notify-only | `--auto` |
|---|---|---|
| `ADDRESSING` / `BLOCKED` (check/conflict) | ping you to drive | spawn a fresh fixer agent |
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
release/ changes get from "implemented" to "conformant": a fresh
agent's empirical experience IS the test.

**Important:** probe uses `bypassPermissions`. Run it only against a
throwaway clone of a consumer repo, never against your working tree.
The clone bounds the blast radius; the widened permissions let the
agent actually execute lint/test commands rather than just describing
them.

### Probes boot the clone first (release#578)

SDK-launched sessions never fire the consumer's SessionStart hook, so a
bare probe would evaluate a stale, unbooted tree — that produced false
PASSes in two validation rounds. Before launching the agent session,
`orc probe` now runs the clone's own real boot chain
(`bin/setup-dev-env.sh` → `install-release-core` → `release-core init`),
exactly as a real session would, and then **boot-asserts** that the
managed sync actually applied:

- `.release/.release-sync-source` exists and names a source sha/ref
  (assert a — init installed the managed files into `.release/`);
- the clone's git `info/exclude` carries the WS7 managed-mirrors block
  sentinel (assert b — init rewrote the mirrors exclude block).

The asserts check **installed state, never commits** — an
already-converged clone commits nothing, and that is success. A boot
report (provenance ref, `release-core --version`, whether this boot
created a sync commit) is printed to stderr before the session starts.

Any boot failure — missing `bin/setup-dev-env.sh`, non-zero exit, or a
failed assert — **aborts the probe by design**: an unbooted probe's
findings describe a stale world, not what we shipped, so they are
invalid. There is no fallback boot path. The eval prompt stays
hint-free; only the boot is explicit.

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
release/) violates the shared markdownlint config (MD041) — then
shipped by the `shell-quality` Component, now part of `templates/commons/`.
That's the value loop in one example — static review missed it; the
fresh agent's `lefthook run` surfaced it.

## `orc livefire` — the live-fire verification harness (release#663)

Where `probe` *evaluates* a clone, `livefire` makes a fresh agent *live the
whole managed loop* on a real consumer and reports back what was rough. It is
the runner for the standard live-fire prompt
([`docs/dev/live-fire-prompt.md`](../docs/dev/live-fire-prompt.md), release#663.2).

```sh
orc livefire arthur-debert/padz --yes              # one consumer
orc livefire a/b c/d --yes --concurrency 2          # several, 2 at a time
orc livefire --all --yes                            # every registered consumer
orc livefire arthur-debert/padz --yes --dry-run     # agent runs; no filing/teardown
```

Per consumer, one run:

1. **Clones** `<owner/name>` fresh from GitHub into a throwaway dir (origin is
   the real remote, so the agent's coverage PR is real — the value left behind)
   and **boots** it (same fail-loud `boot_clone` as `probe`).
2. **Runs the standard prompt** via a fresh agent (`bypassPermissions`; the
   clone bounds the blast radius). The prompt is loaded verbatim from the
   ```` ```text ```` fence in `live-fire-prompt.md` — one source, shared with
   the human doc.
3. **Harvests** the structured `yaml` feedback block the agent ends with.
4. **Files** each non-`ok` finding into the release#348 inbox via
   `release-core issue file <component> …` (grouped by component there).
5. **Tears down** the throwaway `-release-rc` tag + GH pre-release (the prepare
   step never advanced the branch — #663.1 — so nothing else needs reverting).

**Fleet rollout.** Pass several `<owner/name>` (or `--all`, which pulls the
registry via `release-core admin repos list`) to run consumers in parallel,
capped by `--concurrency` (default 3). Results are aggregated into a rollout
report (counts by verdict, total findings filed, the `errored` list); one
consumer's failure is captured there, never fatal, and the command exits
non-zero if any errored. Each run clones into its own temp dir, so concurrent
runs don't collide.

`--yes` is required (real PR + real `-release-rc` cut on each consumer);
`--dry-run` skips only the side-effecting filing + teardown — the agent run
still happens, because that IS the verification. The pure pieces (prompt load,
feedback parse, finding→issue mapping, teardown command, consumer selection,
rollout aggregation) are unit-tested in `tests/test_livefire.py`.

## Layout

- `orchestrator/cli.py` — `orc` entry point
- `orchestrator/boot.py` — `boot_clone()`: the probe's explicit, fail-loud
  consumer-clone boot + boot-assert + boot report (release#578; pure stdlib,
  unit-tested in `tests/test_boot.py` against fake boot scripts)
- `orchestrator/session.py` — `run_session()` wrapping `ClaudeSDKClient`
- `orchestrator/watch.py` — `orc watch` poll-loop + pure `decide()` dispatch
  (imports `release_core.prstate.state`; lazy-imports the SDK so its logic
  unit-tests without it — `templates/commons/lib/release_core/tests/test_prstate_watch.py`)
- `orchestrator/state.py` — JSON-backed `{repo_path: session_id}` store
  at `~/.local/state/release-orchestrator/sessions.json`
- `tests/spike.sh` — end-to-end smoke check
- `tests/test_boot.py` — pytest suite for `boot_clone()` (no network, no SDK;
  collected by the workspace-root `pytest`, or run
  `uv run --project orchestrator pytest orchestrator/tests` directly)

## What's deliberately not here yet

- Multi-repo fan-out within a single `orc watch` (it watches PRs in one repo)
- Cloud/webhook transport for the "survive laptop-off" case — `orc watch`
  covers detached-while-machine-on; true routines are a later, Cloud-coupled add
- Persisting `orc watch` transition memory across restarts (in-memory today)
- Permission-callback gating per project
- Pruning of session state (text-only today; revisit when state grows)
