# Epic #348 — remaining stages (A3, C, D)

**Status:** planned
**Date:** 2026-05-30
**Epic:** [#348](https://github.com/arthur-debert/release/issues/348) — Self-Improving Feedback Loop + consumer orientation (roadmap §5)
**Background:** [fleet-telemetry-via-issues.md](../references/fleet-telemetry-via-issues.md),
[incident-reporting-mechanism.md](incident-reporting-mechanism.md)

## What's already done

| Phase  | What                                                                                             | Where                              |
| ------ | ------------------------------------------------------------------------------------------------ | ---------------------------------- |
| **A1** | Distribute the §4 engine (`gh-task-status` + `lib/release_gh`) into consumer `.release/`         | #351 — now in `templates/commons/` |
| **A2** | Ship the consumer `CLAUDE.md` orientation template (managed block + `@.release/ORIENTATION.md`)  | #360                               |
| **B**  | §5.1 escalation contract baked into the orientation; `gh-release-issue` distributed to consumers | #360                               |

A fresh agent landing in any managed repo is oriented (what's managed, the
`gh-task-status` dev-flow entry, the escalation contract) and can file upstream
via `gh-release-issue` on PATH.

## What remains

Three pieces: **A3** (skill reach + naming), **Phase C** (batch issue
processing — read/triage side), **Phase D** (CI failure analysis — a producer
that feeds the same inbox C consumes).

### Design spine: one inbox, push + poll producers, one triage consumer

The remaining work is a producer/consumer loop over **a single inbox**: the
`arthur-debert/release` issue tracker, filed into with a stable `[component]`
title prefix (the shape `gh-release-issue` / `release-issue-relay` already
write).

```
  PRODUCERS                              INBOX                 CONSUMER
  ─────────                              ─────                 ────────
  B  escalation contract  (push) ─┐
     gh-release-issue             ├──►  release tracker  ──►  C  triage run
  D  fleet CI-failure sweep (poll)┘     ([component] issues)     (cluster,
                                                                  prioritize,
                                                                  fix, notify)
```

Push (B) captures **moments** an agent escalated; poll (D) captures
**steady-state CI health** nobody escalated. Both land in one queryable inbox;
C is the manually-triggered drain. Per
[fleet-telemetry-via-issues.md](../references/fleet-telemetry-via-issues.md)
we deliberately keep this **light**: reuse the existing `gh-release-issue`
body + a small label taxonomy, rather than building the heavier
`report-incident` + `INCIDENT_TOKEN` mechanism (deferred — see "Deferred"
below). D runs from the maintainer's already-authed context, so it needs no
new cross-org PAT.

## Stages

### Stage 1 — A3: PR-loop skill reach + naming (independent) — [#367](https://github.com/arthur-debert/release/issues/367)

The `gh-pr-review-loop` skill reaches agents today only via `env/setup.sh` →
`~/.claude/skills/` (cloud) and the maintainer's machine. Its description is
maintainer-scoped, says "merge" (stale — we stop at _ready-for-human-merge_),
and omits `gh-task-status`.

- Rework `skills/gh-pr-review-loop/SKILL.md` frontmatter **name/description**:
  consumer-inclusive, trigger-optimized (consider the `skill-creator` skill for
  phrasing), lead with `gh-task-status` as the entry point, fix
  "merge" → "ready-for-human-merge". Keep maintainer-only body sections
  (onboarding, `apply-ruleset`, `audit-*`) — they're harmless to a consumer
  agent and the description no longer advertises them.
- **Distribute the skill into consumers** (decision: ship the existing skill
  as-is, not a slim variant). `release-sync` injects it into the sync plan so it
  lands at `.claude/skills/gh-pr-review-loop/SKILL.md` in each consumer — the path
  Claude Code auto-discovers project skills from. **Finalized design:** source the
  skill **directly from `skills/`** (the single home, also installed to
  `~/.claude/skills/` by `env/setup.sh`) rather than duplicating it into a
  `templates/` subtree — one copy, no drift. The materialize loop writes a real
  blob; the mirror step symlinks it out (a symlinked SKILL.md pointing into
  `.release/` is dereferenceable, so Claude Code still discovers it).
  `.claude/skills/**` is already excluded from the consumer markdownlint gate.

**Acceptance:** a fresh agent in a consumer repo (cloud _and_ local) has the
skill available, triggered by "open a PR" / "check PR status"; its description
leads with `gh-task-status` and carries no auto-merge language.

**Depends on:** nothing. Smallest, most independent — do first.

### Stage 2 — Inbox foundation: label taxonomy + `release-inbox` accessor (shared by C & D) — [#368](https://github.com/arthur-debert/release/issues/368)

- Define a **small, stable label taxonomy** on the release tracker. The
  `[component]` title prefix already exists; add a `consumer-filed` (and/or
  `repo:<name>`) label so the inbox filters cleanly. Keep it minimal per the
  telemetry doc — no severity axis in v1.
- Extend `gh-release-issue` + `release-issue-relay` to **apply the label(s)**
  on filing (small change to existing tools).
- Build `bin/release-inbox`: a thin accessor that lists open consumer-filed
  issues, **clusters by component + recurrence** (comment count = recurrence
  signal), and prints a triage-ready digest. Reuses the `[component]` prefix;
  resolves nothing it can't read from `gh issue list`.

**Acceptance:** `release-inbox` returns a clustered digest of the current
inbox; new filings carry the taxonomy label.

**Depends on:** nothing (but is the foundation for 3 and 4).

### Stage 3 — Phase C: batch triage + close-the-loop (consumes inbox) — [#369](https://github.com/arthur-debert/release/issues/369)

A **manually-triggered** triage run (new skill, e.g. `release-fleet-triage`, or
an extension of the existing `triage` skill) that:

1. Pulls the `release-inbox` digest.
2. Clusters by signature, prioritizes by recurrence + blast radius.
3. Produces a prioritized worklist and drives fixes through the existing
   PATCH → `release-advance-major` flow.
4. **Closes the loop**: when a fix ships, comments on the originating consumer
   PR/issue (the issue body's `Reported from:` / PR link) referencing the
   release fix and the `@vN` advance, so the consumer knows to re-run.

**Acceptance:** one real triage run over the current inbox yields a prioritized
cluster list; a fixed cluster produces consumer-notification comments.

**Depends on:** Stage 2.

### Stage 4 — Phase D: fleet CI-failure sweep producer (feeds inbox) — [#370](https://github.com/arthur-debert/release/issues/370)

`bin/release-ci-sweep` (or an extension of `release-verify-fleet`, which is the
working hand-run prototype — `release-verify-fleet --ref main` already surfaced
the npm-deps artifact, the symlink-sweep bug, and supage's gitignore drift):

- Walk `managed-repos`, pull recent CI runs per consumer (`gh run list` /
  `gh api`), extract failures + warnings.
- **Cluster by error signature**; file/dedupe release issues for recurring
  patterns using `gh-release-issue`'s format + Stage 2 labels.
- Runs from the maintainer's authed context (no new PAT). First
  manually-triggered.

**Acceptance:** one sweep run files clustered, deduped CI-failure issues into
the inbox; C (Stage 3) can triage them.

**Depends on:** Stage 2. Independent of Stage 3 (C can be built/validated
against existing manually-filed issues before D automates more producers).

## Ordering

```
Stage 1 (A3) ── independent, any time
Stage 2 (inbox foundation) ──► Stage 3 (C, triage)
                          └──► Stage 4 (D, CI sweep)
```

Build 2 before 3/4; 3 and 4 can proceed in parallel. Validate C against the
existing manually-filed inbox before D widens the producer set.

## Deferred (explicitly out of scope here)

- **Full `report-incident` mechanism** (`bin/report-incident`,
  `report-incident.yml` reusable workflow, `INCIDENT_TOKEN`, 6-kind taxonomy)
  from [incident-reporting-mechanism.md](incident-reporting-mechanism.md). The
  light path (reuse `gh-release-issue` + labels, maintainer-context D sweep)
  covers C/D without minting/distributing a new PAT. Revisit if/when CI-driven
  push from consumer workflows (vs. agent-driven + maintainer-poll) is needed.
- **Weekly digest agent / stale-report sweep** — after the inbox has run live
  long enough to know its shape.
- **Cloud/webhook transport** — deferred on #338 / tracked in #350.
