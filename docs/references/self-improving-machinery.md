# The self-improving machinery under the minimal footprint (WS7)

The keep / fold / drop decisions for epic #501's WS7 (release#528), made
2026-06-10 against the post-WS6 state: the wheel is the sole carrier, the
consumer's tracked footprint is the bootstrap quartet + GitHub-forced files +
thin `@vN` callers + the optional `.release-sync.yaml`.

The governing rule (the WS7 exit line): **anything kept is carried by the
binary, not synced as tracked files.**

## Decisions

| Component | Decision | Carried how | Rationale |
|---|---|---|---|
| Consumer-filed inbox (`release-core admin inbox`) | **keep** | binary (console tree) | Maintainer-side triage over `consumer-filed` issues; zero consumer footprint. The read-side of the feedback loop. |
| Escalation relay — `gh-release-issue` / `release-core issue file` | **keep** | binary (console-script) | The write-side. The *contract* ("unblock locally, escalate upstream") lives in the CLAUDE.md stub + `release-core how-to`. |
| `release-issue-relay` synced skill | **drop from distribution** | — | WS2's exit allows "at most one thin delegating skill"; this was the second. The contract + mechanism are binary-carried (above); a skill file added discovery surface without adding function. Consumers' copies are swept by the broken-symlink cleanup on their next init. |
| `notify-source` (`admin inbox notify-source`) | **keep** | binary | Close-the-loop notifications; maintainer-side only. |
| PR state engine (`gh-task-status`) | **keep** | binary (console-script) | The dev-cycle epic (#547) builds *on* it (state-engine-owned waits, draft↔ready, thread-state done-signal). |
| PreToolUse PR-loop guard (`bin/pr-loop-guard` + `.claude/settings.json`) | **keep, tracked** | bootstrap quartet (real files, WS5) | The one sanctioned tracked exception: enforcement must exist from the first session in a fresh clone — the boot chain can't depend on what it boots. |
| `gh-pr-review-loop` synced skill | **keep, ephemeral** | wheel → mirror, untracked | The one thin delegating skill (the `/`-trigger surface the harness needs as a file). Built by init, never tracked. |
| Phase D fleet CI-sweep producer (#370) | **drop** | — | Standing poll machinery against the minimal-footprint grain. The push side (escalation contract) + hand-runs of `admin repos verify` cover it; re-file with fresh context if unescalated CI debt becomes real. |

## The mechanism: ephemeral mirrors

WS7's implementation half generalizes the decisions: **every symlink mirror is
now ephemeral** — `bin/` tool symlinks, `.editorconfig`, and skill mirrors are
built by every `init` (exactly as before) but never tracked:

- init lists every mirror dest in a managed block in **`.git/info/exclude`**
  (not the consumer's `.gitignore` — the point is zero tracked footprint;
  info/exclude is per-clone state, recomposed by every init like the mirrors
  themselves).
- A pre-WS7 seed's *committed* mirrors are untracked by a one-time managed
  commit (the WS4 `.release/` untrack pattern, generalized): the symlinks are
  taken off-disk for the duration of the pathspec commit — a partial commit
  reads the working tree, so an on-disk path would resurrect instead of delete
  — then recreated, so the session's tree stays live.
- Tracked real files are unchanged: the bootstrap quartet and the
  `.github/workflows/` copies remain committed (GitHub/boot-chain-forced).

Fresh-clone behavior is the same as `.release/` itself: mirrors don't exist
until the SessionStart init runs — which is the first thing a session does.

## What this leaves tracked in a consumer

`.github/**` (thin callers + policy files + workflow copies), the bootstrap
quartet (`.claude/settings.json`, `bin/install-release-core`,
`bin/setup-dev-env.sh`, `bin/pr-loop-guard`), and optionally
`.release-sync.yaml`. Everything else is the binary's output: `.release/`
(gitignored), mirrors (excluded), guidance (`release-core how-to`).
