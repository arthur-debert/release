---
name: gh-pr-review-loop
description: "Drive a PR to ready-for-human-merge in any repo managed by `arthur-debert/release`. The loop is state-machine-driven: `release-core pr status` reports one lifecycle state plus the single next action; do that action, re-read, repeat — open as a draft, request the required reviews, wait in-turn, triage and resolve threads — and `release-core pr wait` flips draft→ready itself once the engine reaches READY, then stop (the human merges). Use when opening a PR, checking where a PR stands, waiting on or triaging review feedback, or driving a PR toward merge-readiness. Triggered by: `gh pr create`, 'check PR status', 'where does this PR stand', requesting a review, or processing review comments."
---

# gh-pr-review-loop

The practiced discipline of the one draft-first dev cycle. The model lives in
`docs/dev-cycle.lex` (in `arthur-debert/release`); the rendered orientation is
`release-core how-to`; this skill is how an agent actually drives it. The loop
is owned by a state machine — you never piece PR state together by hand.

Reviewer-agnostic: reviews are handled identically whether the reviewer is a
human or any bot (Copilot, Gemini, CodeRabbit, …). Bot names and mechanics live
in the reviewer adapter registry behind `release-core pr review`, not in this
skill.

**The required reviewer set.** The default required set is **Copilot**,
**review-once** (`rerun: false`). CodeRabbit is a second requestable reviewer
being piloted on the phos-org repos (the only place its GitHub App is
installed); a pilot repo opts in via the `reviewers:` map in its
`.release-sync.yaml`. Where a repo requires several reviewers they gate **in
parallel** (release#622), not primary-plus-fallback: a PR is `REVIEWED` only
when **every** required reviewer has a counting review; `release-core pr ready`
requires all of them. The trade-off is availability: one required reviewer's
outage holds the PR at `REVIEWS_PENDING` until it recovers — accepted, not a
bug. The engine names the outstanding reviewer in the next action, so a
single-reviewer stall is visible, not silent.

**Re-run on push is per-reviewer config, default OFF (review once).** All
reviewers are token-billed now (local agents cost a real model run each time),
so re-reviewing each new head is explicit opt-in via a reviewer's `rerun: true`.
A default review-once reviewer's review counts on **any** head and is never
stale-after-push; only a `rerun: true` reviewer is re-requested after a push.

## Reading the per-reviewer line in `pr status`

`release-core pr status` prints a per-reviewer breakdown as
`name=lifecycle` pairs (two-space separated), e.g.
`copilot=requested  gemini=in_progress`. This is informational detail
*under* the single lifecycle state — you still act on the one next action,
not on a raw reviewer field. The names are the registered adapters and the
lifecycles are the adapter states; both are a fixed, enumerable set, so an
unfamiliar pair is not a new concept to learn:

- **Reviewer names** (the adapter registry — `prstate/reviewers.py`):
  - `copilot` — requestable; the default required reviewer.
  - `coderabbit` — requestable; the phos-org pilot (opt-in via the
    `reviewers:` map).
  - `gemini` — auto-triggering, best-effort; *not* requestable, so never a
    required gate. It appears in the line whenever it has acted, but a
    timed-out Gemini is treated as skipped rather than blocking Ready.
- **Lifecycle states** (`ReviewLifecycle`):
  - `not_requested` — no review and no pending request on the current head.
  - `requested` — a review request is attached; the reviewer hasn't acted yet.
  - `in_progress` — the reviewer is actively looking (e.g. Gemini's "eyes"
    reaction); not yet done.
  - `done_clean` — finished and left **no** comment threads.
  - `done_comments` — finished and left comment threads (triage them).

A push stales only a `rerun: true` reviewer back toward
`not_requested`/`requested` (re-request that one); a default review-once
reviewer's review counts on any head and is not re-requested. A bare
`release-core pr review request` does this for you — it skips reviewers already
done and re-requests only those not done. The done-signal for the round is
still **zero unresolved threads**, engine-computed — never a manual read of
these pairs.

## The loop

```text
1. branch + change + commit (+ changelog fragment, same PR)
2. push
3. arm the guard (SEPARATE step), then open the PR AS A DRAFT, linking the issue
4. loop:
     release-core pr status        → one state + the ONE next action
     do that action                → request review / triage threads / fix CI /
                                     release-core pr wait
     re-read
5. at READY: the flip is AUTOMATIC — release-core pr wait performs the guarded
   draft->ready flip itself when the engine reaches READY, then STOP
   (human's turn; merge only on explicit authorization). pr ready is the
   explicit verb if you land on READY without a wait.
6. ALWAYS end with the final-report contract (below)
```

`release-core pr status [<pr>]` is pure read: one lifecycle state (`NO_PR` /
`REVIEWS_PENDING` / `ADDRESSING` / `REVIEWED` / `VALIDATING` / `READY` /
`BLOCKED`) plus the single next action. Act on the next action, then re-read.
Don't improvise around it.

| State | Your move |
|---|---|
| `NO_PR` | arm the guard, open a draft |
| `REVIEWS_PENDING` | `release-core pr review request` if not yet requested; else `release-core pr wait` |
| `ADDRESSING` | triage the open threads (A/B/C below), resolve as you go |
| `REVIEWED` | mergeability still computing — `release-core pr wait` |
| `VALIDATING` | CI running — `release-core pr wait` |
| `READY` | `release-core pr wait` auto-flips draft→ready here; if you reached READY without a wait, `release-core pr ready`. Then stop |
| `BLOCKED` | a real blocker (failing CI, conflict, behind base) — fix, push, re-read |

## Arming the guard

A PreToolUse guard (`bin/pr-loop-guard`) blocks a bare `gh pr create` so this loop
can't be skipped under task momentum. Before you open the PR, arm it in its
**own step**:

```sh
touch "$(git rev-parse --git-dir)/pr-loop-armed"
```

Why a separate step: the guard runs *before* the `gh pr create` command
executes, so an arm `touch` chained in the **same** command line hasn't run yet
when the guard checks — it'll still deny. Arm first, create second. The arm is
**one-shot** (consumed when the guard allows), so re-arm before each PR. If you
ever see the deny, that is the guard doing its job: arm and retry.

## Draft vs ready = whose turn it is

- **draft = the agent owns it.** Open the PR as a draft (`gh pr create
  --draft`, linking the issue) and keep it draft for the entire cycle:
  implementing, waiting on and addressing reviews, getting CI green, making it
  mergeable. Review requests work on drafts — drafting does not suppress
  reviews.
- **ready = the human's turn.** The flip is the one signal that says "I'm done
  iterating — come validate and merge." You don't hand-flip in the common path:
  `release-core pr wait` performs the guarded flip itself the moment the engine
  reaches READY, so driving the wait to completion *is* the handoff.
- **Re-work flips it back.** If the human asks for changes, flip back to draft
  (`release-core pr ready --undo`), do the work, re-flip when green.

Open as a **live** PR only when the human explicitly asks for one in this
session.

## Who runs which step (the role split — every task)

The agent the human addresses is **always a coordinator that never
implements** — regardless of task size (dev-cycle §intro). It delegates, and
the loop is **split across roles** so no one context carries all of it: an
implementer that also shepherds its own review rounds drags the full
implementation context through every round (single agents have ballooned past
~700k tokens) and judges comments by defending remembered choices instead of
reading the diff cold. This split is unconditional — a single-PR task and a
multi-PR epic use it identically; they differ only in branch/merge topology
(one PR to `main`, vs. workstream PRs into an epic branch).

- **Implementer subagent — stops at PR-open.** Implement, gate, push, open the
  draft PR with the `## Context` note (below), report back, terminate. It
  never sees a review round.
- **Coordinator — owns every wait and the flip.** It blocks on
  `release-core pr wait` (a subagent that yields to wait terminates and is
  never re-woken); the wait performs the guarded draft→ready flip itself on
  reaching READY (`release-core pr ready` remains the explicit fallback).
- **A fresh shepherd subagent per ADDRESSING round.** Brief: the PR number +
  the Context note. Triage the threads (A/B/C below), fix or reply, resolve,
  push, re-request the review, hand the wait back, terminate. Fresh per round:
  a fraction of the tokens, and a cleaner read of each reviewer point.

## Leave a handoff note when you open the PR

Drop a short note capturing the **non-obvious reasoning** behind the change —
the decisions a reviewer (or a later fixer agent) couldn't re-derive from the
diff: why this approach, what's deliberately out of scope, what *not* to "fix."
Put it in the PR body under a `## Context` heading (or generate one with the
`/handoff` skill). Write it for a stranger — under the coordinated split above,
a stranger (the per-round shepherd) is exactly who addresses the review rounds
with the code but not your reasoning; the note is the cheap carrier of it. Skip
it only for trivial chore/CI PRs.

## The changelog fragment

Every feature/fix PR carries a changelog fragment **in the same PR**:
`release-core changelog add <slug> "<one-line summary>"`. Never hand-edit
`CHANGELOG.md` — a release refuses to cut without a fragment.

## Waiting: block in-turn, never background

```sh
release-core pr wait [<pr>]
```

One generic, engine-driven wait. It blocks **in-turn**, polls the state engine
with adaptive cadence, and returns as soon as you have something to do
(`ADDRESSING` / `BLOCKED` / `READY` / `NO_PR` exit immediately; waiting states
poll until they resolve). Exit 0 = an action is available; 2 = timeout
(`--poll` / `--timeout` tune it).

**Never** hand the wait to a background task or a Monitor. A subagent that
yields its turn to "wait in the background" **terminates and is never
re-woken** — it burns its run and never sees the result. Monitor is a
main-loop primitive; inside this loop you wait by blocking on
`release-core pr wait` in the current turn.

### `pr wait` is a long blocking call — keep it foreground

`release-core pr wait` legitimately runs **4–6 minutes** while reviewers and
CI work (that is the whole point — it blocks in-turn until there's something
to do). The Claude Code harness has a short default Bash timeout and will
**push a command that exceeds it into the background**, printing a
`Command running in background` line. That makes a correctly-foreground
`pr wait` *look* backgrounded — directly contradicting the "block in-turn"
rule above. This is a harness timeout artifact, not a `pr wait` bug
(release#692, #721, #730).

Avoid it by giving the Bash call an explicit long `timeout` so the harness
keeps it in the foreground for the whole wait:

```sh
# invoke pr wait with an explicit long Bash timeout (e.g. 900000 ms = 15 min)
release-core pr wait [<pr>]
```

When you call this tool, set the Bash `timeout` parameter to comfortably
exceed `pr wait`'s own `--timeout` (default a few minutes; bump both for a
slow reviewer). Then:

- A long run and even a `Command running in background` notice are
  **expected** — do not interpret them as "the wait detached, move on."
  Let the call finish and read its exit code (0 = act on the next state;
  2 = `pr wait` timed out, just re-run it).
- If it *does* slip to the background anyway, do **not** abandon the turn or
  spawn a Monitor — re-invoke `release-core pr wait` in the foreground with a
  larger Bash `timeout` and block on it to completion.

## Requesting reviews

```sh
release-core pr review request [<pr>] [--reviewer <name>]
```

Reviewer-agnostic: it dispatches through the adapter registry, defaulting to
all required reviewers — a bare `release-core pr review request` requests every
reviewer in the repo's required set. `--reviewer <name>` narrows to one.
`pr review cancel|show` follow the same shape (`--help` for details). The
done-signal for a review round is **zero unresolved review threads** — the
engine computes it; you never count a particular bot's comments.

### Changing the required reviewer set + rerun policy (a config knob, not code)

Which reviewers gate, and whether each re-runs on push, is **data**, not code —
reviewer pricing/availability shifts, so changing it is a one-line edit, no
engine change:

- **Default (shipped, all consumers):** `{copilot: rerun=false}` (copilot
  required, review-once), baked into `reviewers_config.DEFAULT_REVIEWERS` and
  carried by the `release_core` wheel.
- **Per-repo override:** add a `reviewers:` map to the repo's existing optional
  `.release-sync.yaml` (the same file that carries `capabilities:` — no new
  tracked file). The map KEYS are the required reviewers; each value is an
  options dict whose one option is `rerun: bool` (default false = review once).
  Examples:

  ```yaml
  # the phos pilot: CodeRabbit gates alongside Copilot, both review-once
  reviewers:
    copilot:   {rerun: false}
    coderabbit: {rerun: false}
  ```

  ```yaml
  # opt one reviewer into re-running on every push
  reviewers:
    copilot: {rerun: true}
  ```

  ```yaml
  # list shorthand = all required, rerun=false
  reviewers: [copilot, coderabbit]
  ```

  Each name must map to a registered **requestable** adapter (`copilot`,
  `coderabbit`, `codex`, `agy`); an unknown or non-requestable name fails
  **loud**. An empty/absent map falls back to the default. The retired
  `required_reviewers:` list key fails loud with a migration message (no
  backwards compat). Adding a *new* reviewer backend is still an adapter in the
  registry; flipping which existing ones gate, or whether each re-runs, is purely
  this config.

The verb **verifies the attach**: GitHub can accept the request call yet
silently drop the `review_requested` edge (service stall / quota), so after
placing it polls briefly until the reviewer shows up in the PR's pending
requests (a fresh review submitted meanwhile also counts). Exit 0 means the
request is verified; a dropped attach fails loud with exit 1 — surface the
stall and retry later instead of waiting on a review that was never
requested.

## Triaging review comments

Three categories:

**A) Real issues — fix.** Project-specific correctness problems, broken flags,
missing permissions, actual bugs. Fix, commit, push (CI re-runs on the same
branch).

