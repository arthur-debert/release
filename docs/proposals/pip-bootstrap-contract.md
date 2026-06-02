# pip-bootstrap — interface contract (PoC)

> Scaffolding doc for the `feat/pip-bootstrap` effort. This is the **glue**:
> the frozen interfaces every sub-PR codes against. Implementation lives in the
> sub-PRs; this file is the contract between them. The narrative *why* lives in
> the superseding ADR (PR-A).

## The model (one sentence)

Boot everywhere = **`install-release-core [--major vN]`** — the resolver
`pip install`s the release_core wheel from the GitHub release (`--force-reinstall
--no-deps`; see §Transport) **and then runs `release-core init`** in the current
repo to materialize the per-repo committed bits. One command does the whole boot.
Tools become console-scripts on PATH (current by install).

## Transport (DECIDED)

**Wheel attached to each gh release.** The release pipeline builds
`release_core-<ver>-py3-none-any.whl` and uploads it as a release asset. Boot
resolves the asset URL and installs it.

Boot resolver = **`bin/install-release-core`** (the ONE definition; both contexts
call it). Boot everywhere is one command:

```bash
install-release-core [--major vN]   # resolve + pip install the wheel, then run
                                    # `release-core init` (--no-init to skip)
```

`init` is folded in (NOT a pip post-install hook — wheels have none, and init is
repo-specific while pip is environment-level): the resolver runs it explicitly at
the repo root on the just-installed console-script (located across venv/`--user`/
system layouts), best-effort so its failure never fails the resolver.

**Install model = "latest, force-reinstall" (LOCKED):**

- Source: the repo's `releases/latest` by default; `--major vN` instead pins to
  the **latest release in that major line** carrying the wheel. The major-line
  filter is load-bearing **before any `v3` is cut**: the wheel version is a static
  `0.0.1` (not stamped from the tag), so `releases/latest` would hand a `v2`-pinned
  consumer a future `v3` wheel. `--major v2` keeps `@vN` honest.
- Install is **`pip install --force-reinstall --no-deps "$url"`**, NOT `-U`.
  Because the wheel version is static, `pip install -U` sees `0.0.1` already
  satisfied and SKIPS it, defeating the pull model. `--force-reinstall` always
  reinstalls; `--no-deps` because release_core is dependency-light (stdlib-only)
  and a boot must never mutate the environment's other packages.
- **Exactly one** wheel asset must match per release; zero or many is a
  release-side packaging bug, surfaced loudly (exit 1), never silently installed.

Resolution, the install model, and the folded-in init are covered by
`tests/install-release-core/` (offline: stub `gh` applies the real `--jq` filter;
stub `python3` records the pip args; stub `release-core` records the init call).
`install-release-core` is wired into `setup-dev-env.sh` (SessionStart); wiring it
into the reusable CI workflows is the next step.

## What this PoC does NOT do

- Does **not** rip out `release-sync` / `.release/` materialization fleet-wide.
- Does **not** touch consumer repos (phos especially excluded).
- Does **not** remove the `fetch-deps` curl bootstrap from consumer workflows.
- Proves the mechanics **self-contained in release's own CI** (PR-E smoke job).
  Real consumer rollout + curl/sync retirement are post-merge follow-ups.

## Frozen interfaces

### 1. Console-scripts entry convention (PR-B owns)

Verbs keep their `main(argv: list[str]) -> int` signature **unchanged**
(`changelog` keeps `orchestrator_main`). Do NOT edit verb modules to add
zero-arg mains.

Instead add **`release_core/entrypoints.py`** — one zero-arg wrapper per tool:

```python
"""Console-script entry points. Each wrapper reads sys.argv and delegates to a
verb's main(argv) -> int. Keeps verbs free of console-script plumbing."""
import sys
from release_core.verbs import changelog, detect_kind, managed_repos  # etc.

def changelog_main() -> None:
    raise SystemExit(changelog.orchestrator_main(sys.argv[1:]))

def detect_kind_main() -> None:
    raise SystemExit(detect_kind.main(sys.argv[1:]))
# ...one per tool
```

