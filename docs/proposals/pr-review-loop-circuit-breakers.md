# PR Review Loop Circuit Breakers

**Status:** proposed
**Author:** Arthur (with Claude)
**Date:** 2026-05-02
**Background:** [agentic-review-loop-bounding.md](../references/agentic-review-loop-bounding.md)

## Problem

Agentic PR review (Claude as coder, Copilot as reviewer) can drift
into runaway iteration. Concrete failure: dodot PR #118 — a bounded
~150-line fix turned into 22 review cycles, 3473 lines, 23 commits
over 7 hours, including a self-induced cache-layout migration that
was never required by the issue. Each Copilot pass surfaced edge
cases of code added in the _previous_ pass.

The current `gh-pr-review-loop` skill has no termination logic
beyond "address all comments, push fixups, repeat." It needs
circuit breakers that detect divergence trajectories rather than
just counting cycles.

## Goals

- Detect drift early (cycle 3, not cycle 22).
- Distinguish convergence from divergence using cheap, GitHub-derivable signals.
- Make "stop and surface to human" a first-class outcome, not a failure.
- Make "coder rejects reviewer comment" a first-class outcome — not every Copilot finding is correct, and Claude has project context Copilot lacks.
- Prevent self-induced scope expansion via explicit scope contracts.

## Non-goals

- No cost/token budgets — GitHub rate limits and human visibility are sufficient.
- No activation-delta drift detection — research-grade, no API surface against Copilot.
- No replacement of Copilot — the goal is to bound, not eliminate, the loop.
- No changes to the underlying `arthur-debert/gh-dagentic` reusable workflow — circuit breakers live in the skill orchestrator and per-repo policy files, not in CI.

## Heuristic stack (priority order)

Rules are evaluated _before_ opening a new fixup cycle. If any fires, the loop stops and the user is paged. They are deliberately conservative — false positives ("paged a human when convergence was still possible") are cheaper than false negatives (a 22-cycle PR).

### 1. Hard cycle cap = 3

Rationale: empirical literature is consistent — Aider defaults to 3, multiple 2024–25 papers report self-critique payoff plateaus by cycle 3–5 and _degrades_ beyond. Cycle 4 fires the breaker; no soft extension. If a project legitimately needs more, the user authorizes manually.

Implementation: count of Copilot reviews on the PR (one per cycle).

```sh
gh api "repos/$OWNER/$REPO/pulls/$PR/reviews" \
  | jq '[.[] | select(.user.login | startswith("Copilot"))] | length'
```

### 2. Comment-set hashing — detect fixed point / oscillation

If cycle N+1's open Copilot threads are a subset of (or identical to) cycle N's, stop. The reviewer is repeating itself — either the fix didn't land, or the comments are unfixable from the coder's context.

Implementation: hash sorted set of `(comment.path, comment.original_line, comment.body[:200])` per cycle. Compare across cycles.

### 3. Diff-size trajectory — detect divergence

If `|diff_N+1| > |diff_N|` for two consecutive cycles, stop. A converging review shrinks the diff (or holds it stable while improving correctness); a diverging one expands it. This is the signal that would have caught #118 at cycle 3.

Implementation: each Copilot review has a `commit_id` (head SHA at review time). Diff size at cycle N = `git diff <merge-base>..<commit_id_N> | wc -l`. All state derivable from GitHub history; no local cache needed.

Threshold: `diff_N+1 > diff_N * 1.1` — allow 10% jitter for legitimate test-add cycles.

### 4. Per-comment attempt tracking — two-pass rule

If the same Copilot finding is raised twice (cycle N flags X → coder fixes → cycle N+1 flags a problem with the fix on the same lines), don't try a third code attempt. Escalate to redesign-mode.

Implementation: for each Copilot comment in cycle N+1, check if a comment in cycle N covered the same `(path, line_range)` and was marked resolved. If yes, this is attempt #2 → stop.

This is the heuristic least represented in published frameworks. It's where we add value over Aider/LangGraph.

### 5. Revert-within-PR detection

If a commit on this branch reverts another commit on this branch (textual revert OR same-file-opposite-change pattern), stop and prompt design re-read. This was the `b84ffa2 → f3d5770` tell in #118 — the moment to break out of code-mode.

