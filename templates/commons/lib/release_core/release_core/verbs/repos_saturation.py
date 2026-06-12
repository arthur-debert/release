"""repos saturation — measure #569's per-consumer sunset conditions, mechanically.

Release's source carries deliberately scoped *transitional* machinery (issue
#569): pre-adoption fallbacks, the one-shot pull-model seeder, tolerated no-op
flags, pre-WS4/WS7 migration tolerance, husky residue unsetting, and the
fragment-model legacy-rejection path. Each item is load-bearing *until its named
condition is verified across the whole fleet* — and until this verb, nothing
could MEASURE that. Prose conditions + no meter = #569 could never close
honestly (the exact knowledge-in-prose trap #595 closed for fresh-event
verification).

This verb is the meter. It rides the SAME hermetic, refreshed clones the verify
sweep uses (`release-core admin repos list --clone` into the per-user /tmp root,
#624) and evaluates ONE condition table — each row a #569 sunset item plus a
mechanical predicate run per consumer clone — into a per-item × per-consumer
matrix. An item whose column is GREEN across every consumer it applies to is
reported **REMOVABLE — see #569 item N**: the sunset is closeable on evidence,
not assertion.

The condition table (CONDITIONS, below) is the ONE definition. Adding a future
sunset surface is ONE new row; #569's prose points AT this verb, never the
reverse.

It is INFORMATIONAL: saturation is a ratchet that closes #569 items, it does NOT
fail CI. The exit code names whether the run itself could execute, never whether
the fleet is saturated.

Usage:
  release-core admin repos saturation
      [--root DIR]   hermetic clone root (default /tmp/release-fleet-verify-$USER,
                     shared with the verify sweep so one refresh serves both).
      [--only LIST]  restrict to a comma-separated subset of owner/name.
      [--no-clone]   skip the clone/refresh phase and measure the clones already
                     under --root as-is (for a fast re-read after a verify sweep).
      [--json]       machine-readable matrix (items × consumers + verdicts).

Exit codes:
  0  — the meter ran (saturated or not — this is informational)
  2  — setup/dependency error (missing tool, unreadable manifest, clone failed
       so there is nothing to measure)
  64 — bad usage
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

from .. import proc

# Re-invoke THIS package's CLI in a subprocess (same interpreter/env/code) — the
# verify sweep's lesson (release#534): a bare `release-core` PATH lookup resolves
# to the in-checkout shim on a dev box and dies on nested dep re-resolution.
_SELF_CLI = [sys.executable, "-m", "release_core"]

USAGE = __doc__ or ""

# Kinds whose managed task verbs (bin/build) are the #569 item-1 condition. A
# row gated on these kinds reports N/A for every other kind (a rust-cli has no
# bin/build to materialize, so it can never block item 1's removal).
TASK_VERB_KINDS = frozenset({"electron-app", "tauri-app", "vscode-ext"})

# The bootstrap quartet (sync.BOOTSTRAP_REAL_FILES) — the SessionStart chain
# written as REAL tracked copies, so the deployed bytes are directly comparable
# against the wheel's current copy under RELEASE_HOME/templates/commons/<dest>.
# Duplicated here as the saturation-side contract rather than imported, so a
# saturation predicate never drags in the whole sync engine; the test pins them
# equal to sync.BOOTSTRAP_REAL_FILES so they can't drift.
BOOTSTRAP_QUARTET = (
    ".claude/settings.json",
    "bin/install-release-core",
    "bin/setup-dev-env.sh",
    "bin/pr-loop-guard",
)

# The gate definition + lint configs WS3/WS4 moved into the ephemeral `.release/`
# (sync.GATE_INTERNAL_FILES, minus .shellcheckrc which a consumer never tracked
# at root): a consumer still tracking one at its repo root committed it
# pre-migration — the #569 item-4 red signal.
_LEGACY_ROOT_GATE_MIRRORS = frozenset(
    {
        "lefthook.yml",
        ".markdownlint.json",
        ".markdownlintignore",
        ".yamllint",
        ".prettierignore",
    }
)


# ── Per-consumer evaluation context ──────────────────────────────────────────


@dataclass(frozen=True)
class Consumer:
    """One fleet clone under evaluation: its owner/name, on-disk path, kind, and
    the tracked-file set (`git ls-files`, read once and shared by every
    predicate that asks about committed paths).

    ``tracked`` is ``None`` when `git ls-files` could NOT be introspected (not a
    repo / git failure) — DISTINCT from an empty frozenset, which means the repo
    was introspected and genuinely tracks nothing. Predicates over committed
    paths must read the ``None`` case as RED (indeterminate, never a false
    GREEN); only an actually-empty set is "introspected, nothing found".

    ``kind`` is ``'?'`` when `detect-kind` could not classify the clone; a
    kind-gated predicate reads that as RED too (applicability unknown), never
    N/A."""

    repo: str
    path: str
    kind: str
    tracked: frozenset[str] | None


@dataclass(frozen=True)
class Context:
    """Cross-consumer evaluation context: the release checkout the wheel's
    current bootstrap-quartet bytes are read from (for the resolver-vintage
    predicate)."""

    release_home: str


# A predicate verdict: GREEN (condition met — this consumer no longer blocks the
# sunset), RED (still blocks it), or NA (the condition does not apply to this
# consumer — e.g. a kind-gated row on the wrong kind). NA columns never block a
# REMOVABLE verdict.
#
# THE LOAD-BEARING INVARIANT (#629 review): a predicate that could NOT introspect
# its input (unknown kind, `git ls-files` unavailable, a `git config` that failed
# for any reason other than "key genuinely absent") returns RED, never GREEN and
# never NA. Indeterminate = RED. A false GREEN/REMOVABLE on a clone the meter
# couldn't actually read is the quiet-wrong "saturated" verdict this verb exists
# to kill — so every predicate fails toward visibility.
GREEN, RED, NA = "green", "red", "na"

Predicate = Callable[[Consumer, Context], "tuple[str, str]"]


# ── Predicates (each returns (verdict, detail)) ──────────────────────────────


def _p_task_verbs(c: Consumer, _ctx: Context) -> tuple[str, str]:
    """#569 item 1 — managed task verbs materialized: `bin/build` present +
    executable in the electron/tauri/vsce kinds (the fallback chains in
    electron-app.yml / tauri-app.yml can be removed once every such consumer
    carries it).

    Applicability hinges on the detected kind, so an UNKNOWN kind ('?', from a
    failed `detect-kind`) is RED, never N/A: we can't prove this consumer is
    out-of-scope, and letting item 1 go REMOVABLE on a kind guess is exactly the
    quiet-wrong saturation verdict. A KNOWN non-task kind is genuinely N/A."""
    if c.kind == "?":
        return RED, "kind unknown (detect-kind failed): applicability indeterminate"
    if c.kind not in TASK_VERB_KINDS:
        return NA, f"kind {c.kind}: no managed task verb"
    build = os.path.join(c.path, "bin", "build")
    if not os.path.isfile(build):
        return RED, "bin/build missing"
    if not os.access(build, os.X_OK):
        return RED, "bin/build not executable"
    return GREEN, "bin/build present + executable"


def _p_pull_seeded(c: Consumer, _ctx: Context) -> tuple[str, str]:
    """#569 item 2 — pull-model seeded: the consumer is past the one-shot seed,
    so `repos migrate` is dead code for it. A seeded consumer carries the
    bootstrap resolver as a REAL tracked file (`bin/install-release-core`) — a
    pre-pull consumer predates it. (The full vintage check is item 3; presence
    alone settles seeded-vs-not.)"""
    if c.tracked is None:
        return RED, "git ls-files failed: tracked set indeterminate"
    if "bin/install-release-core" in c.tracked:
        return GREEN, "bootstrap resolver tracked (seeded)"
    return RED, "no bin/install-release-core (pre-pull seed)"


def _p_resolver_vintage(c: Consumer, ctx: Context) -> tuple[str, str]:
    """#569 item 3 — deployed resolver vintage: the consumer's bootstrap-quartet
    bytes match the wheel's CURRENT copy, so its deployed resolver/setup is
    post-cutover (the tolerated no-op flags `init --commit/--force`,
    `install-release-core --user/--break-system-packages` can be removed once no
    consumer runs a stale resolver). Each quartet path must be TRACKED (a stray
    untracked file on disk is not a deployed managed copy) AND byte-match
    RELEASE_HOME/templates/commons/<dest>.

    Indeterminate = RED (#629 review): an unintrospectable tracked set
    (``tracked is None``) can't prove the quartet is deployed, so the vintage is
    indeterminate — never a false GREEN on a clone we couldn't read."""
    if c.tracked is None:
        return RED, "git ls-files failed: tracked set indeterminate"
    stale: list[str] = []
    for dest in BOOTSTRAP_QUARTET:
        deployed = os.path.join(c.path, dest)
        current = os.path.join(ctx.release_home, "templates", "commons", dest)
        if dest not in c.tracked:
            # Not a tracked managed copy — a stray/untracked file at the path
            # must NOT count as a deployed resolver. Vintage can't be GREEN.
            stale.append(f"{dest} (not tracked)")
            continue
        try:
            with open(deployed, "rb") as fh:
                dep_bytes = fh.read()
        except OSError:
            stale.append(f"{dest} (absent)")
            continue
        try:
            with open(current, "rb") as fh:
                cur_bytes = fh.read()
        except OSError:
            # Can't read the wheel-current copy → can't judge vintage. Treat as
            # RED (fail toward visibility, never a false GREEN).
            stale.append(f"{dest} (no current copy to compare)")
            continue
        if dep_bytes != cur_bytes:
            stale.append(f"{dest} (bytes differ)")
    if stale:
        return RED, "stale: " + ", ".join(stale)
    return GREEN, "quartet matches wheel-current bytes"


def _p_ws4_ws7_complete(c: Consumer, _ctx: Context) -> tuple[str, str]:
    """#569 item 4 — WS4/WS7 complete: neither the `.release/` build dir nor any
    managed mirror is tracked (`git ls-files`), so the one-time untrack
    migrations + the marker-substring / stale-managed-copy old-wording tolerance
    are dead for this consumer. A tracked `.release/**` path or a tracked
    managed-copy at a gate-internal dest (lefthook.yml, the lint configs) means
    the consumer predates WS4/WS7."""
    if c.tracked is None:
        return RED, "git ls-files failed: tracked set indeterminate"
    tracked_release = sorted(p for p in c.tracked if p == ".release" or p.startswith(".release/"))
    # The gate configs that WS3+WS4 moved into the ephemeral .release/ — a
    # consumer still tracking one at the root committed it pre-migration.
    legacy_root_mirrors = sorted(p for p in c.tracked if p in _LEGACY_ROOT_GATE_MIRRORS)
    bad = tracked_release + legacy_root_mirrors
    if bad:
        return RED, "tracked pre-WS4/WS7: " + ", ".join(bad)
    return GREEN, "no tracked .release/ or root gate mirrors"


def _p_hooks_path_unset(c: Consumer, _ctx: Context) -> tuple[str, str]:
    """#569 item 5 — husky residue gone: `core.hooksPath` is unset, so
    setup-dev-env.sh's unset-prior-hook-manager step is unneeded for this
    consumer (a husky/lefthook-managed `core.hooksPath` would shadow the
    canonical gate hook).

    `git config --get` exit codes are load-bearing, and the EXIT CODE — not the
    value — decides presence (#629 review):
      - exit 0 → the key IS present (husky residue) → RED, regardless of value.
        A set-but-empty `core.hooksPath` still exits 0; treating that as "unset"
        would contradict git's own presence semantics.
      - exit 1 → the key is genuinely ABSENT (the real unset) → GREEN.
      - any other exit (128 not-a-repo, unreadable config, …) → introspection
        failed → RED, never a false "unset" GREEN. We do NOT collapse
        all-nonzero to unset."""
    result = proc.run(
        ["git", "-C", c.path, "config", "--local", "--get", "core.hooksPath"],
        check=False,
    )
    if result.returncode == 0:
        # Key present (exit 0) — its mere presence is the husky residue, value
        # or not. Name the value when there is one for a legible report.
        value = (result.stdout or "").strip()
        return RED, f"core.hooksPath set ({value})" if value else "core.hooksPath set (empty)"
    if result.returncode == 1:
        return GREEN, "core.hooksPath unset"
    return RED, f"git config failed (exit {result.returncode}): core.hooksPath indeterminate"


def _p_fragment_model(c: Consumer, _ctx: Context) -> tuple[str, str]:
    """The unlisted #569 surface (2026-06-12 audit) — fragment-model adopted: a
    `CHANGELOG/` dir is present, so roll-changelog's legacy whole-file rejection
    path can be retired (the consumer files unreleased-*.md fragments, never a
    hand-edited CHANGELOG.md)."""
    if os.path.isdir(os.path.join(c.path, "CHANGELOG")):
        return GREEN, "CHANGELOG/ fragment dir present"
    return RED, "no CHANGELOG/ dir (pre-fragment model)"


# ── The ONE condition table ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Condition:
    """One #569 sunset item: a stable id, the #569 item number it measures, a
    short label for the matrix, and the mechanical predicate.

    Adding a future sunset surface is ONE new Condition row here — nothing else
    in this verb is per-item."""

    id: str
    item: str  # the #569 item identifier, e.g. "1" or "fragment"
    label: str
    predicate: Predicate


CONDITIONS: list[Condition] = [
    Condition("task-verbs", "1", "managed task verbs (bin/build)", _p_task_verbs),
    Condition("pull-seeded", "2", "pull-model seeded", _p_pull_seeded),
    Condition("resolver-vintage", "3", "resolver vintage (quartet bytes)", _p_resolver_vintage),
    Condition("ws4-ws7", "4", "WS4/WS7 untracked", _p_ws4_ws7_complete),
    Condition("hooks-path", "5", "core.hooksPath unset", _p_hooks_path_unset),
    Condition("fragment-model", "fragment", "fragment-model (CHANGELOG/)", _p_fragment_model),
]


# ── Evaluation ───────────────────────────────────────────────────────────────


def _tracked_files(path: str) -> frozenset[str] | None:
    """`git ls-files` for one clone, as a set (the committed-path surface every
    tracked-file predicate shares).

    Returns ``None`` when `git ls-files` FAILED (not a repo / git unavailable /
    nonzero exit) — DISTINCT from an empty frozenset, which means git ran and the
    repo genuinely tracks nothing. Tracked-file predicates must collapse the
    ``None`` case to RED: a false "nothing bad is tracked" GREEN on a clone we
    couldn't introspect is the quiet-wrong default. (#629 review.)"""
    result = proc.run(["git", "-C", path, "ls-files"], check=False)
    if result.returncode != 0:
        return None
    return frozenset(line for line in (result.stdout or "").splitlines() if line)


def _detect_kind(path: str) -> str:
    """`detect-kind` in the clone, or '?' (mirrors the verify sweep)."""
    result = proc.run(["detect-kind"], cwd=path, check=False)
    out = (result.stdout or "").strip()
    return out if result.returncode == 0 and out else "?"


def evaluate(consumers: list[Consumer], ctx: Context) -> dict[str, dict[str, tuple[str, str]]]:
    """Run every condition against every consumer.

    Returns matrix[condition.id][consumer.repo] = (verdict, detail)."""
    matrix: dict[str, dict[str, tuple[str, str]]] = {}
    for cond in CONDITIONS:
        row: dict[str, tuple[str, str]] = {}
        for consumer in consumers:
            row[consumer.repo] = cond.predicate(consumer, ctx)
        matrix[cond.id] = row
    return matrix


def removable(row: dict[str, tuple[str, str]]) -> bool:
    """An item is REMOVABLE when no consumer is RED for it AND at least one
    consumer is GREEN (an all-N/A row — e.g. a kind-gated item with no consumer
    of that kind in scope — is NOT evidence the surface is unused, so it is not
    reported removable)."""
    verdicts = [v for v, _detail in row.values()]
    if any(v == RED for v in verdicts):
        return False
    return any(v == GREEN for v in verdicts)


# ── Rendering ────────────────────────────────────────────────────────────────

_GLYPH = {GREEN: "✓", RED: "✗", NA: "·"}


def render_matrix(consumers: list[Consumer], matrix: dict[str, dict[str, tuple[str, str]]]) -> str:
    """The per-item × per-consumer matrix + the REMOVABLE verdicts, as text."""
    lines: list[str] = []
    repos = [c.repo for c in consumers]
    # Header: each condition is a row, each consumer a column; print the
    # consumer roster first so the single-char glyph columns are legible.
    lines.append("Consumers (columns, left→right):")
    for idx, consumer in enumerate(consumers, 1):
        lines.append(f"  {idx:>2}. {consumer.repo} [{consumer.kind}]")
    lines.append("")
    width = max((len(c.label) for c in CONDITIONS), default=0)
    col_idx = " ".join(f"{i:>2}" for i in range(1, len(consumers) + 1))
    lines.append(f"{'sunset condition':<{width}}  {col_idx}")
    for cond in CONDITIONS:
        glyphs = " ".join(f"{_GLYPH[matrix[cond.id][r][0]]:>2}" for r in repos)
        lines.append(f"{cond.label:<{width}}  {glyphs}    [item {cond.item}]")
    lines.append("")
    lines.append("Legend: ✓ green (condition met)  ✗ red (still blocks)  · n/a")
    lines.append("")
    any_removable = False
    for cond in CONDITIONS:
        row = matrix[cond.id]
        if removable(row):
            any_removable = True
            lines.append(f"REMOVABLE — see #569 item {cond.item}: {cond.label}")
        else:
            reds = sorted(r for r, (v, _d) in row.items() if v == RED)
            if reds:
                lines.append(
                    f"blocked  — #569 item {cond.item} ({cond.label}): red on {', '.join(reds)}"
                )
            else:
                lines.append(
                    f"n/a      — #569 item {cond.item} ({cond.label}): "
                    "no consumer in scope exercises this surface"
                )
    if not any_removable:
        lines.append("")
        lines.append("No #569 item is fully saturated yet — nothing removable on this evidence.")
    return "\n".join(lines)


def matrix_json(consumers: list[Consumer], matrix: dict[str, dict[str, tuple[str, str]]]) -> str:
    """Machine-readable matrix: items (with verdict + detail per consumer) and
    the REMOVABLE summary."""
    items = []
    for cond in CONDITIONS:
        row = matrix[cond.id]
        items.append(
            {
                "id": cond.id,
                "issue_569_item": cond.item,
                "label": cond.label,
                "removable": removable(row),
                "consumers": {
                    repo: {"verdict": verdict, "detail": detail}
                    for repo, (verdict, detail) in row.items()
                },
            }
        )
    return json.dumps(
        {
            "consumers": [{"repo": c.repo, "kind": c.kind} for c in consumers],
            "items": items,
        },
        indent=2,
    )


# ── Driver ───────────────────────────────────────────────────────────────────


def _usage_error(msg: str) -> int:
    print(msg, file=sys.stderr)
    print(USAGE.strip("\n"), file=sys.stderr)
    return 64


def _release_home() -> str:
    """The release checkout the wheel-current bootstrap-quartet bytes are read
    from. Mirrors the verify sweep's resolution: VERIFY_FLEET_SCRIPT_DIR/.. when
    set (the shim exports it), else ~/release."""
    script_dir = os.environ.get("VERIFY_FLEET_SCRIPT_DIR")
    if script_dir:
        return os.path.realpath(os.path.join(script_dir, ".."))
    return os.path.join(os.path.expanduser("~"), "release")


def main(argv: list[str]) -> int:  # noqa: C901, PLR0911, PLR0912 — linear driver
    user = os.environ.get("USER") or "shared"
    root = f"/tmp/release-fleet-verify-{user}"  # noqa: S108 — shared with verify sweep
    only = ""
    do_clone = True
    as_json = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            if i + 1 >= len(argv):
                return _usage_error("repos saturation: --root needs a value")
            i += 1
            root = argv[i]
        elif arg == "--only":
            if i + 1 >= len(argv):
                return _usage_error("repos saturation: --only needs a value")
            i += 1
            only = argv[i]
        elif arg == "--no-clone":
            do_clone = False
        elif arg == "--json":
            as_json = True
        elif arg in ("-h", "--help"):
            print(USAGE.strip("\n"))
            return 0
        else:
            return _usage_error(f"repos saturation: unknown arg: {arg}")
        i += 1

    for tool in ("git", "detect-kind"):
        if shutil.which(tool) is None:
            print(f"repos saturation: {tool} not on PATH", file=sys.stderr)
            return 2

    subset = [s for s in only.split(",") if s] if only else []
    repos_list = [*_SELF_CLI, "admin", "repos", "list"]

    # Phase 1: ride the SAME hermetic clone+refresh as the verify sweep (#624
    # makes it unconditional). --no-clone reuses an already-refreshed root.
    if do_clone:
        print("==> cloning/refreshing fleet (shared verify root)", file=sys.stderr)
        clone = proc.run(
            [*repos_list, "--clone", *subset],
            env={"REPOS_ROOT": root},
            check=False,
            capture_output=False,
        )
        if clone.returncode != 0:
            print(
                "repos saturation: fleet clone reported failures (measuring what cloned)",
                file=sys.stderr,
            )

    # Phase 2: resolve each consumer's clone path + kind + tracked set.
    paths = proc.run([*repos_list, "--paths", *subset], env={"REPOS_ROOT": root}, check=False)
    if paths.returncode != 0:
        print("repos saturation: cannot resolve fleet paths", file=sys.stderr)
        return 2

    consumers: list[Consumer] = []
    for line in (paths.stdout or "").splitlines():
        if not line:
            continue
        repo, abspath, found = line.split("\t", 2)
        if found != "found":
            print(f"repos saturation: {repo}: clone missing — skipped", file=sys.stderr)
            continue
        consumers.append(
            Consumer(
                repo=repo,
                path=abspath,
                kind=_detect_kind(abspath),
                tracked=_tracked_files(abspath),
            )
        )

    if not consumers:
        print("repos saturation: no consumer clones to measure", file=sys.stderr)
        return 2

    ctx = Context(release_home=_release_home())
    matrix = evaluate(consumers, ctx)

    if as_json:
        print(matrix_json(consumers, matrix))
    else:
        print(render_matrix(consumers, matrix))
    # INFORMATIONAL: saturation is a ratchet, never a CI gate. Exit 0 whether or
    # not the fleet is saturated; non-zero is reserved for "the meter couldn't
    # run" (handled above).
    return 0