**B) Project-ethos mismatch — push back with rationale, don't change the file.**
Reply to the comment:

```sh
gh api 'repos/{owner}/{repo}/pulls/<PR>/comments/<COMMENT_ID>/replies' \
  -X POST -f body="..."
```

End the reply with a line like *"Recording for future review passes: don't ask
us to `<X>`"* so the rationale is searchable later. Typical pushed-back asks:
pin same-owner reusable workflows to a SHA, special-case a shared template
for one repo, flag stacked-PR references to surface that land in a sibling PR.

**C) Cosmetic nits in already-merged style — skip.** Don't reply unless the
same nit recurs; then push back generally.

### Resolve threads as you go

After acting on each comment — fix-and-push *or* rationale reply — resolve its
thread:

```sh
release-core pr resolve-thread <PR> <COMMENT_ID>
```

Threads don't auto-resolve on push or reply; without this, multi-round PRs
become unreadable and the zero-unresolved-threads done-signal never fires.
Resolve aggressively:

- **Fix-and-pushed → resolve.** The diff is the proof.
- **Rationale-replied → resolve.** Trust your judgment; a follow-up pass can
  re-open.
- **Genuinely contested or awaiting human input → leave open.** That's the
  signal.

### Re-requesting a review (per-reviewer config, default review-once)

