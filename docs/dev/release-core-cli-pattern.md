# release-core CLI — the pattern (how to fill a group)

This is the spec for the `release-core <group> <command>` click tree
(epic #461, reorg #460, cutover #468). It is what per-group work follows so
additions land cleanly and identically. Read it before touching `cli/`.

## The wrapping rule: wrap, never rewrite

Each `release-core <group> <command>` leaf wraps an existing verb or `bin/`
script — it does **not** reimplement it. When filling or extending a group:

- wrap the underlying verb/script (see the two patterns below); never re-parse
  its arguments or duplicate its behavior in click;
- keep the wrapped verb's argument parsing and behavior byte-identical.

The cutover (#468) retired the flat maintainer command names from PATH — they
are gone, not aliased; the tree is now the only surface. Existing `test_core_*`
stay green; wrapped command behavior stays byte-identical.

## Layout — one module per top-level group (no shared edit point)

```text
release_core/
  cli_entry.py            # the THIN ASSEMBLER: builds the root click.Group,
                          # attaches each group. main(argv)->int lives here.
  cli/
    __init__.py           # (1) the legacy Opt/parse harness (EXIT_OK/EXIT_USAGE)
                          #     used by verbs; (2) this package's docstring map.
    _helpers.py           # wrap_verb / wrap_script / run_root — THE patterns.
    toplevel.py           # per-project flat verbs + small per-project groups,
                          # attached to the root by attach(root).
    pr.py                 # `pr` group (EXEMPLAR: nested subgroup + both wraps).
    ci.py                 # `ci` group (fetch-deps / fetch-artifact wraps).
    admin/                # `admin` subpackage — large, so split one module per
      __init__.py         #   nested group (the admin assembler).
      repos.py            #   admin repos
      release_cmds.py     #   admin release   (named *_cmds to not shadow the pkg)
      policy.py           #   admin policy
      secrets.py          #   admin secrets
      inbox.py            #   admin inbox
```

**The invariant:** adding or filling a group touches **only that group's
module**. `cli_entry.py` and `_helpers.py` are stable — you should not need to
edit them to fill a group. A group module's only contract is to **define a
`click.Group` and export it as `group`** (and, for `toplevel.py`, the per-project
commands are attached via `attach(root)`). The assembler imports `group` and
attaches it.

## The two wrapping patterns (use these — do not roll your own)

Both live in `cli/_helpers.py`. Both produce a **passthrough** leaf: click does
NOT parse the leaf's args (so `--json 91` reaches the underlying tool verbatim,
including `--help`).

### `wrap_verb(verb_main, *, name, short_help) -> click.Command`

For commands backed by a `release_core.verbs.<verb>.main(argv) -> int` (or any
`main(argv) -> int`, e.g. `prstate.cli.task_status.main`). Forwards argv
verbatim and returns the verb's exit code. The full `--help` is the verb's own
docstring/USAGE — `--help` is forwarded straight through, never intercepted by
click. Behavior is byte-identical to invoking the verb directly.

```python
from .._helpers import wrap_verb
from ...verbs import managed_repos

group.add_command(wrap_verb(
    managed_repos.main,
    name="list",
    short_help="List the managed fleet repos.",
))
```

### `wrap_script(script, *, name, short_help) -> click.Command`

For commands backed by a standalone `bin/<script>` (bash/python tools:
`gh-pr-resolve-thread`, `fetch-deps`, `fetch-artifact`). Execs the script **by name off `$PATH`** (these are on `$PATH`
in every environment that has them — dodot locally, `action_path` in CI; we
never compute a path relative to the installed wheel), forwards args verbatim,
and propagates the child's exit code. A missing tool is a clear exit 127.

```python
from ._helpers import wrap_script

group.add_command(wrap_script(
    "gh-pr-resolve-thread",
    name="resolve-thread",
    short_help="Resolve a PR review thread.",
))
```

### Choosing between them — the home test

- It's a Python verb under `release_core.verbs.` (or `prstate`)? → `wrap_verb`.
- It's a standalone script in `bin/` (bash, or a python tool NOT exposed as a
  verb `main`)? → `wrap_script`.

## Help conventions (a first-class requirement of #460)

- **Every leaf** gets a one-line `short_help`. That is the text shown in the
  **parent group's** `--help` listing. Keep it terse and imperative
  ("Block until …", "List the …"). Discoverability is the whole point of the
  reorg — a missing or vague `short_help` defeats it. (A test enforces that
  every registered leaf has a non-empty `short_help`.)
- **Group callbacks** get a docstring: the first line is the group's
  `short_help`; the body is the group's full `--help`.
- **Full leaf `--help`** is delegated to the underlying tool (the verb's
  docstring for `wrap_verb`, the script's own `--help` for `wrap_script`).
  Never re-state the tool's help in click — show the authoritative one.

## Stubs

Unimplemented commands are registered as **stubs** so `release-core --help`
shows the whole intended shape. A stub never silently succeeds — it always exits
`STUB_EXIT` (`69`, EX_UNAVAILABLE) with a clear "registered stub (not yet wired —
see #460)" message — so it can't be mistaken for a working command.

- **Stub leaf** — `toplevel._stub_command(name, short_help)`. Accepts any
  args/flags (so `release-core <stub> --json` still hits the 69 path, not a
  click usage error); `--help` stays reachable. Implement it by replacing the
  `_stub_command(...)` registration with the real `wrap_verb(...)` /
  `wrap_script(...)` — that is the entire change.
- **Stub group** (an empty group, or one whose bare form isn't wired yet) —
  `_helpers.stub_group(name, *, short_help, help=...)`. Bare invocation
  (`release-core <group>` with no subcommand) prints the help **plus** a stub
  note and exits `69`, instead of click's default of exiting `0` silently. Fill
  it by `add_command`-ing real leaves; when the bare form should itself do
  something (e.g. `admin inbox` → release-inbox), swap `stub_group` for a normal
  `@click.group(invoke_without_command=True)` with the real callback.

> A group that already has subcommands (even all-stub leaves) and uses a plain
> `@click.group` will, when invoked bare, hit click's "Missing command" usage
> error (exit `2`) — also never a silent `0`. Use `stub_group` for the empty
> groups and the not-yet-wired-bare-form groups specifically.

## The built-out tree

The tree is fully built out — every group and leaf below is implemented and on
PATH:

- **Top-level (per-project):** `cut`, `status`, `init`, `selfcheck`,
  `changelog`, `semver`, `sync`, `detect-kind`, `audit`, `issue`.
- **`pr`** (whole group: `pr review request|cancel|show`, `pr wait`, `pr ready`,
  `pr resolve-thread`, `pr status`) — the nested-subgroup + both-wraps exemplar.
- **`ci`** — `fetch-deps` / `fetch-artifact` wraps.
- **`admin/*`** — `admin repos` (`list / prs / scripts / audit / verify`),
  `admin release` (`advance-major / betas / lex`), `admin policy`
  (`ruleset / sweep / dependabot`), `admin secrets` (`install / token`),
  `admin inbox` (bare triage + `notify-source`), `admin smoke-test`.

New leaves follow the same wrap-an-existing-verb/script pattern; the stub
mechanism below stays available for registering an intended-but-unwired shape.

## Reaching the CLI two ways

- Local checkout: `bin/release-core` (a thin sys.path shim → `cli_entry.main`).
- Installed wheel: the `release-core` console-script (`[project.scripts]` →
  `release_core.cli_entry:main`). Same `main`, same tree.

## Running the checks

```sh
.venv/bin/python -m pytest templates/commons/lib/release_core/tests/   # unit
lefthook run pre-commit --all-files                                    # the gate
```
