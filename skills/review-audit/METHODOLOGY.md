# review-audit — methodology, pilot validation & caveats

This is the "why" behind the [`review-audit`](SKILL.md) skill: the natural
experiment it exploits, the validating pilot, the maintenance burden, and the
confounders you must report when you quote a number.

## The natural experiment

A repo's merged-PR history is rarely run under one fixed review policy.
Reviewer config drifts over time — none → one bot → +a second → a
trigger-policy change (e.g. re-request on every push, with a round
circuit-breaker). Those changes partition the history into **eras** with
different reviewer sets, giving clean A/B contrasts the pipeline reads off
mechanically. `summarize.py` and `aggregate.py` **derive** the eras from the
reviewer set actually present on each PR — there is no hardcoded list of bots
or boundaries, so the same code works for any repo's history.

## The seven stages

```text
1 extract.py   REST pull of every merged PR (metadata, commits+timestamps,
               reviews, inline threads, issue comments, timeline). Resumable,
               rate-budget-aware (pauses near the limit). De-noises bot
               markdown — strips CodeRabbit's collapsible <details> walls,
               badges, boilerplate (~38x shrink) while KEEPING outcome markers
               like "Addressed in <sha>". Emits raw/ (archive) + slim/ (compact
               agent input) + metrics.jsonl (mechanical signals).
2 enrich.py    GraphQL reviewThreads -> true isResolved / isOutdated /
               resolvedBy per thread (REST cannot give resolution state).
               Matched back by root-comment databaseId.
3 finalize.py  Merges each thread's diff_hunk (the exact code the comment is
               about) from the archive, so judges check claims against real
               code instead of guessing.
4 summarize.py Mechanical headline metrics, ZERO agents: latency by era (TTM,
               ready->merge, first-review wait), per-reviewer volume +
               action-rate, the rounds-vs-PR-number curve that dates
               re-request onset, review-induced churn. The cheap tier.
5 stage2       The LLM-judging tier as a `release` Workflow fan-out (~4
  .workflow.js PRs/agent). Each agent reads slim files and emits
               schema-enforced verdicts: per-comment {category, severity,
               true-positive vs hallucination, actioned?, novelty = would
               tests/linter/the other bot have caught it}, per-reviewer x PR
               {convergence: one_shot|converging|churning|mixed}, per-PR
               best-catch / worst-noise.
6 aggregate.py Joins verdicts with mechanical metrics into the synthesis tables
               + summary.json (machine-readable — the feedback-loop hook).
7 converge.py  Answers Q4 precisely: classifies every round>=2 inline comment
               as converging (on code the triggering push changed / a
               follow-up) vs re-scan (fresh issue on code unchanged since
               round 1), using per-commit file lists and inter-round timing.
```

## Pilot validation — `phos-editor/core`, 181 PRs

The pipeline was piloted end-to-end on `phos-editor/core` (a Rust repo, 181
merged PRs) and produced an unambiguous, defensible verdict — evidence it works
end to end. These are **the pilot's results, not a universal answer**; the
point of the skill is to re-run them on YOUR repo.

- **Reviews help, clearly:** 71 true-positive critical catches over 118
  reviewed PRs (0.6/PR), 63 of them real bug/correctness/security defects (path
  traversal, EXIF corruption, wasm32 overflow → infinite-loop, non-deterministic
  output). Latency cost modest; the *unreviewed* era had the worst p90 tail
  (556 min).
- **One strong primary ≫ a committee:** Copilot delivered **82% of high-value
  catches** at a **5% false-positive rate** (mean value 1.61, 57% useful).
  Second reviewers added < 0.7 unique-useful findings/PR and ~half-duplicated
  the primary.
- **Reviewers differ a lot:** Copilot ≫ Gemini (mean 1.18, 4% halluc,
  perf/maintainability niche) > CodeRabbit (mean 1.00, **56% noise, 17%
  hallucination**, niche = determinism/reproducibility).
- **Re-request is front-loaded:** round 1 ≈ 85% of all value; round ≥2 inline
  comments are **68% converging / 7% pure re-scan**, but yield only ~0.6 *new*
  useful catches per re-requested PR — concentrated in the first re-review
  (later rounds mostly re-flag unresolved threads).
- **Cost of the audit:** ~1.3k API calls + 1.59M agent tokens for 118 PRs (full
  tier).

Net policy guidance the pilot yielded: keep one strong primary, at most one
scoped second reviewer, cap re-request rounds (~2) with the breaker, and lean
on thread-resolution to stop re-raise traffic.

## Caveats you MUST report when quoting numbers

- **The "which reviewer is better" axis (Q3) needs config to have varied over
  time** (or reviewers to run in parallel). A repo on one stable reviewer still
  gets value / noise / convergence, but not the cross-reviewer comparison.
- **Era size is a confounder** — later epics are usually larger PRs, which
  inflates latency independent of review policy. Report era size and lean on
  per-finding value, not raw latency, for cross-era claims.
- **Value / novelty are single-judge LLM ratings (±1 noise).** Corroborate with
  the mechanical resolved/actioned rates from the cheap tier before acting.

## Maintaining the config block

`scripts/audit_config.py` is the one place that knows specific bots, and it is
the main ongoing maintenance burden — Copilot / CodeRabbit / Gemini change
their login handles and markdown output formats over time, so:

- **The bot login map (`BOTS`)** maps every login a bot posts under (top-level
  review author AND inline display login, with/without `[bot]`) to one
  canonical name. Coding agents (`copilot-swe-agent`, `claude[bot]`) are PR
  *authors/fixers*, deliberately absent — never count them as reviewers.
- **The denoise regexes** (`DETAILS`, `BADGE`, `BOILER`, …) strip the
  collapsible walls and boilerplate while keeping outcome markers. When a bot
  changes its output, these stop matching and slim files bloat or lose signal.
- **Per-repo overrides without forking:** pass `--config audit.json` to
  `extract.py`; the overlay adds bots / boilerplate patterns:

  ```json
  {
    "bots": { "some-new-reviewer[bot]": "newbot" },
    "boiler_extra": ["(?is)Generated by newbot.*"]
  }
  ```

- **Add a fixture test** (next slice, see below) that runs `clean()` over a
  captured CodeRabbit/Copilot comment and asserts the shrink + marker retention,
  so the gate flags the day denoise stops matching instead of you discovering it
  in a bad report.

## Scope — what landed vs deferred

This skill is the **repo-agnostic mechanical pipeline + the skill scaffold +
this methodology**, with the LLM-judge tier (`stage2.workflow.js`)
parameterized and wired but exercised only via the `release` Workflow runtime.

Deferred to follow-up slices (tracked in #740):

- A **denoise fixture test** under `tests/` (captured bot comments → assert
  shrink + outcome-marker retention) so the gate catches config rot.
- `--sample N` stratified-by-era sampling in `extract.py` for very large repos.
- A thin `release-core` entrypoint that runs the mechanical tier end to end
  (today the stages are invoked directly, which is fine for an audit run).