Implementation: `git log --grep="^Revert" <base>..<head>` catches the easy case. Harder case (same-file-opposite-change without "Revert" prefix) is out of scope for v1.

### 6. First-class "reject with rationale"

Not a circuit breaker — a missing escape hatch that _prevents_ breakers 1–3 from firing in the first place. When Claude judges a Copilot finding is wrong / out of scope / lacks project context, the right response is a PR comment explaining why, not a fixup commit.

The skill already documents this for category B comments ("Project ethos drift — push back with rationale"). Strengthen the framing: **Copilot's verdict is advisory, not authoritative.** A cycle that ends with N rejections + M fixups is normal and should not increment the "no progress" counter.

## Implementation surface

Changes span three locations.

### A. `~/h/release/templates/rust/copilot-instructions.md` (this repo)

Add a section priming Copilot with project posture, neutralizing maturity-assumption comments at source:

```markdown
## Project posture

These projects are pre-release, single-author, used by friends and the
author only. There are no production users, no SLAs, no stability
guarantees, no API consumers in the wild.

Implications:

- Backwards compatibility is not a goal. Renames, signature changes,
  and removed-fields are fine.
- No deprecation periods, no shim layers, no `// removed` comments,
  no migration adapters.
- Don't suggest "consider whether this breaks existing callers" —
  the only callers are in this repo.
- Don't suggest changelog/migration-guide entries beyond what already
  exists in `CHANGELOG_UNRELEASED.md` / `CHANGELOG.md`.
```

Also update the existing `What will get pushed back on` list to include scope-creep suggestions:

```markdown
- Suggestions to add migrations, schema-version bumps, or backfills for
  pre-release on-disk formats. Re-keying or re-laying-out is fine; the
  data is regenerable.
- Suggestions that expand a PR's surface beyond what its linked issue
  scoped. Out-of-scope concerns belong in a follow-up issue.
```

Replicate to per-stack templates as those land (`templates/electron/`, `templates/vsce-ext/`, etc.).

### B. `~/h/release/templates/rust/pull_request_template.md` (this repo)

Add a scope-contract section so both agents have an explicit reference:

```markdown
## Scope

**In scope:**

<!-- Files / behaviors this PR changes. Be specific. -->

**Out of scope (filed separately if needed):**

<!-- Adjacent concerns that surfaced but are not this PR. -->
```

When Copilot raises a concern that maps to "out of scope," Claude can reply pointing at the PR template as the contract — and the comment-set hash will treat that resolution as final, not as an unaddressed finding.

### C. New helper: `~/h/release/bin/gh-pr-loop-status`

A read-only command that prints the current state of all circuit breakers for a given PR. Output is YAML-ish so Claude can parse it before deciding whether to enter another cycle.

```
$ gh-pr-loop-status 118
pr: arthur-debert/dodot#118
cycles: 3
diff_size:
  cycle_1: 234
  cycle_2: 412
  cycle_3: 891         ← growing 2 cycles in a row, BREAKER 3 FIRED
comment_set:
  cycle_2_open: 7
  cycle_3_open: 6
  cycle_3_subset_of_cycle_2: false
