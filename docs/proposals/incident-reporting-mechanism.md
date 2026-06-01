# Incident Reporting Mechanism

**Status:** proposed
**Date:** 2026-05-02
**Background:** [fleet-telemetry-via-issues.md](../references/fleet-telemetry-via-issues.md)
**Sibling:** [pr-review-loop-circuit-breakers.md](pr-review-loop-circuit-breakers.md)
(the circuit-breaker work is the first consumer of this mechanism)

## Problem

Consumer repos in the fleet need a uniform way to file structured
reports on `arthur-debert/release` when something tooling-related
happens that the author should know about. Without a shared
mechanism, each event source (circuit breakers, release pipelines,
policy sweeps) reinvents the format and consumer repos drift.

Per [fleet-telemetry-via-issues.md](../references/fleet-telemetry-via-issues.md),
the design is **events-as-issues on this repo**. This proposal
specifies the taxonomy, body schema, reporting tools, and consumer
integration.

## Goals

- One canonical way for any tool/CI/skill to file a report.
- Stable, queryable taxonomy of event kinds.
- No per-consumer-repo issue-body authoring — consumers pass
  structured inputs, this repo owns the format.
- Failure of the reporting path is loud (consumer CI step fails),
  not silent.
- Easy to add new event kinds without touching every consumer.

## Non-goals

- A dashboard / UI beyond `gh issue list`. The native tracker is
  the dashboard.
- Notification routing rules (pager, email). GitHub's native
  subscription mechanics are sufficient.
- A query/aggregation layer. Issue search + labels covers it.
- Reports about non-tooling concerns (product bugs, feature
  requests). Those live on the consumer repo.

## Event taxonomy

Six kinds, each with a label and body schema. Add new kinds
deliberately (PR to this proposal, then to the schema).

| Kind                       | Label                   | Triggers                                                                                                                       |
| -------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **circuit-breaker-fired**  | `incident:breaker`      | A `gh-pr-review-loop` circuit breaker stopped a review cycle.                                                                  |
| **release-failed**         | `incident:release`      | A release-pipeline workflow failed mid-stage (build/sign/publish/notarize/tag).                                                |
| **deploy-failed**          | `incident:deploy`       | A publish step failed (cargo publish, brew tap update, gh release upload, apt repo push).                                      |
| **workflow-broken**        | `incident:workflow`     | A CI workflow itself is broken — wrong `uses:` ref, missing secret, schema error — distinct from "the code under test failed." |
| **policy-drift**           | `incident:policy-drift` | `sweep-github-policy` or `apply-ruleset` detected a consumer repo out of sync with templates.                                  |
| **secret-rotation-needed** | `incident:secret`       | A token/cert/key is expired or within the rotation window.                                                                     |

Per-event source repo also gets a label: `repo:<name>` (e.g.
`repo:dodot`, `repo:lex`). Combined with the kind labels, this gives
2-axis filtering for free.

No severity label in v1. The fleet is small enough that everything
filed is "the user should look at this." If volume grows, add
`sev:info` / `sev:warn` / `sev:error` later.

## Issue title and body schema

### Title

```
[<kind>] <one-line summary> (<repo>#<source-id>)
```

Examples:

- `[breaker] diff trajectory — 234→412→891 (dodot#118)`
- `[release] cargo publish failed at v1.4.2 (padz)`
- `[policy-drift] copilot-instructions.md outdated (clapfig)`
- `[secret] CRATES_IO_KEY expires in 7 days (lex-fmt/lex)`

### Body

Markdown, structured into fixed sections so it's both human-readable
and machine-parseable:

````markdown
## Source

- **Repo:** arthur-debert/dodot
- **Reference:** PR #118 — https://github.com/arthur-debert/dodot/pull/118
- **Commit:** abc1234 (head at time of event)
- **Workflow run:** https://github.com/arthur-debert/dodot/actions/runs/12345 (if applicable)

## Event

- **Kind:** circuit-breaker-fired
- **Subkind:** diff-trajectory
- **Time:** 2026-05-02T14:32:00Z
- **Reporter:** gh-pr-loop-status v1.0

## Data

<!-- Structured payload, fenced as YAML or JSON. Schema varies per kind. -->

```yaml
breaker: 3
trajectory:
  - cycle: 1
    diff_lines: 234
    head: a1b2c3d
  - cycle: 2
    diff_lines: 412
    head: e4f5g6h
  - cycle: 3
    diff_lines: 891
    head: i7j8k9l
```
````

## Diagnosis

<!-- Short paragraph from the reporter explaining what the data suggests. -->

