# ADR-0004: managed files are committed symlinks into a gitignored, bootstrap-composed tree

## Status

Accepted. Extends [ADR-0003](0003-pip-install-bootstrap-distribution.md) (pip is
how the package arrives) and supersedes the _committed-`.release/`_ decision of
[ADR-0001](0001-release-sync-build-dir-with-symlinks.md): the build dir + symlink
mechanism stays, but the build dir becomes **gitignored** (not committed) and is
composed at bootstrap from the installed package rather than synced as committed
content.

Sequencing: this is the design for **after** the pull model (ADR-0003) is live
and proven — i.e. after `release_core` is cut into a release that bundles its
config templates and the boot resolver is seeded fleet-wide. Do not pull the
committed-files floor out from under consumers before the bootstrap that replaces
it is boring-reliable. **phos-app / phos-core are excluded** until they stabilize.

## Context

After ADR-0003, `release_core` arrives as a pip-installed wheel and the tools are
console-scripts. But the _per-repo_ surface is still a grab-bag:

- `.release/` and a set of `bin/` shim symlinks are **committed** (ADR-0001);
- `lefthook.yml` and lint/format configs are **committed real files**;
- `CLAUDE.md` carries a managed block inside an otherwise per-repo file;
- skills are committed real files;
- the thin workflow callers are committed real files.

The committed-real-file parts share one failure mode that has been the dominant
operational pain: **silent per-repo divergence.** Nothing stops an agent (or a
human) from editing a managed file in place; nothing detects it; so over time
every repo drifts differently — one has stale orientation, another is missing a
binary tool, a third has a hand-edited gate. The files are "present and working"
in each repo, but the _fleet_ is inconsistent, and the inconsistency is invisible
until it bites in that repo.

ADR-0001 committed the materialized content on purpose, for **graceful
degradation / self-containedness**: a repo keeps working if `release` disappears.
That is a real value — but in practice it has bought us
stale-but-working, silently-divergent repos, which is exactly the state we most
need to escape.

## Decision

**1. No managed real files in the repo except an irreducible seed.** Everything
`release` owns is either a `release-core <verb>` subcommand (from the installed
package) or a **committed symlink** into a **gitignored** per-repo build dir.

**2. Bootstrap composes the build dir; the repo holds symlinks into it.** On
bootstrap (`release-core init`, invoked by SessionStart and CI), the installed
package composes each managed file — centralized parts from the package, plus any
per-repo `*.part.local` content concatenated in — into a gitignored per-repo dir
(`.release/`). The repo's managed paths are committed symlinks pointing into it.
The build dir is **per-repo** (composed output differs by kind/capabilities), so
it cannot live in the shared site-packages dir — it is a gitignored tree in the
repo, fed _from_ the package.

**3. Per-repo content uses `*.part.local`.** A file that legitimately mixes
managed + per-repo content ships a committed `*.part.local` (the only per-repo
bytes); bootstrap concatenates centralized + local into the composed file. The
composed file is never committed (its repo path is a symlink, so it _can't_ be).

**4. The symlink invariant is a drift gate.** Because every managed path is a
symlink into a gitignored tree, drift is no longer invisible:

- a managed path that is **not a symlink** → someone edited it in place → violation;
- the gitignored build dir (or the installed package dir) **appearing in git** → violation.

This is gate-able in pre-commit and CI. Drift goes from _invisible and per-repo_
to _impossible-or-loud and uniform_.

**5. Carve-outs — files that stay committed real files (NOT symlinks):**

- **Workflow files (`.github/workflows/*.yml`).** GitHub Actions reads workflow
  files as blobs from the tree; a symlink's blob is the link-path string, not
  YAML, so a symlinked workflow is invalid/ignored. They **must** be real files —
  and they already want to be: they are thin per-repo `@vN` callers whose heavy
  logic is remote (reusable workflows + composite actions), so nothing is lost.
- **`CLAUDE.md`.** Its job is cold-read orientation — on GitHub web, in a fresh
  clone, and for an agent that has _not yet_ bootstrapped. A symlink into the
  gitignored tree is dangling in exactly those moments. So `CLAUDE.md` stays a
  real committed file carrying a managed block (per release#360); `*.part.local`
  feeds the block, but the file itself is real and readable at rest.

Symlinked (the ideal case — no at-rest value, consumed only post-bootstrap):
`lefthook.yml`, lint/format configs (`.markdownlint.json`, `.yamllint`,
`.prettierignore`, …), skills, and the former `bin/` shims (now subcommands).

**6. The irreducible seed.** One thing can never be a symlink-into-the-package:
the bootstrapper itself — the boot resolver (`install-release-core`) and the
SessionStart hook that calls it. You cannot symlink the thing that creates the
symlinks. So the end state is exactly one tiny committed real file (the
resolver/hook, the synced bootstrap from release#433), and everything else is a
symlink or a subcommand.

**7. Triggers.** Bootstrap runs at **SessionStart** (local + cloud Claude Code)
and in **GitHub Actions** — the two contexts where a repo is acted on. Both
already invoke the resolver after ADR-0003.

## Consequences

### What we gain

- **Drift becomes impossible-or-loud and uniform.** The symlink invariant + the
  "package dir not in git" check, gated in pre-commit/CI, end silent per-repo
  divergence — the dominant pain.
- **Tiny committed surface.** One seed file + a set of symlinks + the `*.part.local`
  bytes a repo genuinely owns. No committed `.release/`, no `bin/` shims, no
  committed gate/config bodies.
- **One way, everywhere.** Every repo gets identical managed content composed the
  same way; the only per-repo bytes are `*.part.local` and the thin workflow
  callers.

### What we give up (accepted)

- **Graceful degradation / self-containedness, for the symlinked files.** Before
  bootstrap runs, those paths are dangling: a fresh clone or a pre-bootstrap read
  sees broken links rather than stale-but-working files. **This is accepted by
  decision:** a clearly-broken, uniform, gate-able failure is _preferable_ to the
  current silently-divergent state (no context in one repo, missing tools in
  another). Fail-loud-and-consistent over fail-silent-and-different. The two files
  where cold-read genuinely matters (`CLAUDE.md`, workflows) are carved out and
  stay real, so the degradation we keep is exactly the degradation that's worth
  keeping.
- **More dependence on the bootstrap working** (the "reliability paradox"):
  committed real files degrade to _stale-but-working_; symlinks-to-gitignored
  degrade to _dangling_. Mitigated by: bootstrap runs in both SessionStart and CI,
  is idempotent and best-effort, and — per Sequencing — this ADR is adopted only
  **after** the pull model is live and proven boring. We are deliberately trading
  a soft-but-silent failure mode for a hard-but-obvious one.

### Migration / sequencing

1. Pull model live + proven (ADR-0003): release cut with bundled templates, boot
   resolver seeded fleet-wide (one seed-propagate, skipping phos).
2. Collapse `bin/` shims → `release-core <verb>` subcommands.
3. Flip the composed build dir from committed `.release/` to **gitignored**;
   convert managed config/skill paths to symlinks; add the symlink + no-package-in-git
   drift gate to pre-commit and CI.
4. Keep workflow callers and `CLAUDE.md` as real committed files.
5. Retire `release-sync`'s committed-content materialization and the curl
   bootstrap (epic #416 step 11).

phos remains excluded throughout, folded in once it stabilizes.
