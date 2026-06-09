# ADR-0005: minimal consumer footprint — the binary is the sole carrier ("invoke, don't discover")

## Status

Accepted; in progress (epic [#501](https://github.com/arthur-debert/release/issues/501)).

Builds on [ADR-0003](0003-pip-install-bootstrap-distribution.md) (the wheel is
how the package arrives) and [ADR-0004](0004-symlinked-managed-files-into-the-installed-package.md)
(managed files are committed symlinks into a bootstrap-composed tree). Where
ADR-0004 asked *how should managed files be stored so drift is loud?*, this ADR
asks the prior question *should they be tracked files at all?* and answers
mostly no.

When `.release/` becomes gitignored and regenerated each session (the WS3/WS4
end state of this epic), this ADR **supersedes** the committed-content
provenance/drift machinery of [ADR-0001](0001-release-sync-build-dir-with-symlinks.md)
and [ADR-0002](0002-provenance-marker.md): with the tree gitignored and rebuilt
from the pinned binary, drift is impossible by construction, so drift-check and
the provenance marker have nothing left to do. Those two ADRs are marked
superseded only once that flip ships — not by this document alone.

## Context

A 2026-06-08 review found `release/` behaving as a self-managing **platform**
when the goal was a **library**: ~107K LOC, 443 template files, three core
re-architectures in five weeks, and 39% of all commits were fix/repair/bake
follow-ups.

The instability is not the auto-pull. Auto-updating one versioned binary is
deterministic and has been reliable. The instability is the **tracked in-repo
footprint**: every file materialized into a consumer (the CLAUDE.md block,
ORIENTATION.md, synced skills, `bin/` shims, committed `.release/` symlinks,
gate configs) is a thing that can drift, get gitignored, get shadowed, or sit
at a different version per repo. Checking 20 repos became "Russian roulette" —
each is plausibly fine and silently different.

ADR-0004 already shrinks the *storage* failure mode (symlinks into a gitignored
tree make in-place edits loud). But a symlink is still a tracked file: it can be
deleted, replaced, shadowed by a `lefthook-local.yml`, or dropped by a
consumer's `.gitignore`. The cheapest file to keep in sync is the one that
isn't there.

**Hard constraint (preserved):** never hand-update 20–30 repos on a change. The
auto-pull of the binary STAYS — this ADR removes tracked files, it does not
reintroduce per-repo edits.

## Decision

> **Invoke, don't discover.** `release-core` is the sole carrier and the sole
> invoker. The consumer repo holds (almost) nothing tracked.

A file gets committed into a consumer for one of four reasons; each dissolves
into a binary invocation:

| Why a file is in the repo today | Replacement |
|---|---|
| An agent/human reads guidance (skills, ORIENTATION, CLAUDE.md block) | `release-core how-to` prints it, version-correct, from the binary |
| Git hooks find config | `release-core gate` runs the gate from the binary |
| CI runs it | CI installs the binary and regenerates — same as session start |
| Tools auto-discover config | We invoke each tool with an explicit `--config` path |

### The target end-state footprint, per consumer

Irreducible (platform-forced, tiny, stable):

- `.github/workflows/*.yml` — thin `@vN` callers (GitHub-forced path).
- `.github/dependabot.yml`, `CODEOWNERS`, copilot config (GitHub-forced).
- **one** SessionStart bootstrap hook that installs/refreshes the binary and
  regenerates `.release/`.
- at most **one** small `.release.toml` of per-repo knobs (detect-kind may
  obviate even this).

Everything else moves into the binary, or into a **gitignored, ephemeral
`.release/`** regenerated from the pinned binary each session. Gitignored +
regenerated ⇒ drift is impossible by construction.

### The two carriers

- `release-core how-to` — the kind-aware procedural source of truth (lint /
  test / build / release / run + the draft-first dev cycle). It replaces the
  synced ORIENTATION.md and the per-Kind reference docs: the consumer CLAUDE.md
  becomes a short stub pointing here, so there is nothing to drift.
- `release-core gate` — the one quality entry, run identically locally and in
  CI, configured from `.release/`-rooted paths so it survives dropping the root
  discovery symlinks.

## Consequences

### What we gain

- **Drift is impossible by construction, not merely loud.** ADR-0004 made
  in-place edits detectable; this removes the file, so there is nothing to edit
  or detect. A gitignored, regenerated tree cannot diverge per repo.
- **One source of procedural truth.** The dev cycle stops living in six places
  (global CLAUDE.md, project CLAUDE.md, ORIENTATION.md, the dev-cycle docs, a
  skill, the PreToolUse guard) and lives in `release-core how-to` — kept in
  lockstep with `dev-cycle.lex` and the `gh-pr-review-loop` skill.
- **Docs shrink the same way.** Per-Kind "how to build/test this" content
  becomes `release-core how-to <kind>` output, not a hand-maintained file (the
  WS9 consolidation: 9 narrative `.lex` → 4; per-stack/per-category/per-component
  reference docs deleted).

### What we give up (accepted)

- **More dependence on the bootstrap working.** With the guidance and gate
  configs no longer at rest in the repo, a pre-bootstrap read sees less. This is
  the same trade ADR-0004 accepted (fail-loud-and-uniform over
  fail-silent-and-divergent), pushed one step further; mitigated because the
  bootstrap runs in both SessionStart and CI, is idempotent and best-effort, and
  this is adopted only after the pull model is proven boring.
- **A harness-visible skill file may still be required.** Claude Code triggers
  `/...` skills from files on disk, so at least one thin delegating skill may
  have to remain a synced file even when its body is `release-core how-to`
  output. Resolved per WS2/WS7, not by this ADR.

### Sequencing (the epic workstreams)

1. WS0 — prove every gate tool runs green from a `.release/`-rooted config with
   nothing at the repo root to discover. (Confirmed.)
2. WS1 — ship `release-core how-to` and `release-core gate`; agent-tune the help
   strings. (Shipped: [#502](https://github.com/arthur-debert/release/pull/502),
   [#504](https://github.com/arthur-debert/release/pull/504).)
3. WS2 — CLAUDE.md → short stub; stop syncing ORIENTATION.md and the infra
   skill set as files. (Shipped:
   [#523](https://github.com/arthur-debert/release/issues/523) — the CLAUDE.md
   block is a stub pointing at `release-core how-to`; ORIENTATION.md retired;
   PUSH_ALL_SKILLS trimmed to `gh-pr-review-loop` + `release-issue-relay`, the
   rest auto-swept on next init. One delegating skill kept per the open question.)
4. WS3 — gate configs into `.release/`; the gate runs from the binary so
   `lefthook.yml` leaves the consumer. (Shipped:
   [#524](https://github.com/arthur-debert/release/issues/524) — `lefthook.yml`
   + most lint/format configs are release-internal (materialized into `.release/`,
   no longer mirrored to the root; each tool is handed its config explicitly via
   `--config`/`-c`/`--ignore-path`). `.editorconfig` (editor-facing) and
   `.shellcheckrc` (shellcheck has no version-portable `--rcfile` on the fleet's
   0.9.0 — its rc must be root-discovered) stay mirrored. The git hook runs through the binary — `release-core gate
   --install-hook` writes `.git/hooks/pre-commit` → `release-core gate --hook`
   (staged, `--no-auto-install` so lefthook can't reclaim the hook). The
   broken-symlink sweep generalized to a mirrored-dest rule, so a migrated
   consumer's old root gate symlinks are swept on next init. Folds in the
   WS4-deferred root-`lefthook.yml` symlink drop. Rider: npm `typecheck` fails
   loud when TS is staged with no `typecheck` script — no more hollow green.)
5. WS4 — `.release/` gitignored + regenerated; delete the drift/sync subsystem.
   This is the flip that supersedes ADR-0001 and ADR-0002. (Shipped:
   [#521](https://github.com/arthur-debert/release/issues/521) — `.release/` is
   self-ignoring + composed by `release-core init`; the standalone `release-sync`
   / `release-drift-check` verbs, console-scripts, and `sync` CLI group were
   removed; CI materializes via `arm-gate`. The root `lefthook.yml` discovery
   symlink drop was deferred to a follow-up.)
6. WS5–WS8 — lock the irreducible footprint, migrate the fleet via pull,
   re-evaluate the self-improving machinery, and validate with fresh agents on
   real tasks.

`phos-app` / `phos-core` remain excluded until they stabilize, as in ADR-0004.
