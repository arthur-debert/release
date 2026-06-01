# Fleet Telemetry via GitHub Issues

**Status:** design rationale
**Date:** 2026-05-02
**Related:** [pr-review-loop-circuit-breakers.md](../proposals/pr-review-loop-circuit-breakers.md)

## Problem

The arthur-debert / lex-fmt fleet is ~10 repos sharing tooling that
lives here in `release/`: reusable workflows, policy templates,
helper scripts, the `gh-pr-review-loop` skill. As the tooling does
more — circuit breakers, policy sweeps, release pipelines — the
question of _how the author knows whether the tooling is working
across the fleet_ becomes structural.

Two architectural shapes are available, and they have very different
implications at this scale.

## The two shapes

### Pull / scheduled polling

A scheduled agent (cron, Claude `/schedule`, GitHub Action on a
schedule) walks each consumer repo every interval, reads CI runs,
PR states, recent commits, and reports a digest.

Strengths:

- Stateless. No per-repo integration; everything is read-only `gh api`.
- Captures aggregate state ("3 PRs are stale" requires looking at all of them).
- Easy to add new signals — just extend the polling script.

Weaknesses at this scale:

- Misses transient events. A circuit breaker that fires at 2am and is
  resolved by the time the 6am poll runs leaves no trace.
- Wastes work in steady state. Most polls find nothing interesting.
- Crawl cost grows with N repos × N signals.
- No natural UX. The output is a digest somewhere; the user has to
  remember where to look. There's no "inbox."

### Push / events-as-issues

When something interesting happens in a consumer repo — circuit
breaker fires, release fails, policy drift detected — that repo's
CI (or the tool that detected it) files a GitHub issue on
`arthur-debert/release` with a structured body. The user (or a
weekly digest agent) walks `gh issue list` on this one repo to see
the fleet pulse.

Strengths at this scale:

- Single inbox. `gh issue list` on release/ becomes the dashboard.
  Native UX, no custom plumbing.
- Captures _moments_, not state. Every breaker firing is a row;
  trends over time are free (issue creation timestamps + labels).
- Push is cheaper than poll in steady state. Nothing happens when
  nothing happens.
- Aligns with this repo's identity. release/ owns the tooling;
  release/ owns the telemetry about that tooling.
- Works with the rest of the GitHub UI: search, labels, milestones,
  notifications, CLI.

Weaknesses:

- Requires per-repo integration (a token + a workflow call).
- Requires a taxonomy upfront, or the inbox becomes prose-soup.
- Doesn't capture _aggregate state_ — a question like "which PRs
  are stale right now?" still wants a poll. (In practice this is
  a small handful of digest-style queries that can be one
  scheduled agent reading a single inbox.)

## Why push wins for this fleet

This is a **single-author multi-repo fleet**. The constraints that
favor pull at scale (many readers needing different views, no single
"home" for telemetry, complex permission boundaries) don't apply.
The constraints that favor push (one inbox, capturing moments,
low steady-state cost, native UX) do.

The pivotal observation: **the user already opens GitHub.** Adding
"check release/ issues" to that habit is free; building and
remembering to run a scheduled poller is not.

## Core principles

These are the rules the design holds itself to. They're worth
naming because the easy failure mode is letting telemetry drift
into being a parallel discussion venue, which it should not be.

### 1. The issue is a report, not a discussion venue

When a circuit breaker fires on `dodot#118`, the _resolution_
happens on `dodot#118` — that's where the code is, where the PR
review thread lives, where the merge button is. The
release/ issue is a pointer + summary + suggested action. It exists
so the user can _find_ the situation, not so they can _resolve_ it
in two places at once.

Concretely:

- Every report includes the source URL.
- The source PR/issue/run links back to the report ("filed:
  release/#42").
- Comments on the report are for triage notes ("ack, won't fix"),
  not for working the underlying problem.

### 2. The reporter owns the format, this repo owns the schema

Consumer repos do not hand-write issue bodies. They call into a
shared mechanism (script or reusable workflow) that this repo
provides, with structured inputs. This means:

- The body shape is consistent and queryable.
- Changing the format is a release/ change, not an N-repo change.
- New event types are gated through this repo's review.

### 3. Events, not state

A report represents _something happened at time T_. It does not
represent _something is currently true_. If the underlying
condition resolves, the issue is closed (manually or by digest
agent), not edited to say "no longer true." This keeps the inbox
queryable as a time-series.

State queries ("what's currently broken across the fleet?") are
answered by `gh issue list --state open` filtered by label —
which is exactly what a per-event log lets you do for free.

### 4. Taxonomy is small and stable

Better to have 6 well-known event kinds than 30 ad-hoc ones. New
kinds are added deliberately; existing kinds are not renamed
casually (issue history queries break). When in doubt, file under
an existing kind with a richer body rather than minting a new one.

## Tensions and tradeoffs (named explicitly)

- **Inbox volume.** If breakers fire often, release/'s issue tracker
  becomes high-traffic. Mitigation: the fleet is small, the breakers
  are conservative, and a weekly digest agent can close obviously-
  resolved entries. If volume becomes a problem, split into a
  dedicated `release-incidents/` repo — but YAGNI for now.

- **Cross-repo permissions.** Filing issues on `arthur-debert/release`
  from a repo in `lex-fmt/*` requires a token. This is a one-time
  setup cost (handled by `bin/install-release-secrets`) but it does
  mean the reporting path can fail silently if a consumer's secret
  isn't set. Reports should fail loud locally (CI step error) when
  they can't reach release/.

- **Coupling.** Consumer repos now know a name: `arthur-debert/release`.
  If this repo is ever renamed or moved, every consumer's CI breaks.
  Mitigation: the reusable-workflow indirection (`uses:
arthur-debert/release/.github/workflows/report-incident.yml@v1`)
  is a single rename target; consumers don't hardcode the issue
  endpoint themselves.

- **Reports about release/ itself.** If the reporting mechanism is
  broken, it can't report its own brokenness. This is fine in
  practice — the user is the one running the tooling and notices
  fast. Don't build self-monitoring on top of monitoring; that's
  scale-creep.

## When this would stop scaling

The design holds while the fleet is single-author and ~tens of
repos. It would start to strain when:

- Multiple authors need different views (one person's "noise" is
  another's "must-fix"). Solution: filtered notifications via
  labels + GitHub's native subscription mechanics. Push past that:
  graduate to a real telemetry tool (Sentry, Honeycomb, Linear).
- Event volume exceeds ~5/day sustained. The inbox stops being
  scannable. Solution: digest agent rolls up daily summaries and
  closes the originals.
- Cross-org reporting requires fine-grained access. Solution: a
  GitHub App rather than a PAT. Same shape, different auth.

None of these are current problems. Document them so the design's
endurance is explicit, not assumed.

## Relationship to scheduled agents

Push doesn't eliminate scheduled agents — it relocates what they
do. Useful scheduled work in this model:

- **Weekly digest.** Read open issues on release/, group by repo
  and kind, post a summary. Close auto-resolvable ones (e.g.
  source PR was merged, breaker is moot).
- **Stale-report sweep.** Issues older than N weeks with no
  activity get a "still relevant?" comment.
- **Aggregate state queries** the user explicitly asks for ("which
  PRs across the fleet have been open >2 weeks") — these legitimately
  want a poll, but they're rare and on-demand.

What scheduled agents _don't_ do anymore: poll for events. The
events come to them.
