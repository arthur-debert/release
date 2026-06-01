# Proposal: migrate release's shell surface to Python

**Status:** in progress — feature branch `feat/shell-to-python`
**Date:** 2026-05-31
**North star:** future development speed / maintainability / extensibility.

## Why

Release's own code is ~7,300 LOC of bash (`bin/` 6,201 + `bin-internal/`
1,097), plus ~2,140 LOC of embedded bash in composite actions. An evidence
pass (see "Evidence" below) found **~27–34% of that surface (~2,000–2,500
LOC) is accidental shell complexity** — not domain logic:

- **Arg-parse + help boilerplate: ~1,145 LOC (15%).** 23 scripts hand-roll the
  _identical_ `while [ $# ]` / `case "$1"` / `show_help()` machine. Shell has no
  import, so reuse is copy-paste.
- **JSON/YAML gymnastics.** 19 scripts use `jq`, 6 `yq`, 12 `gh api`. Examples:
  `fetch-deps` unpacks a 4-field JSON object via `jq | join($'\x1f') | IFS= read`;
  `apply-ruleset` does `yq -o json | jq` to normalize a string-or-array-or-object
  field; `audit-repo` fetches `dependabot.yml` as base64 and parses it with
  `grep|sed|tr|sort`. Each is 1–4 lines in Python with `json`/a YAML loader.
- **Shell-safety defenses.** 93 `2>/dev/null`, 32 `|| true`, 43 `IFS=` resets,
  6 `shellcheck disable`. PR review burns disproportionately on these edge cases
  rather than logic.

## The pattern already exists in-repo

This is **not greenfield**. Two production Python packages already run in all
three target environments (local / GitHub Actions / Claude Cloud):

- `templates/commons/lib/release_gh/` — the PR state machine. Stdlib-only,
  hatchling-built, **single `gh`/`git` chokepoint** (`ghapi.py`), pure
  `evaluate(context) → TaskStatus` core, pytest fixtures + BATS contract tests.
- `orchestrator/` (`orc`) — argparse-subcommand CLI; deps on the Agent SDK +
  `release_gh` via uv workspace.

And the exact migration mechanic is **already live**: `bin/gh-task-status` is an
18-line Python shim that puts `release_gh` on `sys.path` and calls
`task_status.main()`. No `pip install` — `release-sync` materializes the package
into `.release/lib/`, the shim reaches into it. We are scaling a proven pattern,
not inventing one.

## Classification of the surface

| Bucket                            | What                                                                           | LOC (approx)        | Action                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------ | ------------------- | ------------------------------------------------------------------------------- |
| **A — pure logic / data-munging** | JSON/YAML/gh-API orchestration, clustering, ref/version math, templating       | ~3,100 / 23 scripts | **Migrate first** — strong win (~40–50% LOC drop, big testability gain)         |
| **B — filesystem/git plumbing**   | symlink materialize (`release-sync`), git porcelain, drift/verify              | ~2,800 / 12 scripts | **Migrate after A** — modest LOC win, large testability win                     |
| **C — GH-Actions-native glue**    | `build-tauri`, `compute-tauri-matrix`, `setup-*-signing-env`, `install-*-deps` | ~600 / 10 scripts   | **Leave in bash** — welded to `GITHUB_OUTPUT`/`GITHUB_ENV`, Python buys nothing |

Highest-leverage single targets in Bucket A: `fetch-deps` (775) + `fetch-artifact`
(271), `audit-repo` (468), `done-check` (421), `release-lex` (368),
`release-cut` (264), the `changelog` family, the fleet tools
(`managed-repos`/`inbox`/`notify-source`/`list-repo-*`).

## Architecture

A new stdlib-only package **`release_core`** in the uv workspace holds the shared
primitives; domain verbs import it. Entry points stay as thin per-name shims on
`$PATH` (the proven `gh-task-status` pattern) so the lefthook / symlink /
consumer contracts (`bin/release`, `bin/check-shell`, `detect-kind`, …) do not
break.

