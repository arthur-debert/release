# `release_core` interface contract

This is the **binding spec** for the shell→Python migration
([proposal](./shell-to-python.md)). Every migration PR into
`feat/shell-to-python` is reviewed against it. Subagents implement *to* this
contract; the orchestrator (gatekeeper) verifies fit at merge.

Changing a signature here is an orchestrator-level decision — a subagent that
needs a change does NOT invent it; it flags the gap in its PR description and
the contract is amended first.

## Package layout

A new package in the existing uv workspace (sibling to `release_gh`):

```
templates/commons/lib/release_core/
  pyproject.toml            # name="release-core", hatchling, requires-python>=3.11, dependencies=[]  (stdlib-only, Phase 0–1)
  release_core/
    __init__.py             # version + curated re-exports
    gh.py                   # gh/git subprocess chokepoint, returns parsed JSON
    proc.py                 # generic subprocess runner
    cli.py                  # shared argparse harness + uniform exit codes
    yamlio.py               # YAML read via `yq` shell-out (Phase 0–1; swappable later)
    version.py              # semver parse/compare/bump (replaces vendored bash semver-tool)
    manifest.py             # Kind detection + manifest/config (.release-sync.yaml) parsing
  tests/                    # pytest, fixture-driven, no network
```

Domain verbs (Bucket A/B) live under `release_core/verbs/<name>.py` for now (a
later phase may split into a `release_cli` package; not now). Each verb module
exposes `def main(argv: list[str]) -> int`.

> **Stdlib-only is enforced for Phase 0–1.** `pyproject.toml` `dependencies`
> stays `[]`. YAML is the one thing stdlib can't do — it goes through
> `yamlio.py` shelling to `yq` (already a required external CLI). Do NOT add
> PyYAML/requests/click. The dep decision is deferred (proposal §dependency
> frontier).

## `proc.py` — subprocess

```python
class ProcError(RuntimeError):
    cmd: list[str]; returncode: int; stderr: str

def run(cmd: list[str], *, cwd: str | os.PathLike | None = None,
        env: dict[str, str] | None = None, input: str | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run cmd (no shell). text=True, capture_output=True. On nonzero with
    check=True raise ProcError. env, when given, is MERGED over os.environ."""

def out(cmd: list[str], **kw) -> str:
    """run(...).stdout.strip(). Convenience for the common 'capture one value'."""
```

Rules: never `shell=True`; never interpolate into a shell string. Args are
lists. This module is the only place `subprocess` is imported outside `gh.py`.

## `gh.py` — GitHub/git boundary (the single chokepoint)

Mirrors `release_gh/ghapi.py` conventions (consolidate later; duplication
tolerated in Phase 0).

```python
class GhError(RuntimeError): ...

def rest(path: str, *, method: str | None = None,
         fields: dict[str, str] | None = None, paginate: bool = False) -> object:
    """`gh api` → parsed JSON (or None on empty). Raises GhError on failure."""

def graphql(query: str, **variables: object) -> dict:
    """`gh api graphql`; checks payload['errors']; returns data dict."""

def git(args: list[str], *, cwd=None, check: bool = True) -> str:
    """git porcelain via proc.out. e.g. git(['rev-parse','--show-toplevel'])."""

def repo_root(start: str | None = None) -> str:
    """git rev-parse --show-toplevel, resolved real path."""
```

All JSON shaping happens in Python on the returned objects — **no `jq`** in any
migrated script.

## `cli.py` — shared CLI harness

Collapses the 23 hand-rolled arg loops. A verb declares its options
declaratively; the harness provides `--help` (from the module docstring),
uniform error exit `64` on bad usage, and `--json` handling.

```python
EXIT_OK = 0; EXIT_USAGE = 64

@dataclass
class Opt:
    name: str                      # "--repo"
    takes_value: bool = False
    default: object = None
    help: str = ""

def parse(argv: list[str], opts: list[Opt], *, positionals: tuple[int, int] = (0, 0),
          doc: str = "") -> tuple[dict, list[str]]:
    """Return (values_by_long_name_without_dashes, positionals).
    -h/--help prints `doc` and raises SystemExit(0).
    Unknown option or wrong positional arity prints to stderr, SystemExit(64)."""
```

Verb modules call `parse()` at the top of `main()`. Help text is the module
docstring (single source — no separate `show_help()`).

## `version.py` — semver