## Suggested action

<!-- Optional. What the reporter thinks should happen. Numbered list. -->

---

_Filed by `bin/report-incident`. See [the proposal](https://github.com/arthur-debert/release/blob/main/docs/proposals/incident-reporting-mechanism.md)._

````

The `## Source`, `## Event`, and `## Data` sections are required.
`## Diagnosis` and `## Suggested action` are optional but encouraged
for breaker/failure events.

## Reporting tools

Two layers, sharing one core.

### Core: `~/h/release/bin/report-incident`

Canonical scriptable entry point. Takes flags, posts the issue.

```sh
report-incident \
  --kind circuit-breaker-fired \
  --subkind diff-trajectory \
  --source-repo arthur-debert/dodot \
  --source-ref pr/118 \
  --source-commit abc1234 \
  --reporter "gh-pr-loop-status v1.0" \
  --data-file /tmp/breaker-data.yaml \
  --diagnosis "Diff grew 234→412→891 across cycles 1–3..." \
  --suggested-action-file /tmp/actions.md
````

Behavior:

- Reads `GH_TOKEN` (or `GITHUB_TOKEN`) for auth.
- Validates `--kind` against the taxonomy. Unknown kind → exit 2.
- Constructs title + body per schema.
- `gh api repos/arthur-debert/release/issues -X POST` to file it.
- Adds labels: `incident:<kind>`, `repo:<source-repo-basename>`.
- Prints the new issue URL on stdout.
- Exit codes: 0 = filed; 1 = transient (network/rate-limit); 2 = bad input; 3 = auth missing.

Tools like `gh-pr-loop-status` invoke this directly when the breaker
fires.

### Wrapper: `.github/workflows/report-incident.yml` (reusable workflow)

For consumer-repo CI to call:

```yaml
# In a consumer repo's workflow, on failure:
- name: Report release failure
  if: failure()
  uses: arthur-debert/release/.github/workflows/report-incident.yml@v1
  with:
    kind: release-failed
    subkind: cargo-publish
    source_ref: ${{ github.run_id }}
    source_commit: ${{ github.sha }}
    diagnosis: "cargo publish failed for ${{ github.event.repository.name }} v${{ inputs.version }}"
  secrets:
    INCIDENT_TOKEN: ${{ secrets.INCIDENT_TOKEN }}
```

Internally calls `bin/report-incident`. Keeps consumer-side
boilerplate to one `uses:` block.

## Authentication

A new fine-grained PAT, `INCIDENT_TOKEN`, scoped to:

- `arthur-debert/release` only
- Permission: `issues: write` (no other access)

Distribution:

- Add to `bin/install-release-secrets` so onboarded repos get it.
- Document in `docs/secrets.md` (alongside the existing 7 release secrets).
- For lex-fmt/\* (cross-org), the consumer must list `INCIDENT_TOKEN`
  explicitly under `secrets:` in their reusable-workflow `uses:` block —
  same caveat as the existing cross-org pattern.

## Issue lifecycle

- **Open by default** when filed.
- **Closed manually** when the user has handled it (resolved the
  underlying problem, or decided not to act).
- **Closed automatically by the weekly digest agent** if:
  - The source PR/issue is closed/merged AND the kind is breaker
    or workflow (the trigger is gone).
  - The kind is `secret-rotation-needed` AND the secret has been
    rotated (separate check).
- **Never auto-edited.** A new event = a new issue, even if it
  duplicates an existing open one. Dedup is a digest-agent concern,
  not a reporter concern. (Reporting-time dedup would require state
  the reporter shouldn't have.)

Stale-issue sweep (separate scheduled task):

- Issues open >30 days with no comments → digest agent posts
  "still relevant?" and labels `stale`.
- Twice-stale (60 days) → close with `wontfix`.

## Implementation surface

### A. `~/h/release/bin/report-incident` (new)

Bash script. Validates inputs, constructs body, calls
`gh api`. ~150 lines. Tested via `tests/fixtures/` — the
existing test harness pattern.

### B. `~/h/release/.github/workflows/report-incident.yml` (new reusable workflow)

Thin wrapper that checks out this repo, runs `bin/report-incident`
with workflow inputs, no other logic. ~30 lines.

### C. `~/h/release/bin/install-release-secrets` (existing — extend)

Add `INCIDENT_TOKEN` to the secret set propagated to onboarded
repos. Per the load-bearing CLAUDE.md note, the script's secret
list is part of the contract — bump it as a MINOR.

### D. `~/h/release/docs/secrets.md` (existing — extend)

Document `INCIDENT_TOKEN`: scope, rotation cadence, how to mint.

### E. `~/h/release/templates/rust/copilot-instructions.md` (existing — small note)

Add a line: _"This repo files incident reports to `arthur-debert/release/issues`
when tooling-related events occur (circuit breakers, release failures, etc.).
Don't suggest removing or restructuring those reporting calls."_

### F. Cross-link from `pr-review-loop-circuit-breakers.md`

The circuit-breakers proposal §D already references this one:
`gh-pr-loop-status --post-stop-comment` performs **two coordinated
writes** — a structured PR comment on the source PR (the user's
decision interface) AND an incident report filed via
`bin/report-incident` with `kind=circuit-breaker-fired` (the
fleet-pulse entry). The PR comment includes a `Fleet record:` line
pointing at the release/ issue number; the release/ issue's
`## Source` block points back at the PR. Navigation is bidirectional.

Implementation order: this proposal's tools (A + B) must land
before circuit-breakers step 3 (`gh-pr-loop-status` rollout) so
the two-write behavior is wired from the start. If the order
slips, `gh-pr-loop-status` ships with PR-comment-only mode and
the report-incident call is added in a follow-up PATCH.

## Decisions to settle before implementation

1. **Reusable workflow vs composite action for the consumer entry point.**
   Reusable workflows can be called via `uses:` at the workflow level
   (cleaner) but are heavier. Composite actions are step-level (more
   flexible) but harder to invoke for a single side-effect.
   **Recommendation:** reusable workflow. Most consumers will report
   from a dedicated `if: failure()` job, and reusable-workflow indirection
   matches the existing `arthur-debert/gh-dagentic` pattern.

2. **Issue-creation rate limits.**
   GitHub allows 80 issue creations per hour per token. Almost
   certainly a non-issue at fleet scale, but worth noting in
   `bin/report-incident` so a runaway loop in a reporter doesn't
   silently get rate-limited. Add a 1s sleep after each post and
   exit 1 on 429.

3. **Should `bin/report-incident` be invoke-able from outside this repo's
   working directory?** Yes — it must work when called from a checked-out
   consumer repo's CI runner. Implementation: no relative path assumptions;
   templates resolved relative to script's own location (matches existing
   convention here per CLAUDE.md).