repeat_findings: []
reverts_in_branch: 0
verdict: STOP
reason: diff size growing across cycles 1→2→3 (234→412→891)
```

All state derivable from `gh api` + `git log`; no local cache file.

When the verdict is `STOP`, the helper has a `--post-stop-comment <PR>` mode that performs **two coordinated writes**:

1. Posts a structured comment on the source PR (see §D template) — this is what the user reads on the PR they're working with, to make the land/pause/override decision.
2. Files an incident report on `arthur-debert/release` via `bin/report-incident` with `kind=circuit-breaker-fired` — this is the fleet-pulse entry, queryable later as `gh issue list -l incident:breaker`. See [incident-reporting-mechanism.md](incident-reporting-mechanism.md) for the schema and tooling.

Both writes happen atomically from the user's perspective: either both land or the helper exits non-zero and the loop stays paused. The PR comment links to the release/ issue ("filed: release/#N") and vice versa.

### C′. Consolidate PR-loop helpers from `~/h/dotfiles/gh/` into `~/h/release/`

The current split — policy/setup tools in `release/bin/`, day-to-day PR-loop helpers in `dotfiles/gh/bin/` — is artificial. Both sets serve the same `gh-pr-review-loop` skill and the same set of repos. The release tooling should be self-contained.

Files to migrate from `~/h/dotfiles/gh/`:

- `bin/gh-copilot-on` → `~/h/release/bin/gh-copilot-on`
- `bin/gh-copilot-off` → `~/h/release/bin/gh-copilot-off`
- `bin/gh-copilot-wait` → `~/h/release/bin/gh-copilot-wait`
- `bin/gh-copilot-review` → `~/h/release/bin/gh-copilot-review`
- `bin/gh-pr-checks-wait` → `~/h/release/bin/gh-pr-checks-wait`
- `Brewfile` → `~/h/release/Brewfile` (or merge into existing if there is one)
- `RELEASE-TOKEN.md` → `~/h/release/docs/dev/release-token.md`

Side-effects to handle in the same migration:

- Update dodot config so `dodot up release` covers what `dodot up gh` previously did. Retire the `gh` pack.
- Update the `gh-pr-review-loop` SKILL.md "The helpers" table to drop the two-homes framing.
- `git rm` the migrated files from dotfiles in the same change that adds them here, so PATH only finds the new location.

### D. `~/.claude/skills/gh-pr-review-loop/SKILL.md` (skill repo, outside this repo)

Add a new section between current step 5 (triage) and step 6 (push fixups):

```markdown
### Step 5b: check circuit breakers before another cycle

Before committing fixups in response to Copilot comments, run:

    gh-pr-loop-status <PR>

If verdict is STOP, do NOT push fixups, do NOT continue the loop.
The breaker has fired. Run:

    gh-pr-loop-status <PR> --post-stop-comment