Re-run on push is a **per-reviewer** setting and defaults **OFF (review once)**
for everyone — all reviewers are token-billed now (local agents cost a real
model run each time), so re-reviewing each new head is explicit opt-in via a
reviewer's `rerun: true`. After a push, run `release-core pr review request`:
the bare verb skips any reviewer already done and re-requests only those not
done — so a default review-once reviewer (whose review counts on any head) is
skipped, and a `rerun: true` reviewer (staled by the push) is re-requested. The
state engine is the arbiter — `release-core pr status` advises RE-REQUEST only
for a staled `rerun: true` reviewer; that next action is authoritative. The
manual escape hatch is `--reviewer <name>`, which FORCES a (re-)request of that
reviewer regardless of state.

## The stopping rule: 6 rounds, or an all-nitpick round

Address every review comment each round, **except stop when either**:

- **6 rounds have happened** (there is no 7th round), or
- **the current round is all nitpicks** — docstring/wording corrections, micro
  performance with a low run-count, cosmetic style already settled; nothing
  that changes correctness or behaviour.

When either condition is hit on an otherwise-ready PR (CI green, mergeable),
the engine routes straight to **READY**: flip and hand to the human; do **not**
open another round. The engine applies this itself — `release-core pr status`
reports the `round-cap` / `all-nitpick` stopping condition on the READY status,
so the flip just proceeds (there is no acknowledgement flag).

