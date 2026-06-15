# The standard live-fire prompt

This is the **one** reusable, repo-independent prompt that powers the live-fire
verification harness (release#663). Firing it at a consumer is a *consumer
check*; firing it at N consumers in parallel is a *fleet rollout check*. It
exercises the whole release-managed loop authentically **and leaves merged value
behind**, then reports the friction back to release as structured feedback (the
self-improving loop, release#348).

See [`standardization-model.md`](standardization-model.md) → "Verification —
live-fire, not synthetic" for *why* this complements the synthetic canary and
`release-core admin repos poke`.

## How it differs from an `orc probe` eval prompt

`orc probe` prompts are environment evaluations ("this is not a coding task").
This one is the opposite: a **real coding + release task**. It edits code, runs
the gate, opens a PR, and cuts a throwaway release. Run it as a coding session
(acceptEdits / a bounded clone that opens a real PR), not as a probe.

## The contract

- **Discovery is part of the test.** Do *not* pre-teach the agent how this repo
  releases, lints, or runs tests. The whole point is to observe how far it gets
  using only what the repo ships (`release-core how-to`, `release-core --help`,
  `--help` on the tools, the CLAUDE.md stub, the one distributed
  `gh-pr-review-loop` skill). Every place it has to guess, work around, or fails
  to discover is a release bug — capture it.
- **Leave value behind.** The coverage work is a real, mergeable improvement,
  not a throwaway. The PR goes through the normal loop to ready.
- **The release half is throwaway.** The `-release-rc` cut is a *verification*
  cut: tag-only, no version-line pollution, deleted on teardown (release#663).
- **End with structured feedback.** The final output MUST be the machine-
  readable feedback block defined below — that is what the rollout runner
  harvests and files into the release#348 inbox.

## The prompt

> Copy everything in the fenced block below verbatim as the agent's task. It is
> repo-independent — it names no specific tool, path, or Kind on purpose.

````text
You are working in a software repository whose release/CI tooling is managed by
arthur-debert/release. This is a real task: you will improve test coverage, open
a PR, and cut a throwaway verification release. Treat every step as a test of
whether the managed tooling is discoverable and works — record friction as you
go, because reporting it is the deliverable.

Do NOT ask me how to do things. Discover them from what the repo ships:
`release-core how-to`, `release-core --help`, `--help` on any tool, the
CLAUDE.md / AGENTS.md stub, and the available skills. If you cannot discover how
to do something, that is a finding — note it and use your best guess.

1. COVERAGE. Determine how this repo measures test coverage and run it. Find one
   module that is both important (load-bearing, widely used) and poorly tested.
   Improve its tests — and the code if a test surfaces a real bug. Keep the
   change focused and genuinely mergeable.

2. COMMIT. Stage and commit your work. The pre-commit quality gate must run.
   Report whether it ran, what it checked, and whether its output was useful
   (did a failure tell you how to fix it?). Add a changelog fragment if the repo
   requires one — discover how.

3. PR. Open a pull request. Discover and follow this repo's PR review loop
   (drive it to the point a human would merge: reviews addressed, CI green,
   mergeable). Do not merge it yourself; stop at ready.

4. RELEASE HALF. Cut a throwaway verification release to exercise the release
   pipeline without polluting the version line: use the reserved pre-release
   suffix `-release-rc` (e.g. if the current version is 1.4.2, cut the bare
   version `1.4.3-release-rc` — no leading `v`; the cut command rejects a `v`
   prefix and the resulting tag becomes `v1.4.3-release-rc`). Discover the cut
   command. Report whether it dispatched,
   whether the pipeline ran prep → build → (sign/notarize) → publish, and
   whether it left the branch / version line clean. Do NOT clean up the rc
   tag/release yourself — the harness does teardown.

5. FEEDBACK. End your response with the structured feedback block specified
   below — nothing after it. For each step, report how you discovered how to do
   it, what tripped you, and anything missing, inaccurate, or requiring a
   workaround. Be specific (name the command, file, or doc). "It worked, no
   friction" is a valid and useful entry.

Output the feedback as a single fenced ```yaml block with this shape:

```yaml
repo: <owner/name>
verdict: <clean|minor-friction|blocked>
pr: <url, or "none — blocked at step N">
rc: <vX.Y.Z-release-rc tag cut, or "none — blocked at step N">
findings:
  - step: <coverage|commit|pr|release|discovery>
    component: <release surface, e.g. how-to|gate|changelog|pr-loop|cut|docs|skills>
    severity: <blocker|friction|papercut|ok>
    what: <what happened, specifically>
    expected: <what you expected / what would have helped>
```
````

## Teardown (harness responsibility, not the agent's)

After harvesting feedback, the rollout runner deletes the `-release-rc` tag and
its GitHub pre-release on each consumer (the prepare step never advanced the
branch, so nothing else needs reverting). The coverage PR is left for the human
to merge — that is the "value left behind".

## Routing feedback (release#348)

The rollout runner parses each consumer's `findings` and files them into the
release#348 inbox (`release-core admin inbox`), grouped by `component`. A finding
that recurs across consumers is a fleet-wide release bug; a one-off is usually
consumer-specific. `severity: ok` entries are dropped (signal, not noise).