4. **What about reports filed during `release/`'s own CI?**
   They go to release/'s own issue tracker. Self-reporting is fine and
   actually desirable — release/ workflows breaking is a fleet event
   too. No special-casing needed.

## Rollout plan

1. **Land the schema and tools (A + B + extend D).** Tag this repo
   as a MINOR (new optional capability, no input contract change).
2. **Mint and distribute `INCIDENT_TOKEN` (extend C).** Sister
   change to bin/install-release-secrets. Run the sweep against all
   onboarded repos.
3. **Wire the first consumer: circuit breakers.** Per the sibling
   proposal §D, `gh-pr-loop-status --post-stop-comment` calls
   `bin/report-incident`. Validates the end-to-end path with a real
   event source.
4. **Wire release-failed reports** in this repo's own
   `rust-cli.yml` and other category workflows. Adds `if: failure()`
   step that calls the reusable workflow.
5. **Wire policy-drift detection** in `sweep-github-policy --check`
   (a new mode that reports drift instead of fixing it).
6. **Add weekly digest agent** (separate proposal, after telemetry
   has been live ~2 weeks and we know what the inbox looks like).

## What success looks like

- Within 1 month, every event kind in the taxonomy has at least
  one real-world report filed and triaged.
- The author's "what's going on across the fleet" question is
  answered by `gh issue list -R arthur-debert/release -l incident:*`
  in a single command.
- Adding a new event kind requires editing exactly two files in
  this repo (`bin/report-incident`'s validation, this proposal's
  taxonomy table) and zero files in consumer repos.
- The reusable-workflow consumer-side boilerplate stays at ≤10 lines.

## Out of scope (future work)

- **Dedup at report time.** Requires state the reporter doesn't have.
  Defer to digest agent.
- **Issue templates in `.github/ISSUE_TEMPLATE/`.** These are for
  human-filed issues; reports are bot-filed and don't go through
  the template UI. Skip.
- **Webhook/external integration** (Slack, email). GitHub's native
  notification mechanics are sufficient at one-author scale.
- **Anomaly detection.** "Breakers fired 3x more this week than
  last" — interesting, but premature without baseline data.
- **GitHub App instead of PAT.** Cleaner auth model, more setup
  cost. Revisit if the PAT becomes painful.