This (a) posts a structured comment on the source PR (template
below) and (b) files an incident report on arthur-debert/release
with kind=circuit-breaker-fired. The PR comment is the user's
decision interface; the release/ issue is the fleet-pulse entry.
Then halt. Surface the situation to the user with a one-line summary
("breaker 3 fired on PR #N: diff growing 234→412→891; report
release/#M") and wait for their decision.

Categories of comment-handling now have an explicit count:

- A (real issues addressed) → fixup commits
- B (rejected with rationale) → PR-comment replies, NO commits
- C (cosmetic skipped) → no action

A cycle of all-B + all-C is normal and does not count as "no progress."
```

The structured stop-comment posted to the PR is the artifact the user reads to make a call. It must set the stage for that decision, not just announce that something stopped:

```markdown
## 🛑 Review-loop circuit breaker fired

**Breaker:** {1: cycle cap | 2: comment-set fixed point |
3: diff trajectory | 4: repeat finding | 5: revert-within-PR}

**Trajectory observed:**

- Cycle 1: {N₁ comments, {D₁} diff lines, head {sha₁}}
- Cycle 2: {N₂ comments, {D₂} diff lines, head {sha₂}}
- Cycle 3: {N₃ comments, {D₃} diff lines, head {sha₃}}

**Why this fired:** {one-paragraph diagnosis grounded in the data
above — e.g. "diff grew 234 → 412 → 891 across cycles 1–3, against
a typical convergence pattern of stable or shrinking. The expansion
happened in `src/cache/` (+520 lines over cycles 2–3) which was not
in the original PR scope per the issue."}

**What the data suggests:**
{Pick one or two of:

- "Self-induced scope expansion: the diff is growing in files outside
  the original issue's scope. Consider landing the in-scope portion
  and filing a follow-up for the rest."
- "Combinatorial edge-case chasing: each cycle's comments are about
  code added in the previous cycle. Consider stopping and re-reading
  the issue to confirm the current shape is right."
- "Reviewer is stuck on a finding the coder has tried to address
  twice. Consider whether this needs design rework or a push-back
  rather than a third fix attempt."
- "Comments raised in this cycle were all out-of-scope or stylistic;
  the in-scope work is done. Consider merging."
  }

**Decision points for the user:**

1. **Land what's good.** Squash + merge, file follow-up issues for
   the rest. (Recommended if in-scope work is complete.)
2. **Pause for design.** Close this PR or mark draft; re-read the
   issue; decide if the shape is right before more code.
3. **Override the breaker.** Manually authorize one more cycle.
   (Use sparingly — the breaker exists because cycle N+1 rarely
   converges when N didn't.)

**Fleet record:** arthur-debert/release#{issue_number}

—
Posted by `gh-pr-loop-status` (see
`~/h/release/docs/proposals/pr-review-loop-circuit-breakers.md`).
```

The `Fleet record:` line is filled in with the issue number returned
by `bin/report-incident` after the release/ issue is created. The
release/ issue's `## Source` block in turn links back to this PR,
so navigation is bidirectional.

Update the existing category-B documentation to add a line: _"Copilot's verdict is advisory, not authoritative. Pushing back is a first-class outcome, not a last resort."_

## Rollout plan

This proposal depends on [incident-reporting-mechanism.md](incident-reporting-mechanism.md) for the release/-side issue filing. The incident-reporting tools (`bin/report-incident` + `INCIDENT_TOKEN` distribution) must land before step 3 here.

1. **Land template changes (A + B) first.** Pure docs, low risk, immediately useful via next `sweep-github-policy` run on each consumer repo. Tag this repo as a PATCH (no input contract change).
2. **Migrate dotfiles/gh helpers into `release/bin/` (C′).** Standalone refactor, no behavior change. Update dodot config to point at the new location; retire the `gh` pack.
3. **Land helper (C) `gh-pr-loop-status` in `release/bin/`.** Depends on (C′) for co-location and on the incident-reporting mechanism for the `--post-stop-comment` two-write behavior. If incident-reporting isn't ready, ship `gh-pr-loop-status` with PR-comment-only mode and add the report call in a follow-up PATCH.
4. **Land skill changes (D) last.** Depends on (C) being on PATH and the structured-comment template existing.
5. **Backfill consumer repos via `sweep-github-policy --force`** to update existing `copilot-instructions.md` files. This is intrusive (touches every onboarded repo) — bundle it as a single rollout PR per repo.

## Decisions resolved

These were the open questions during drafting; settled before implementation begins.

1. **Cycle cap: 3.** Hard cap, no soft extension. Cycle 4 fires the breaker. If a project legitimately needs more, the user authorizes manually per-PR.
2. **Comment-set hash: `(path, line)` only**, not body text. Avoids false negatives when Copilot reworks the same finding's wording.
3. **Helper home: `~/h/release/bin/`.** Day-to-day PR-loop helpers do not belong in dotfiles. The split with `~/h/dotfiles/gh/` is being collapsed (see §C′).
4. **Breaker behavior: STOP and post.** Not "warn." When the breaker fires, the helper halts the loop AND posts a structured PR comment that sets up the user's decision (see §D template). Continuing requires an explicit user override.
5. **Per-repo opt-out: not needed.** These are personal projects with uniform setup. No `.github/pr-loop-config.yml` mechanism.

## Out of scope (future work)

- **Same-file-opposite-change revert detection** (vs. textual revert). Hard to do reliably without semantic diff. Skip for v1.
- **Activation-based scope-drift detection** (Abdelnabi et al. 2024). Research-grade, no API surface.
- **Multi-PR pattern detection** ("this is the 4th PR this week that ran 10+ cycles"). Reasonable telemetry play, but premature without baseline data.
- **Automatic follow-up issue filing** when "out of scope" is invoked. Convenient, but adds a new failure mode (stale auto-filed issues) that's worse than the manual workflow.
- **Onboarding electron / vsce-ext / nvim-plugin templates** with the same posture priming. Will land naturally as those stacks get their first onboarded repo.

## Success criteria

We'll know this worked if:

- No PR opened after rollout exceeds 5 review cycles without an explicit user override.
- The diff-size and comment-set breakers fire on at least one drift case in the first month, and the user agrees with the call (vs. overriding).
- Copilot stops raising "consider backwards compatibility" / "add a deprecation period" comments on these repos (template change A working).
- `gh-pr-loop-status` becomes a routine part of the loop — used before every fixup cycle, not just when something feels wrong.

If after 3 months the breakers have never fired and PR cycle counts stay under 3 organically, that's also a success — it means the template and prompt changes (A, B, D's wording) caught the drift before it started, and the breakers are just a backstop.