**Implemented in Phase 0 (#379) — import and reuse; do not re-derive.** Public
surface:

```python
@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int
    _sort_key: tuple = field(compare=True, ...)   # ordering key (see below)
    prerelease: tuple = field(compare=False, ...)  # public identifiers, e.g. ('rc', 1)

def parse(s: str) -> SemVer: ...          # accepts optional leading 'v'
def bump(v: SemVer, part: str) -> SemVer: # part in {major,minor,patch}; strips prerelease
# str(v) renders canonically; pass a prefix at the call site if you need 'vX.Y.Z'.
```

> **Amended from the original naive spec** (`order=True` over a raw `prerelease`
> tuple): that sorts wrong (empty tuple sorts *below* a populated one — reverse
> of semver.org §11) and `TypeError`s on mixed int/str identifiers. Ordering is
> therefore driven by a derived `compare=True` `_sort_key` while the public
> `prerelease` field is `compare=False`. Release outranks its prereleases;
> numeric identifiers rank below alphanumeric. The module docstring documents it.

Replaces `bin/share/semver-tool/`. That vendored tree is removed only once no
script references it (NOT in Phase 0).

## Phase 0 conventions (locked in by #379 — Phase 1 follows these)

- **No `tests/fixtures/` for `release_core`.** Tests live in
  `templates/commons/lib/release_core/tests/`, named **`test_core_*`** (avoids a
  pytest rootdir import collision with `tests/python/test_cli.py`; package has no
  `__init__.py`, matching `release_gh`). BATS fixtures are inline per the repo's
  existing convention (see `tests/detect-kind/detect-kind.bats`).
- **Verb registration** lives in `release_core/verbs/__init__.py` — keep it
  additive (each verb self-registers) to avoid merge contention across parallel
  Phase 1 PRs.
- **`pyproject.toml` (workspace root)** already wires `release_core` as a uv
  workspace member with `testpaths`/`pythonpath`; new verb tests are picked up
  automatically.

## `manifest.py` — Kind detection + config

```python
def detect_kind(root: str | None = None) -> str:
    """Filesystem-signal Kind detection. MUST match bin/detect-kind output
    byte-for-byte (it is consumed by other scripts + BATS)."""

def load_sync_config(root: str | None = None) -> dict:
    """Parse .release-sync.yaml (via yamlio) → {'capabilities': [...], ...}. {} if absent."""

def kind_manifest(kind: str, release_home: str) -> dict:
    """Load templates/<kind>/manifest.yaml."""
```

## `yamlio.py` — YAML (stdlib gap)

```python
def load(path: str) -> object:   # via proc: `yq -o=json '.' path` then json.loads
def loads(text: str) -> object:
```

Single seam: if/when we adopt PyYAML, only this module changes.

## The shim (entry-point pattern)

Every migrated script becomes a ≤18-line shim that puts `release_core` on
`sys.path` and dispatches. There are **two placement variants**, decided by
whether the script is distributed to consumers — determined by whether its
current `bin/` entry is a symlink into `templates/commons/bin/` (distributed) or
a real file in `bin/` (release-only). Check before migrating:

**(a) Distributed** — real source at `templates/commons/bin/<name>`, symlink
`bin/<name> -> ../templates/commons/bin/<name>`. `realpath` lands in
`templates/commons/bin/`, so the package is at `../lib/release_core`. This is
identical to `bin/gh-task-status`:

```python
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "lib", "release_core"))
from release_core.verbs import <name_module>  # noqa: E402
if __name__ == "__main__":
    sys.exit(<name_module>.main(sys.argv[1:]))
```

**(b) Release-only** — real file at `bin/<name>` (no `templates/commons`
indirection; not synced to consumers, e.g. `detect-kind`). `realpath` lands in
`bin/`, so the package is at `../templates/commons/lib/release_core`. Same shim,
only the relative path differs:

```python
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "templates", "commons", "lib", "release_core"))
```

**Distribution glue:** so that variant (a) shims resolve `../lib/release_core`
inside a consumer's `.release/`, `release-sync` must materialize
`templates/commons/lib/release_core/` into `.release/lib/release_core/` exactly
as it does `release_gh` — add `lib/release_core/*` to `is_release_internal`
(materialized, not symlinked into the working tree). **The Phase 0 PR makes this
`release-sync` change** even though the canary (`detect-kind`) is variant (b),
so Phase 1's first distributed verb is unblocked. Verify with `release-sync
--dry-run` against a fixture/consumer.

## Per-PR requirements (gatekeeper checklist)

A migration PR is mergeable into `feat/shell-to-python` only when ALL hold:

1. **Targets `feat/shell-to-python`** (`gh pr create --base feat/shell-to-python`), **not draft**.
2. **CLI contract preserved byte-for-byte** for any script consumed by others
   (`detect-kind`, `changelog`, anything in a lefthook glob): same stdout, same
   exit codes, same flags. Proven by a BATS test that runs the shim.
3. **pytest** unit tests for the new module, fixture-driven, **no network/gh** —
   mock at the data layer (load recorded JSON), never at subprocess.
4. **No `jq`, no `set -euo pipefail` boilerplate, no hand-rolled arg loop** in the
   replaced script — it's a shim. Logic is in `release_core`.
5. **Imports only `release_core` + stdlib.** No new third-party dep.
6. `lefthook run pre-commit --all-files` passes (lint/format gate).
7. **One script (or one tight cluster) per PR.** Small, reviewable, reversible.
8. PR body states: which bash script(s) removed, LOC before→after, and any
   contract-spec gap encountered (do not silently deviate).

## Sequencing / glue

- Phase 0 (this PR set) lands `release_core` + `detect-kind` canary + the
  `release-sync` materialization change. **Everything else depends on it.**
- Bucket A PRs branch off the updated `feat/shell-to-python` and may run in
  parallel; they touch disjoint files (own verb module + own shim).
- **Shared-file contention = `gh.py` and `cli.py` only.** `verbs/__init__.py` is
  NOT shared (verbs self-register; shims import their module directly — proven in
  #379/#380/#381). If a verb needs a new `gh.py` helper (e.g. #381 added
  `issue_list` for `gh issue list` porcelain) or a new `cli.py` option type, that
  is **additive and must be flagged in the PR body**; the orchestrator does not
  run two `gh.py`-extending PRs in the same parallel batch (serialize / rebase to
  avoid the one conflict point). Prefer the existing `gh.rest`/`graphql`/`git`/
  `issue_list` before adding a helper.
