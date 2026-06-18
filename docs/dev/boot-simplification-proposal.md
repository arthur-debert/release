# Boot & Distribution Simplification — Proposal / De-risking Spec

Status: PROPOSAL (pre-epic). This is the shared artifact validators check against
and the eventual epic's design doc. Nothing here is built yet.

## North star

Minimize consumer **committed logic** (every committed file/line is staleness +
fanout surface). Everything pullable lives in the wheel. Boot is **fail-loud** —
no silent recovery on the foundation (if the wheel pull fails, halt; there is no
gate without `release-core` anyway). Single source of truth per fact, **offline +
auditable** where it is load-bearing.

## Target consumer committed footprint (end state)

- `.claude/settings.json` — SessionStart → `install-release-core`; PreToolUse → `pr-loop-guard`
- `CLAUDE.md` — ONE `@import` line
- `.claude/IMPORTANT-RELEASE.md` — the `@import` target (managed, auto-committed)
- `bin/install-release-core` — boot resolver (now tiny)
- `bin/pr-loop-guard` — PreToolUse guard (unchanged)
- `.github/workflows/*.yml` — thin launchers, `@vN` = **structural** axis only
- `skills/**` — managed (no `@import` equivalent)
- `.release.major.txt` — single committed source: the release-core major line
- `.release/` stays ephemeral/gitignored. **`bin/setup-dev-env.sh` is REMOVED.**

## The two version axes (must stay distinct, not redundant)

- **`.release.major.txt`** = release-core **logic** version (FREQUENT). Migrate = edit 1 file.
- **`uses: …@vN`** launcher ref = workflow **structure** version (RARE; matrix/artifact/job-graph).
- They couple only at the verb-signature boundary (a release-core major that renames a
  verb the workflow calls forces both to bump).

## Changes (IDs are stable; deps noted)

- **A. Stamp wheel version from the release tag** (kill the static `0.0.1`).
  Prereq for B, D. Touches the wheel build (`hatch_build.py` / pyproject) + `release.yml`.
- **B. Pip index on GitHub Pages** (PEP 503 or `--find-links`) whose entries point at
  the existing GitHub **release-asset** URLs; a `release.yml` step regenerates + deploys
  it. Consumers/CI install via the index + a version constraint. Repo is PUBLIC → no auth.
  Needs A. Deps used: only `click>=8,<9` from PyPI (use `--extra-index-url`, keep PyPI for deps).
- **C. `.release.major.txt`** — committed single source for the release-core major.
  Read offline by the bootstrap (`cat`) and by the reusable workflow (from the consumer
  checkout). REPLACES `derive_caller_major` (the `@vN` grep) as the major source.
- **D. Gut `install-release-core`.** REMOVE: `resolve_url`/`_resolve_url_list`, the
  exactly-one-asset check, `--force-reinstall`, the `release-source.tag` provenance
  sidecar, `derive_caller_major`, the `--user`/`--break-system-packages` no-ops. NEW body:
  read `.release.major.txt` → `pip install` from the index at that major → symlink
  console-scripts → run `release-core init`. Needs A, B, C.
- **E. Dissolve `bin/setup-dev-env.sh` into `release-core init`.** Move into wheel verbs:
  toolset arming (§0), pre-commit hook wiring (§0.2), dep caches (§2), NSS cert import
  (§2.5), submodule init (§0.0) — with a `--cloud` arm for cloud-only steps. SessionStart
  calls `install-release-core` directly. setup-dev-env.sh REMOVED. Needs D.
- **F. Fail-loud boot.** The wheel pull is load-bearing → halt on failure (drop the
  `|| warn`). Remove the "arm the gate before the pull so it survives a failed pull"
  ordering — it is hollow (no wheel ⇒ no `release-core gate --hook` ⇒ no gate anyway).
