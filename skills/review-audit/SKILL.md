---
name: review-audit
description: "Measure whether automated PR reviews (Copilot / Gemini / CodeRabbit) actually earn their cost on a given repo, so review policy is set by evidence instead of gut. Runs a repo-agnostic pipeline over the repo's own merged-PR history: a cheap mechanical tier (extract → enrich → finalize → summarize, zero agents) answers latency / volume / convergence, and an optional LLM-judge tier (a `release` Workflow fan-out + aggregate → converge) scores each bot comment for value, hallucination, novelty and re-request convergence. Emits a machine-readable summary.json. Use when: 'audit our PR reviews', 'are the review bots worth it', 'should we keep CodeRabbit / Gemini', 'is re-request-on-push paying off', 'one reviewer or several?'."
---

# review-audit

Decide **whether, which, and how many** bot reviewers a repo should run — and
whether the new-commits-→-new-review re-request loop pays off — from that
repo's own merged-PR history, not gut feeling. Sits next to the reviewer
*plumbing* (`gh-pr-review-loop`, the reviewer-adapter registry): those decide
**how** to run reviewers; this decides **whether it's worth it**.

It answers four questions mechanically, per repo:

1. **Do reviews help at all?** (reviewed vs unreviewed eras: defect catch
   rate, latency delta)
2. **One reviewer or several?** (marginal unique-useful findings of the Nth
   reviewer; redundancy)
3. **Are some reviewers better?** (value, hallucination rate, noise rate,
   niche per bot)
4. **Is new-commits-→-new-review good?** (do round ≥2 comments converge on
   what the push changed, or re-scan the original PR?)

A repo's PR history is often a **natural experiment**: reviewer config changes
over time (none → one bot → +a second → a trigger-policy change), giving clean
A/B eras the pipeline exploits. Full methodology, the validating pilot
(`phos-editor/core`, 181 PRs), and the caveats are in
[`METHODOLOGY.md`](METHODOLOGY.md) — **read it before quoting any number**, it
documents the confounders (era size, single-judge ±1 noise, the
varied-config requirement for the cross-reviewer axis).

## Two tiers

| Tier | Stages | Agents? | Cost | Answers |
|---|---|---|---|---|
| `mechanical` | 1–4 | none | ~free, minutes | latency, volume, action-rate, re-request onset, churn |
| `full` | + 5–7 | LLM judge | ~1.6M tokens / ~120 PRs (linear) | value, hallucination, novelty, convergence, the marginal-second-reviewer call |

Always run `mechanical` first — it is already informative and free, and it is
the dry-run. Add `full` only when you need the per-comment value judgment.

## The scripts (all under `scripts/`, repo-agnostic)