| `release_core` module | replaces                                | kills                                                   |
| --------------------- | --------------------------------------- | ------------------------------------------------------- |
| `gh.py`               | scattered `gh api` + `jq`               | JSON munging — `rest()`/`graphql()` return parsed dicts |
| `proc.py`             | inline `subprocess`/git porcelain       | trap / `set -e` / `\|\| true` defenses                  |
| `cli.py`              | 23 hand-rolled arg loops                | ~1,145 LOC boilerplate → ~150                           |
| `yamlio.py`           | `yq` / `yq \| jq` pipelines             | YAML→JSON→jq gymnastics                                 |
| `version.py`          | the vendored bash `semver-tool`         | a vendored dependency, gone                             |
| `manifest.py`         | `detect-kind` + manifest/config parsing | duplicated heuristics                                   |

The exact signatures are pinned in
[`shell-to-python-core-contract.md`](./shell-to-python-core-contract.md) — the
contract every migration PR is reviewed against.

## The dependency frontier (deferred, deliberately)

`release_gh` is stdlib-only because it runs with no install step (the `sys.path`
shim). **The no-dependency rule buys exactly one thing: zero-install
runnability.** Its cost: no stdlib YAML parser (the biggest residual shell tax),
no semver, no nice HTTP/CLI libs.

This is the _same_ decision as "ship release as a pip-installed package via GH
releases": if there is an install step, dependencies are free and the no-dep
rule stops paying for itself; if there is no install step, the rule is
load-bearing. The two coherent positions:

1. **Zero-install shim + stdlib-only** (extend `release_gh` as-is). YAML via
   shelling to `yq` (already a required external CLI) or a vendored mini-parser.
   Graceful degradation preserved (`.release/` is committed).
2. **Installed package + curated deps** (PyYAML, `packaging`, maybe `httpx`).
   YAML/semver/HTTP pain fully vanishes; takes on install-step + distribution +
   degradation concerns.

The trap is doing _both_ (pay the install cost, still ban deps).
**Decision deferred to after Phase 1** — how the YAML-via-`yq` boundary actually
feels during Bucket A is the empirical signal. Phases 0–1 stay stdlib-only and
ride the existing zero-install shim, so they require **zero distribution change**
and cannot dead-end.

## Plan (feature branch + PR-per-unit)

Driven on `feat/shell-to-python`; each work unit is a **live PR into the feature
branch**, implemented by a subagent, reviewed/merged by the orchestrator (the
gatekeeper who owns the contract and the integration, not the implementations).

- **Phase 0 — shared core + canary.** Build `release_core` (stdlib-only,
  hatchling, in the workspace) per the contract, and migrate **one** small
  Bucket-A script end-to-end (`detect-kind`) as proof the shim→package→core path
  works with a preserved CLI contract + BATS test. Zero distribution change.
- **Phase 1 — Bucket A, file-by-file.** One PR per script (or tight cluster).
  Each: Python module + pytest + BATS contract test, then swap the bash for the
  shim. Reversible per file. ~3,100 bash → ~1,300 Python.
- **Phase 2 — Bucket B** (`release-sync` 666, `release-drift-check`,
  `release-verify-fleet`). Modest LOC win, large testability win.
- **Leave Bucket C in bash.**
- **Dep/distribution decision** (§ above) made after Phase 1, with data.

BATS stays as the e2e/contract layer (pytest for logic, BATS for the shim +
end-to-end) — exactly the split `release_gh` already uses.

## Evidence

Quantitative + qualitative pass over the surface (2026-05-31), recorded in the
"Why" and "Classification" sections above. Headline numbers: ~7,300 LOC bash;
~27–34% accidental complexity; 23 scripts with duplicated arg-parsing; 37 scripts
touching `jq`/`yq`/`gh api`. Two existing Python packages (~2,000 LOC) establish
the conventions and the 3-environment viability.
