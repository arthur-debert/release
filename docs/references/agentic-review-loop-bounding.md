# Bounding Agentic Review/Critique Loops — Landscape Survey

Research input for designing termination heuristics in the
`gh-pr-review-loop` skill (Claude as coder, GitHub Copilot as
reviewer). Surveyed May 2026.

## Why this exists

A 22-cycle, 3473-line PR (dodot #118) on what should have been a
~150-line bounded fix. Drift came from local hill-climbing: each
review pass surfaced edge cases of code added in the *previous*
pass, plus self-induced scope expansion (a cache-layout migration
that wasn't required by the issue). Want to design heuristics that
detect divergence trajectories, not just count cycles.

## 1. Landscape table

| Tool / Framework | Termination signal | Cap type | Notes / source |
|---|---|---|---|
| **Aider** | `--max-reflections` (default **3**) on lint/test fix-up cycles. After N: "Only N reflections allowed, stopping." | Hard cap, configurable | Added because "if aider is unable to fix lint error, it will loop forever without adding or changing code." [docs](https://aider.chat/docs/usage/lint-test.html), [issue #1090](https://github.com/paul-gauthier/aider/issues/1090) |
| **LangGraph** | `recursion_limit` (default **25** super-steps); `RemainingSteps` channel lets nodes branch on "≤2 steps left." Throws `GraphRecursionError`. | Hard cap, configurable; adaptive via `RemainingSteps` | [docs](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT) |
| **LangChain (legacy AgentExecutor)** | `max_iterations` + `max_execution_time` | Hard cap | Standard agent termination contract |
| **SWE-agent / mini-SWE-agent** | `per_instance_cost_limit` ($1–$2), `step_limit` (~50–250 turns), `MSWEA_GLOBAL_*` env caps | Hard caps on **cost** and **steps**, no convergence detection | [docs](https://swe-agent.com/latest/usage/competitive_runs/); SWE-Effi paper notes agents "consume excessive resources while stuck on unsolvable tasks" ([arxiv 2509.09853](https://arxiv.org/html/2509.09853v1)) |
| **Cursor (agent)** | Customer-side `MAX_ITERATIONS` via Stop hook scripts; otherwise wall-clock | Hard cap (user-defined) | [agent best practices](https://cursor.com/blog/agent-best-practices), [scaling agents](https://cursor.com/blog/scaling-agents) |
| **Cline** | Human-in-the-loop per step; no autonomous loop bound — every action is permission-gated | "Cap" = the human | [cline/cline](https://github.com/cline/cline) |
| **Devin (Cognition)** | Self-testing → autofix on lint/CI/review-bot comments. **No publicly documented cap or convergence test.** Implicit budget = wall-clock + session/credit accounting. | Implicit / not disclosed | [Closing the agent loop](https://cognition.ai/blog/closing-the-agent-loop-devin-autofixes-review-comments) |
| **GitHub Copilot cloud agent** | Session limit + weekly token quota; no documented per-PR review-cycle cap | Resource cap (not loop cap) | [Copilot usage limits](https://docs.github.com/en/copilot/concepts/usage-limits) |
| **Constitutional AI (Anthropic)** | SL stage uses a **fixed small number** of critique-revise pairs sampled per prompt; further revisions help marginally. Not adaptive. | Fixed pass count | [arxiv 2212.08073](https://arxiv.org/abs/2212.08073) |
| **Reflexion / MAR (NeurIPS '25)** | Trial-count cap; MAR finds "increasing trial limits yielded marginal improvements with sharply diminishing returns beyond certain thresholds" | Hard trial cap | [MAR arxiv 2512.20845](https://arxiv.org/html/2512.20845) |
| **Self-Reflection in LLM Agents (Renze & Guven, 2024)** | Empirically caps trials; documents that **intrinsic self-correction without external signal degrades** beyond small N | Empirical guidance | [arxiv 2405.06682](https://arxiv.org/abs/2405.06682) |
| **AutoGPT / BabyAGI** | `maxChainLength` (default 10 in many forks); BabyAGI gates *new task admission* against the goal — a **scope admission filter** more than a loop cap | Hard cap + scope filter | Community write-ups |
| **QuantumBlack (McKinsey)** | "Cap iterations at **3–5 attempts**; if evals don't pass, fail and roll back to human" | Hard cap + escalation | [QuantumBlack](https://medium.com/quantumblack/agentic-workflows-for-software-development-dc8e64f4a79d) |
| **"Double loop" model (Test Double)** | Outer loop = product behavior; inner loop = code quality. Termination: **"acceptable, not perfect — keep momentum."** Narrow scope per loop. | Soft / scope-based | [testdouble.com](https://testdouble.com/insights/youre-holding-it-wrong-the-double-loop-model-for-agentic-coding) |
| **Adversarial 2-AI PR review (agnihotry)** | Stops on (1) tests green, (2) human-judge resolves dispute, (3) **coder agent records explicit disagreement as a PR comment instead of committing** | Multi-signal | [Two AIs, One PR](https://p.agnihotry.com/post/two-ais-one-pr-adversarial-code-review-loop/) |
| **Addy Osmani — self-improving agents** | Composite: max-loop cap (~50), wall-clock cap, **idle detection** ("no new commit in 5 iterations → break"), **diff-size sanity check** ("abort if diff much larger than expected or touches files outside scope") | Composite, adaptive | [addyosmani.com/self-improving-agents](https://addyosmani.com/blog/self-improving-agents/) |
| **LLM task-drift detection (Abdelnabi et al., 2024)** | **Activation-delta classifier** detects drift between pre- and post-context model activations; near-perfect ROC AUC | Research, not productized | [arxiv 2406.00799](https://arxiv.org/html/2406.00799v6) |

## 2. Patterns that recur

- **Hard cycle/step cap is universal.** Aider's 3, LangGraph's 25, SWE-agent's step_limit, QuantumBlack's "3–5" — everyone's first line of defense is a flat integer.
- **Cost / token / wall-clock budget is the second line.** Especially in academic / batch settings (SWE-agent's `per_instance_cost_limit`). Bounds *unsolvable* tasks where step count is also growing.
- **Empirical consensus on diminishing returns.** Multiple 2024–25 papers (MAR, Reflexion follow-ups, SETS) report self-critique payoff plateaus by iteration 3–5; intrinsic self-correction without an external signal can *degrade* output. Argues for low caps, not high ones.
- **External signal beats self-critique.** Tools that work (Aider, Devin) tie revisions to *external grounding*: lint exit code, test pass/fail, CI status, reviewer-bot output. Self-critique unbounded by external signal is the canonical runaway-loop antipattern.
- **Escalation-to-human is the canonical exit.** QuantumBlack, the adversarial 2-AI loop, Cline — the loop terminates *into a human queue*, not into a "best effort" merge. Treated as a successful outcome, not a failure.

## 3. Notable absences / gaps

- **Diff-size trajectory as a convergence signal is barely used.** Only Addy Osmani's blog explicitly mentions "abort if diff much larger than expected." Nobody is doing what would be obvious: track |diff| across cycles and stop when it's monotonically growing (divergence) or stable at non-zero (oscillation / write-then-revert). LangGraph, Aider, SWE-agent all use cycle counts only.
- **Scope-drift detection inside a loop is mostly research.** Abdelnabi's activation-delta work and the "Goal Drift" paper exist, but no shipping framework has it as a first-class signal. Production tools rely on initial prompt clarity rather than mid-loop detection.
- **No canonical "two-pass rule."** Closest is QuantumBlack's "3–5 attempts then escalate" and Reflexion-paper trial counts. Nobody publishes "if attempt #2 at the same root cause fails, switch from fix-mode to redesign-mode" — which is what experienced engineers actually do.
- **Information-asymmetry handling is shallow.** The iAgents paper ([arxiv 2406.14928](https://arxiv.org/abs/2406.14928)) names the problem; production tools handle it with a shared CLAUDE.md / context file fed to both agents. **Nobody treats reviewer comments as "possibly wrong because reviewer lacks context"** — they're treated as ground truth to be addressed.
- **"Coder disagrees with critique" as first-class outcome is rare.** Only the agnihotry adversarial-loop post explicitly designs for it: *"If Claude disagrees with a finding, no commit, just a PR comment explaining why."* LangGraph, Aider, Devin, Copilot all assume comments are to be addressed, not contested.
- **Oscillation / write-then-revert detection.** No major framework detects "you added X in cycle N and removed it in cycle N+1" — exactly the "edge case of code added in previous pass" failure mode, invisible to a flat cycle cap.

## 4. Recommendations for our case (Claude coder + Copilot reviewer over GitHub PR)

### Map well

1. **Low hard cap (3–5 cycles), not 10+.** Empirical literature is consistent. Match Aider's `3` rather than LangGraph's `25`. The 22-cycle PR was 4–7× past where ROI plateaus.
2. **Diff-size trajectory as a real termination signal.** Cheap on GitHub PRs (`gh pr diff | wc -l` per cycle). Stop when (a) diff is *growing* across cycles N and N+1 (divergence — the scope-expansion failure mode), or (b) total |diff| exceeds a budget proportional to the original ask. **This is the gap the field has and we should fill it.**
3. **External signal gating.** Only enter another revise cycle if Copilot returned *new* findings vs. last cycle. Hash the comment set; if identical or a subset of the previous cycle's resolved set, stop — fixed point or oscillation.
4. **Coder-disagreement as first-class.** Per the agnihotry adversarial pattern: Claude should be allowed to respond "rejected, here's why" inline on the PR without committing. Highest-leverage single change in a Copilot-driven flow, because Copilot's reviewer suggestions have no project context and are often wrong by design.
5. **Two-pass escalation rule.** If cycle 2 fails to address the *same finding* that cycle 1 attempted, escalate to "redesign or human" — don't try cycle 3 on the same comment thread. Novel in published work but matches how the QuantumBlack 3–5 cap works in practice.

### Don't map well

- **Cost/token budgets** (SWE-agent style) — overkill for a per-PR loop where GitHub-level rate limits and human visibility already exist. Use cycle count + diff-size instead.
- **Activation-delta drift detection** — research-grade, no API surface against Copilot.
- **LangGraph-style 25-cycle ceilings** — too permissive; the whole point is to stop *before* 22 cycles, not at 25.
- **Reflexion-style "trials"** — designed for tasks with a verifiable success oracle (passing tests). PR review comments aren't a clean oracle; treating them as one is what produces the failure mode.

### Concrete heuristic stack (priority order)

1. **Cycle count cap = 3** (hard); 5 (soft, requires explicit user opt-in to extend).
2. **Per-cycle: comment-set hashing.** Compare to previous; if subset/identical → stop.
3. **Per-cycle: diff-size delta.** If growing 2 cycles in a row → stop, surface to human.
4. **Per-comment: attempt tracking.** On second failure of same finding → escalate, don't retry.
5. **First-class "reject with rationale"** outcome that posts a PR comment instead of a commit. Reviewer agent's verdict is *advisory*, not authoritative — the right framing given information asymmetry.
6. **Revert-within-PR detection.** Commit in this PR reverts another commit in this PR → automatic stop, prompt design re-read. (Not in literature; pattern observed in dodot #118 `b84ffa2 → f3d5770`.)

### Adjacent work outside the skill

- **`.github/copilot-instructions.md`** in each consumer repo, priming Copilot with project posture (pre-release, single-author, no BC, no users-in-the-wild) — neutralizes maturity-assumption comments at source rather than filtering them mid-loop.
- **PR template scope contract** — "in scope: X, Y. out of scope: Z (filing #N)." Both agents have a contract to point at when concerns drift. Templated from this `release/` repo so all consumers inherit.

## Key sources (spot-checkable)

- [aider lint-test docs](https://aider.chat/docs/usage/lint-test.html), [issue #1090 — runaway lint loop](https://github.com/paul-gauthier/aider/issues/1090)
- [LangGraph GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)
- [SWE-agent competitive runs / cost limits](https://swe-agent.com/latest/usage/competitive_runs/), [SWE-Effi paper](https://arxiv.org/html/2509.09853v1)
- [Cognition: Closing the Agent Loop](https://cognition.ai/blog/closing-the-agent-loop-devin-autofixes-review-comments)
- [Constitutional AI paper](https://arxiv.org/abs/2212.08073)
- [Multi-Agent Reflexion (MAR)](https://arxiv.org/html/2512.20845), [Self-Reflection in LLM Agents](https://arxiv.org/abs/2405.06682), [SETS](https://arxiv.org/pdf/2501.19306)
- [Two AIs, One PR — adversarial review loop](https://p.agnihotry.com/post/two-ais-one-pr-adversarial-code-review-loop/)
- [Addy Osmani — Self-Improving Agents](https://addyosmani.com/blog/self-improving-agents/)
- [Test Double — Double Loop Model](https://testdouble.com/insights/youre-holding-it-wrong-the-double-loop-model-for-agentic-coding)
- [Task Drift via Activation Deltas](https://arxiv.org/html/2406.00799v6), [Goal Drift in LM Agents](https://arxiv.org/html/2505.02709v1)
- [Information Asymmetry / iAgents](https://arxiv.org/abs/2406.14928)
- [QuantumBlack (McKinsey) — agentic workflows](https://medium.com/quantumblack/agentic-workflows-for-software-development-dc8e64f4a79d)