`BLOCKED` is a real blocker only (failing check, merge conflict, behind base) —
yours to fix: do the fix, push, re-read. The stopping rule never produces a
BLOCKED; a real CI/merge problem still blocks on its own terms.

## At READY: the flip is automatic, then stop

```sh
release-core pr wait [<pr>]   # flips draft->ready itself on reaching READY
release-core pr ready [<pr>]  # the explicit verb, when you land on READY without a wait
```

The flip is a pure predicate the engine has already evaluated, so the loop does
it for you: **`release-core pr wait` performs the guarded draft→ready flip the
moment the engine reaches READY** — driving the wait to completion *is* the
handoff. `release-core pr ready` is the same guarded flip as an explicit verb,
for when you arrive at READY without a wait (and `--undo` for re-work).

Both are guarded and are the **only** sanctioned way to flip draft→ready —
never raw `gh pr ready`. They refuse (exit 1, printing the state and next
action) unless the engine says `READY`, so a premature flip is impossible by
construction.

`READY` requires a genuinely-mergeable PR: a **CLEAN** merge state, not just
GitHub's `mergeable` verdict. GitHub computes `mergeable` asynchronously and
returns a STALE, optimistic `MERGEABLE` on the first read after an open / push
/ base move — so a PR that actually conflicts (`mergeStateStatus=DIRTY`) or
trails its base (`BEHIND`) reads `mergeable=MERGEABLE` for a moment. The engine
cross-checks `mergeStateStatus` and reports `BLOCKED` (conflict/behind) or
re-poll (`REVIEWED`, uncomputed) instead of flipping (release#675). Trust the
engine state, never a raw `mergeable` field.

**Do NOT auto-merge the final PR.** The flip ends the agent's job: post a short
status and stop — the human does the final read and merge. Which PR the human
merges depends on topology:

- **Single-PR task:** the one PR targets `main`; the human merges it to `main`.
- **Epic:** each workstream PR targets the **epic branch**, and the coordinator
  merges those workstream PRs into the epic branch itself (its own go/no-go, no
  user approval). The **umbrella PR** (epic branch → `main`) is the one driven
  to READY and the **human merges** it. The user's approval gate is the umbrella
  PR, not the individual workstreams.

Merge a final/`main`-targeting PR only on explicit authorization ("merge it",
"go ahead and merge", "merge when green", or a standing auto-merge instruction
for the batch). When authorized: `gh pr merge <PR> --squash --delete-branch`.
If a pre-existing failure unrelated to the PR blocks the merge, surface it and
ask — never `--admin` unprompted.

## The final-report contract (always, even mid-flow)

Whenever you end a turn on a PR — ready, blocked, or stopping early — close
with a structured report. This is non-negotiable when the skill runs as a
subagent: the parent has no other window into what happened.

Two hard rules before you report:

1. **Re-verify actual state — don't trust the last wait result.** An exit code
   can be stale by the time you stop (a check finished, a new commit landed).
   Always read live state first:

   ```sh
   gh pr view <PR> --json url,headRefOid,mergeStateStatus,mergeable,statusCheckRollup,reviews --jq '{url,head:.headRefOid,mergeState:.mergeStateStatus,mergeable,checks:[.statusCheckRollup[]?|{name:.name,c:.conclusion}],reviews:[.reviews[]?|{by:.author.login,state:.state}]}'
   ```

2. **Never stop mid-wait.** If a wait is genuinely needed, block on
   `release-core pr wait` to completion first — it exists precisely so you
   can. Ending a turn with "I'll wait in the background" wastes the run: the
   parent finds the event already arrived and has to restart you.

Then emit the report block verbatim — same shape every time so downstream
agents can parse it:

```text
## Report
PR: <url>
Head SHA: <sha>
CI: <check=conclusion, ...>   (or "none yet")
Reviews: <bot/user=state, ...>   (or "none yet")
Mergeable: <yes | no — blocker>
Next step: <merge | wait for X | file issue | stop, handing back>
```

`Next step` is the actionable line — be specific (`wait for review on <sha>`,
not `wait`). If `Mergeable: no`, name the blocker (failing check, unresolved
thread, pre-existing main breakage).

## When the loop misbehaves: file an issue at `arthur-debert/release`

The loop's infrastructure (state engine, reviewer adapters, reusable
workflows, ruleset, guard) lives in `arthur-debert/release`. When it fails in
a way the consumer repo can't fix locally — a review never attaches, a wait
times out on a healthy PR, the ruleset demands a check name that doesn't
exist — file it there; don't patch around it in the consumer:

```sh
release-core issue file <component> "<one-line symptom>"
```

It auto-collects repo, branch, PR, and recent workflow-run context. The fix
lands upstream and propagates to every consumer; after filing, follow up with
logs / suspected cause as a comment. **Don't file** comment nits or
project-specific test failures — those are PR-level.