- **G. Parametrized reusable workflows.** `uses: …@vN` becomes the stable **structural**
  launcher; the reusable workflow reads `.release.major.txt` from the consumer checkout to
  install the right release-core. Workflows NEVER branch on version (no `if v2…elif v3`);
  they only **parametrize which wheel they install**. Needs C.
- **H. CLAUDE.md `@import`.** Replace the injected managed header BLOCK with a one-line
  `@.claude/IMPORTANT-RELEASE.md` + a committed managed target file. REMOVE the
  create/inject/refresh splice logic in `sync.py`.
- **I. Unify gate-toolset provisioning into the wheel** (`release-core gate --provision`
  or an init step), called by BOTH local boot and CI. Collapse the
  `bin-internal/provision-gate-toolset.sh` + `bin/gate-tool-versions.sh` duplication.
  Part of E.

## Migration-safety requirement (the anti-"mid-migration explosion" rule)

Each change must either (a) land independently and be absorbed lazily by the next
SessionStart pull, or (b) have its flag-day coupling stated explicitly with a safe
order. No consumer may be left in a broken half-state. Highest-risk coupling to prove
out: **G** (reusable workflow reading `.release.major.txt`) vs the file's PRESENCE in a
given consumer — what happens to a consumer that pulls the new workflow before it has
the file, and vice versa.

---

# VALIDATED PLAN (post six-agent de-risk)

Verdict: **feasible, contingent on landing order.** No blocker is fatal; the risk
is entirely mid-migration half-states, which the phased order below neutralizes.

## Root prerequisite

**A (version-stamp the wheel from the tag) gates everything.** D's provenance
(`release-core --version` replacing the `release-source.tag` sidecar) and B (the
index needs distinguishable versions) both depend on it. The static `0.0.1` lives
in FOUR places: `pyproject.toml`, `release_core/__init__.py`,
`release_core/prstate/__init__.py`, and test fixtures (`test_core_canary_run.py`).

## The self-healing migration (why consumers need no coordination)

