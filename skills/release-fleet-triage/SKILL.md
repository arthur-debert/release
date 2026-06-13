---
name: release-fleet-triage
description: "Drain the release fleet inbox: process the consumer-filed issues that arthur-debert/release accumulates from the escalation loop. Pull the prioritized cluster digest with `release-inbox`, decide what to fix now by recurrence + blast radius, drive each fix through the normal PR loop and `release-advance-major`, then close the loop with `release-notify-source` so the consumers who reported it know to re-sync. Use when triaging the release backlog, processing fleet/consumer-filed issues, doing a batch upstream-fix run, or asking 'what fleet friction should release fix next?'. Run from inside arthur-debert/release."
---

# release-fleet-triage

The **write-side** of the self-improving feedback loop (release#348 §5.2). The
read-side (`gh-release-issue` + the `release-issue-relay` skill) funnels fleet
friction into one inbox — `consumer-filed` issues on `arthur-debert/release`.
This skill is the periodic, manually-triggered run that drains that inbox:
cluster, prioritize, fix upstream, and tell the consumers it's fixed.

It is an orchestration playbook over existing tools — it adds no new judgment
about *whether* a failure is a release bug (that's [`release-fleet-ops`](../release-fleet-ops/SKILL.md)'s
upstream-vs-consumer call) and no new PR mechanics (that's
[`gh-pr-review-loop`](../gh-pr-review-loop/SKILL.md)). It sequences them.

## When to use

- "Triage the fleet inbox", "process the consumer-filed issues", "what should
  release fix next?", a batch upstream-fix session.
- After a Phase D CI sweep files a wave of issues.

Run it from inside `arthur-debert/release` (the tools resolve the repo from the
working directory / `RELEASE_HOME`).

## Step 1 — orient: read the inbox

```sh
release-core admin inbox            # human digest: open consumer-filed issues, by cluster
release-core admin inbox --json     # same data, machine-readable (for scripting a worklist)
```

`release-core admin inbox` (retired flat: `release-inbox`) groups open
`consumer-filed` issues by `[component]` and sorts
clusters by **recurrence** — the comment count, which is the relay skill's
"also hit on `<repo>`" signal appended on each duplicate. A cluster with three
comments across two issues outranks a one-off.

If the inbox is empty, you're done — say so and stop.

## Step 2 — prioritize

Rank the clusters you'll act on this run by two axes the digest already gives you:

| Axis | Where it comes from | Why it matters |
|---|---|---|
| **Recurrence** | comment count (`recurrence` in `--json`) | the same break hitting many consumers / many times is costing the fleet the most |
| **Blast radius** | `issue_count` + the distinct source repos in the bodies | a break in a shared workflow/template hits every consumer of that component |

Take the top cluster(s). It's fine to fix one cluster per run — the inbox is a
queue, not a checklist to clear in one sitting. **Log what you're deferring** so
the run is honest about coverage (a one-line comment on each deferred issue:
"triaged YYYY-MM-DD, deferred behind `[component]`").

## Step 3 — fix upstream

For the chosen cluster, the fix lives in `release/` and propagates via `@vN`
(never patched in a consumer — see `release-fleet-ops` and the repo's operational
rules). The mechanical path:

1. **Confirm it's a release bug.** If unsure whether the symptom is upstream or
   consumer-specific, invoke `release-fleet-ops` (reproduce-once → route). Don't
   fix what isn't release's to fix.
2. **Branch + PATCH + tests** in `release/`. A bug surfaced by a consumer is a
   PATCH here.
3. **PR loop:** drive it with `gh-pr-review-loop` (open → Copilot → address →
   green → ready). Stop at ready-for-human-merge; the user merges.
4. **Advance the major** once merged so consumers on `@vN` actually get it:

   ```sh
   release-core admin release advance-major   # fast-forward the highest vN to main, push
   ```

   (Run `release-core admin repos verify` first if the change is broad — per the
   core fleet loop in the repo's CLAUDE.md.)

## Step 4 — close the loop

A fix nobody hears about isn't done. For **each** issue in the cluster you fixed,
notify the consumers who escalated it and close the release issue:

```sh
# Dry-run first (default) — prints the source PRs it will comment on + the body:
release-core admin inbox notify-source <release-issue-#> --fix "release#<pr>, v2 advanced to <sha>"

# When the plan looks right, send it and close the release issue:
release-core admin inbox notify-source <release-issue-#> --fix "release#<pr>, v2 advanced to <sha>" --post --close
```

`release-core admin inbox notify-source` (retired flat: `release-notify-source`)
reads the release issue, extracts every source PR it
points at (the `**PR:**` body line + the relay skill's `- PR:` duplicate lines),
and posts one consistent "upstream fix shipped — bump `@vN` and re-run" comment
on each. It is **dry-run by default** because it fans out across consumer repos;
always eyeball the dry-run before `--post`.

If it exits 3 ("no source PR"), the issue recorded no PR link — note the
`Reported from` repo it prints and notify that consumer by hand (a comment on
their tracker), then close the release issue manually.

## Guardrails

- **The release issue is a pointer, not a discussion venue** (per
  the issues-as-telemetry design). The fix happens on the
  release PR; the notification happens on the consumer's PR; the release issue
  just gets closed with a back-reference. Don't relitigate the bug in three places.
- **Events, not state.** One issue = one reported moment. Close it when handled;
  don't edit it to "no longer true." A recurrence files a fresh report (or adds
  a comment via the relay skill).
- **Don't auto-merge or auto-advance without the user.** `gh-pr-review-loop`
  stops at ready; `release-core admin release advance-major` and
  `release-core admin inbox notify-source --post` are outward-facing — surface
  the plan and let the user greenlight.
- **Scope.** Only `consumer-filed` infra issues belong here. Product bugs /
  feature requests on `release/` are normal issues, not fleet-inbox items.

## Related

- [`release-inbox`](../../bin/release-inbox) — the prioritized digest (Step 1).
- [`release-notify-source`](../../bin/release-notify-source) — the close-the-loop
  notifier (Step 4).
- [`release-issue-relay`](../release-issue-relay/SKILL.md) / `gh-release-issue` —
  the read-side producers that fill the inbox.
- [`release-fleet-ops`](../release-fleet-ops/SKILL.md) — the upstream-vs-consumer
  diagnosis + `release-core admin release advance-major` /
  `release-core admin repos verify` rules.
- [`gh-pr-review-loop`](../gh-pr-review-loop/SKILL.md) — the PR mechanics for Step 3.