Everything repo-specific is a **parameter**: `--repo OWNER/NAME` (defaults to
the current checkout's git remote), `--dir PATH` (the output dir, default
`./analysis/reviews`). The **one config block that rots** — the bot login map
and the markdown denoise regexes — lives in `scripts/audit_config.py`; override
per-repo without forking via a JSON overlay (`--config audit.json`). See
[`METHODOLOGY.md`](METHODOLOGY.md#maintaining-the-config-block).

| # | Script | Tier | What it does |
|---|---|---|---|
| 1 | `extract.py` | mech | REST pull of every merged PR; **de-noises** bot markdown (~38× shrink on CodeRabbit) keeping outcome markers; writes `raw/` + `slim/` + `metrics.jsonl`. Resumable, rate-budget-aware. |
| 2 | `enrich.py` | mech | GraphQL `reviewThreads` → true `isResolved` / `isOutdated` / `resolvedBy` per thread (REST can't). |
| 3 | `finalize.py` | mech | Merges each thread's `diff_hunk` (the exact code) into slim, so judges check claims against real code. |
| 4 | `summarize.py` | mech | Zero-agent headline metrics: latency by era, per-reviewer volume + action-rate, the rounds-vs-PR curve dating re-request onset, churn. |
| 5 | `stage2.workflow.js` | full | The LLM-judge tier as a **`release` Workflow fan-out** (~4 PRs/agent); schema-enforced per-comment / per-reviewer / per-PR verdicts → `verdicts/`. |
| 6 | `aggregate.py` | full | Joins verdicts with mechanical metrics → synthesis tables + **`summary.json`** (the stable contract for feedback-loop automation). |
| 7 | `converge.py` | full | Classifies every round ≥2 inline comment as converging (on code the push changed / a follow-up) vs re-scan (fresh issue on unchanged code). |

## Running it

`gh` provides auth; run from anywhere (pass `--repo`) or inside the target
repo's checkout (auto-detected). Pick an output dir once and pass the same
`--dir` to every stage.

### Mechanical tier (cheap default)

```sh
# Run from the repo root. DIR must be ABSOLUTE: stage 5 (the release Workflow)
# requires an absolute `dir`, so anchor it here and reuse the same value for
# every stage — never a path relative to scripts/.
SCRIPTS=skills/review-audit/scripts
DIR="$(pwd)/analysis/reviews"      # absolute; or any other abs path

python3 "$SCRIPTS/extract.py"   --repo OWNER/NAME --dir "$DIR"   # 1: pull + denoise (slow, resumable)
python3 "$SCRIPTS/enrich.py"    --repo OWNER/NAME --dir "$DIR"   # 2: resolution flags
python3 "$SCRIPTS/finalize.py"  --dir "$DIR"                     # 3: diff_hunks
python3 "$SCRIPTS/summarize.py" --dir "$DIR"                     # 4: headline metrics  <-- read this
```

Checkpoint after stage 4: the latency/volume/convergence tables alone often
answer "do reviews help" and "is re-request front-loaded". If that's enough,
**stop** — you never paid for agents.

### Full tier (adds the LLM judgment)

Stage 5 is a `release` Workflow (the judging fan-out), so it runs under the
Workflow runtime, not bare node. Pass the **same** output dir; the reviewed-PR
list is derived from the slim files (no hardcoded list):

```sh
# Self-contained: redeclare the same values as the mechanical tier (run from
# the repo root; DIR must be the SAME absolute path you used above).
SCRIPTS=skills/review-audit/scripts
DIR="$(pwd)/analysis/reviews"

# 5: judge — one agent per ~4 reviewed PRs, writes verdicts/pr-*.json
#    args: { dir: '<abs DIR>', reviewers: ['copilot','gemini','coderabbit'], batch: 4 }
#    pass the SAME absolute $DIR from the mechanical tier above.
#    (run via the release Workflow runner; see METHODOLOGY.md for the invocation)

python3 "$SCRIPTS/aggregate.py" --dir "$DIR"   # 6: synthesis tables + summary.json
python3 "$SCRIPTS/converge.py"  --repo OWNER/NAME --dir "$DIR"   # 7: re-request convergence
```

For very large repos, `extract.py --sample N` subsamples to N PRs spread
**evenly across the PR-number timeline** — a proxy for the review-config era
(reviewer changes are chronological), so every era stays represented instead of
over-sampling the most recent PRs; the full tier is ~linear in PRs. The
cross-**reviewer** comparison (Q3) only holds if config **varied over time** or
bots ran in parallel — a repo on one stable reviewer still gets
value/noise/convergence, just not the head-to-head.

## Output contract

`summary.json` (written by `aggregate.py`) is the stable, machine-readable
result: per-reviewer `{mean_value, useful_pct, noise_pct, halluc_pct,
acted_pct, unique_pct, convergence, mean_net_value}`, the high-value catch
count, and false-positive counts per reviewer. It is the hook a feedback-loop
automation consumes to auto-recommend review policy.

## What you do with the verdict

The pilot's policy guidance (one strong primary, at most one scoped second
reviewer, cap re-request rounds with the breaker, lean on thread-resolution to
stop re-raise traffic) is **the pilot's result, not a universal answer** — run
it on YOUR repo. The numbers feed the review-policy knobs the
`gh-pr-review-loop` / reviewer-adapter machinery exposes.

## Pitfalls

- **Don't quote cross-era latency as the headline** — later eras are usually
  larger PRs (a confounder). Lead with per-finding value; report era size.
- **Value/novelty are single-judge LLM ratings (±1 noise)** — corroborate with
  the mechanical resolved/actioned rates from the cheap tier.
- **The denoise regexes rot.** If `summarize.py` shows a bot you know reviewed
  with near-zero findings, or slim files still carry `<details>` walls, refresh
  `audit_config.py` (and add a fixture — see METHODOLOGY).
- **Re-run is idempotent** — `extract.py` skips PRs whose slim file exists;
  delete a slim file to re-pull just that PR.