Land C+D+H+E in one release tag. A consumer's next SessionStart: the (still-present)
`setup-dev-env.sh` calls the new `install-release-core` (synced from the new wheel)
→ which runs `release-core init` → `sync.py` seeds `.release.major.txt` (derived
from the consumer's `@vN` pins), writes `.claude/IMPORTANT-RELEASE.md`, rewrites
CLAUDE.md to the one-line `@import`, and auto-commits. One pull, self-heals, no
human, cloud-CI-safe. **`init` is the seeder** — answers "who creates the file."

## Landing order — additive-first, subtractive-LAST

- **Phase 0 (additive, zero consumer impact):** A then B. Wheel version becomes real;
  the index serves alongside the existing release-asset path (pure fallback).
- **Phase 1 (additive core, one release tag):** C + D + H + E + F + I, BUT **keep
  `setup-dev-env.sh` in the loop and do NOT flip `settings.json` yet.** During this
  phase provisioning runs via BOTH the old shell §0 AND init (redundant, safe) — so
  there is never a window where neither provisions. `install-release-core` is gutted
  but keeps `--from-source` and a transitional `derive_caller_major` FALLBACK.
- **Phase 2 (G, in-place on `@vN` WITH a fallback):** `arm-gate` reads
  `.release.major.txt` from the checkout, **falling back to its action_ref** when the
  file is absent. The fallback makes G safe to land in-place on the floating `@vN`
  even before every consumer has the file (resolves the floating-tag hazard without a
  disruptive `@v4` bump). Belt-and-suspenders: also run
  `release-core admin repos verify` to confirm the fleet has the file.
- **Phase 3 (subtractive, ONLY after fleet convergence is verified):** flip
  `settings.json` -> direct `install-release-core`; retire `setup-dev-env.sh` via the
  retired-files sweep; delete the `derive_caller_major` fallback, the arm-gate
  action_ref fallback, `release-source.tag`, `provision-gate-toolset.sh`,
  `gate-tool-versions.sh`. These are the transitional shims, scheduled for deletion.

## Three load-bearing safety rules

1. **`settings.json` flip + `setup-dev-env.sh` removal = Phase 3 ONLY.** Flipping in
   Phase 1 creates old-wheel+new-settings -> toolset never armed -> first commit fails.
2. **H must be atomic AND must never stage the consumer's CLAUDE.md.** Write the
   `@import` line + emit `.claude/IMPORTANT-RELEASE.md` together (a dangling `@import`
   breaks CLAUDE.md loading). After insertion CLAUDE.md is 100% consumer-owned — init
   manages ONLY `.claude/IMPORTANT-RELEASE.md`, never re-stages CLAUDE.md.
3. **G ships with the action_ref fallback**, removed only in Phase 3.

## Gaps the spec closed (were omissions, now decided)

- **`--from-source` STAYS** — load-bearing in `arm-gate`, `fleet-matrix.yml`,
  `install-release-core-pkg.sh` for canary/release-dev CI. Only `derive_caller_major`,
  `resolve_url`/`_resolve_url_list`, the asset-count check, `release-source.tag`, and
  the `--user`/`--break-system-packages` no-ops get gutted from `install-release-core`.
- **`app-bin/post-setup-hook.sh`** (consumer extension point) -> becomes a final
  `release-core init` step.
- **Index RC-pollution:** the index generator MUST filter `prerelease == false`
  (mirroring the resolver's release filter) or RC/verify cuts pollute the public
  index (#689-class).
- **Pages collision:** `mdbook.yml` + `mkdocs.yml` already deploy to `github-pages`.
  The pip index needs a separate branch/path + a concurrency group.
- **Toolset pins move into the wheel** (`release_core/toolset.py`); the pre-wheel
  timing worry dissolves because provisioning runs POST-pull. Dual-source the pins
  (shell + Python, tested in-sync) only through the migration window.
- **Fail-loud (F)** = halt on the load-bearing pull AFTER transient-retry, not on the
  first network blip.

## Removal inventory (all deletions happen in Phase 3)

- `templates/commons/bin/setup-dev-env.sh` (retired-files sweep)
- `bin-internal/provision-gate-toolset.sh`
- `templates/commons/bin/gate-tool-versions.sh`
- `install-release-core`: `resolve_url`/`_resolve_url_list`, asset-count check,
  `derive_caller_major` (+ fallback), `release-source.tag` stamp, `--user`/`--break`
- `sync.py`: `claude_desired()`, `_strip_managed_block()`, `decide_claude()` splice
- `arm-gate`: the action_ref-derivation fallback
- `bin/setup-dev-env.sh` entry from `BOOTSTRAP_REAL_FILES` + `consumer-contract.yaml`

## Workstreams (merge into one epic branch)

- **WS1 — A:** version-stamp the wheel from the tag (+ fix 4 static sites & fixtures).
- **WS2 — B:** Pages pip index (separate branch/concurrency; prerelease filter). [needs WS1]
- **WS3 — C+D:** `.release.major.txt` + `init` seeder + gut `install-release-core`
  (keep `--from-source` + transitional fallback). [needs WS1, WS2]
- **WS4 — H:** CLAUDE.md `@import` + managed `.claude/IMPORTANT-RELEASE.md`; remove
  splice; never-stage-CLAUDE.md rule. [ships with WS3 in one tag]
- **WS5 — E+I:** dissolve `setup-dev-env.sh` provisioning into `init` + unify
  gate-toolset into `release-core gate --provision`; pins -> `toolset.py`; `--cloud`
  arm; `post-setup-hook` final step. Does NOT remove `setup-dev-env.sh` yet. [needs WS3]
- **WS6 — F:** fail-loud boot with transient-retry. [needs WS3]
- **WS7 — G:** parametrize workflows; `arm-gate` reads the file with action_ref
  fallback. [Phase 2; needs C fleet-deployed]
- **WS8 — subtractive cleanup:** Phase-3 removals, gated on `repos verify` confirming
  fleet convergence. [needs all above + fleet pull]
