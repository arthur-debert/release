# Welcome — this repo is managed by `release`

This repository's development infrastructure — the quality gate, CI workflows,
build, release, and the PR/dev workflow — is provided by
[`arthur-debert/release`][release] and synced in. It is **not** maintained here.
This note orients you: where you are, what is managed, and where problems go.

## What is managed (don't hand-edit)

- **`.release/`** is the materialized managed tree. It is regenerated wholesale
  from `release`; anything inside it is overwritten on the next refresh. You
  rarely run this by hand: **`release-core init` materializes the full managed
  tree** (this `.release/` build dir + every working-tree mirror — `bin/` tools,
  the skills, `ORIENTATION.md`, the lint configs, the `CLAUDE.md` managed block)
  **from the pulled release wheel and auto-commits any managed change.** This runs
  automatically at SessionStart, so the repo self-updates to the latest `release`
  with no manual step and no propagate PR. (`release-core init --config-only`
  refreshes just the lint/gate config subset — the rare escape hatch.)
- **`bin/`** holds release-provided tools, symlinked into `.release/`. The
  pre-commit gate (`lefthook.yml`), `.github/workflows/`, and the lint configs
  are managed too. Every managed task is run through the **`release-core`** CLI —
  `release-core --help` is the map (per-repo commands at the top level; fleet ops
  under `release-core admin`).
- **`app-bin/`** is this repo's own tooling — yours to edit freely.

Editing a managed file (anything that is a symlink into `.release/`) does not
stick: the next sync replaces it. Changes to managed infrastructure belong
upstream — see **Escalation** below.

## Skills (managed too — don't hand-copy)

This repo carries `release`'s official infrastructure and dev-cycle skills under
`.claude/skills/` — the PR review loop, review-response, upstream escalation, and
general dev skills (TDD, review, triage, diagnose, and more). They are **managed
and synced**, just like the rest of `.release/`: each is a symlink into the
materialized tree, regenerated on every sync.

- **Use the synced skills as-is.** Do **not** hand-edit or hand-copy them into
  this repo. A hand-copied skill drifts out of step with upstream — the synced
  symlink is what keeps your copy current. If a skill needs a fix, that is an
  upstream change (see **Escalation**).
- **Only application-domain skills are this repo's own.** Skills specific to this
  project's subject matter live here and are yours to maintain; the infra/dev
  skills are not.

## The dev flow at a glance

Pull requests are driven to _ready for human merge_ by a reviewer-agnostic state
engine. Rather than piecing together which reviews are pending, which threads
are open, and whether the PR is mergeable, you ask the engine where the PR
stands and act on what it reports:

```sh
release-core pr status <pr-number>
```

It reports the PR's lifecycle state and what is left before a human can merge.
**Draft vs ready is the turn-signal:** a **draft** PR is WIP that _you_ own; flipping
it to **ready** is the one signal that says "I'm done — a human can come in." Opening
the PR is not the end of your job; you own it through the whole loop:

1. **Open the PR as a _draft_** (`gh pr create --draft`), linking the issue. Draft = your
   turn. Copilot still reviews drafts (the review workflow fires at `opened` regardless
   of draft state), so a draft does **not** suppress the review.
2. **Poll `release-core pr status <pr>`.** It names what is outstanding: a pending
   review, unresolved threads, or failing checks.
3. **Clear what it names.** Fix the code or reply with a rationale, resolve each
   thread, push, and let checks go green. Never bypass the gate (`--no-verify`)
   to force a check past — fix the cause; CI re-runs the same gate on a clean
   runner.
4. **Repeat until the state is `ready`** (reviewed + CI green + mergeable). Reviews can
   lag — wait for them rather than declaring done early.
5. **Flip draft→ready (`gh pr ready`) — that is the handoff** — then stop. Don't
   self-merge; the final read and merge are the human's. If they ask for changes, flip
   back to draft, do the work, and re-flip to ready only when the new changes + checks pass.

That is the whole loop: open **draft** → poll the engine → clear what it names → `ready`
flip → hand off. Drive it through `release-core pr status`; don't reinvent it with
ad-hoc `gh api` calls. The canonical statement of this cycle is `docs/dev-cycle-task.lex`.

> **This is enforced, not advisory.** A PreToolUse guard (`bin/pr-loop-guard`)
> blocks a bare `gh pr create` so the loop can't be skipped by reaching for the
> raw helpers under task momentum. Engage the loop (invoke the
> `gh-pr-review-loop` skill) before opening the PR. If you are already running
> the loop and the guard blocks you, arm it once and retry:
> `touch "$(git rev-parse --git-dir)/pr-loop-armed"`. The arm is one-shot
> (consumed per PR), so it gates each PR, not just the first.

**Landing a feature or fix?** Add a changelog fragment in the same PR:

```sh
release-core changelog add <slug> "<one-line summary>"
```

It writes `CHANGELOG/unreleased-<slug>.md`. The release refuses to cut without
one — the prepare gate fails with _"No CHANGELOG/unreleased-\*.md fragments
found"_ — so a feature that merges without a fragment silently blocks the next
release until someone backfills it.

## Escalation — when managed infrastructure breaks

If a gate, workflow, or managed tool misbehaves, do **not** patch it in this
repo: the file is release's and your fix will not survive the next sync. Instead:

1. Unblock locally so your own work proceeds (for a pre-commit gate, a single
   `git commit --no-verify` is the usual escape hatch).
2. Search the [`release` issue tracker][issues] for a matching symptom.
3. If there is none, file one from inside this repo — it auto-collects the repo,
   branch, PR, and failing run for reproduction context.

```sh
release-core issue file <component> "<one-line symptom>"
```

The fix lands in `release`, is released, and reaches every consumer when the
floating major you pin (`@v2`) advances — your next SessionStart `release-core
init` pulls the new wheel and self-syncs the whole managed tree (auto-committing
the change). One fix, the whole fleet, nothing to hand-edit and no propagate PR.

[release]: https://github.com/arthur-debert/release
[issues]: https://github.com/arthur-debert/release/issues