`pyproject.toml` `[project.scripts]` maps the **on-PATH command name** (hyphenated,
matching today's `bin/` names) to the wrapper:

```toml
[project.scripts]
changelog       = "release_core.entrypoints:changelog_main"
detect-kind     = "release_core.entrypoints:detect_kind_main"
release-cut     = "release_core.entrypoints:release_cut_main"
# ... full table = every verb that has a bin/ shim today
release-core    = "release_core.cli_entry:main"   # the top-level CLI, see §2
```

The command-name set = exactly the tools that have a `bin/` shim today (symlinks
into `templates/commons/bin/` **and** the variant-b real files in `bin/`).
`fetch-deps` and `fetch-artifact` are **NOT** in this table yet — they are still
bash (Bucket-C); folding them in is a follow-up (see §5).

**Invariant:** for every tool, `<name> --help` via the installed console-script
must produce byte-identical output to the current `bin/<name> --help`. The
existing `tests/test_core_*` suite must stay green.

### 2. `release-core` top-level CLI + `init` (PR-C owns)

New console command **`release-core`** (the package's own CLI), entry
`release_core.cli_entry:main`, dispatching subcommands. PoC subcommands:

- `release-core init` — materialize per-repo committed bits. **Idempotent.**
- `release-core --help` / `release-core` (no args) — usage.

`init` scope for the PoC (minimal, additive, idempotent — materialize only what a
consumer must have committed and cannot get from the installed package):

- Wire the pre-commit hook the same way `setup-dev-env.sh` §0 does **is NOT in
  scope** (that stays in the hook). `init` is about *files that must live in the
  repo*, not git-hook wiring.
- PoC `init` materializes nothing destructive: it writes `lefthook.yml` and the
  managed lint configs **only if absent** (never overwrite consumer edits in the
  PoC), and prints what it did / would do. A `--force` flag may overwrite; default
  is create-if-absent. Exact file list = whatever `release-sync` currently
  materializes that is *config*, not *package code* — PR-C reads `sync.py` /
  `release_sync.py` to enumerate, and documents the list it chose in the PR body.
- `init` must exit non-zero on any real failure (cannot write a file it intended
  to write). No silent best-effort swallowing for the PoC's own writes.

Keep `init` deliberately small. It is the *seam*, not the whole materializer; the
full sync→init migration is post-PoC.

### 3. Wheel publish job (PR-D owns)

Add a job to the release pipeline (`.github/workflows/gh-action.yml` is the
shared release workflow; the wheel build belongs wherever the gh release is
created — PR-D decides the cleanest seam and documents it) that, on a release:

```bash
python -m build --wheel templates/commons/lib/release_core
gh release upload "<tag>" templates/commons/lib/release_core/dist/release_core-*.whl
```

- Build with `python -m build` (add `build` to a CI-only group; the package
  runtime stays dependency-light — build tooling is CI-only, not a runtime dep).
- Asset name must match the resolver regex `^release_core-.*\.whl$`.
- The job must fail the release if the wheel build or upload fails.
- Does not need PR-B merged to *run*, but its smoke assertion (wheel contains the
  console-scripts) does — PR-D rebases on PR-B before final green.

### 4. End-to-end smoke proof (PR-E owns)

New workflow `.github/workflows/pip-bootstrap-smoke.yml` (or a job in `ci.yml`)
that proves the whole chain **in release's own repo**, no consumer:

1. `python -m build --wheel` the package (same as PR-D).
2. `pip install` the built wheel into a fresh venv.
3. Assert console-scripts are on PATH: `changelog --help`, `detect-kind --help`,
   `release-cut --help` exit 0 with expected output.
4. `release-core init` in a throwaway fixture repo → idempotent (run twice, second
   run is a clean no-op), exits 0, materializes the documented file set.
5. Re-run `release-core init` → asserts idempotency (no diff / exit 0).

This is the **PoC's definition of done**: green smoke = the model works.

### 5. Explicitly deferred (follow-ups, NOT this PoC)

- `fetch-deps` / `fetch-artifact` → console-scripts (the "if deps fail, return
  non-zero" hard requirement rides with that migration).
- Boot wiring of the resolver into `setup-dev-env.sh` (SessionStart) and into the
  reusable CI workflows.
- Retiring `release-sync` package materialization + the curl bootstrap fleet-wide.
- Major-line-aware wheel resolution (pin to the consumer's `vN`).

## Decomposition / merge DAG

```
PR-A (ADR + spec, docs only) ─┐
                              ├─ independent
PR-B (entrypoints+scripts) ───┴─→ PR-C (release-core init)
                                └─→ PR-D (wheel publish job)
                                          PR-C, PR-D ──→ PR-E (smoke proof)
```

All PRs target `feat/pip-bootstrap`, LIVE (not draft), full `gh-pr-review-loop`.
Gatekeeper merges each into the feature branch; the user merges
`feat/pip-bootstrap` → main.
