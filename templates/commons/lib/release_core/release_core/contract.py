"""contract — the consumer-contract manifest + the assumption lint (#584, epic #583).

Root cause A of #583: the consumer-tree contract (what is tracked, what is an
untracked ephemeral mirror, what lives only in `.release/`) was implicit in
``sync.py``'s constants and predicates, so nothing could mechanically enumerate
"who still assumes the old shape" — #579 (`rust-ci.yml`'s e2e job sparse-checking
out paths that became untracked mirrors in WS4/WS7) stayed latent for weeks.

Two artifacts fix that:

1. **The manifest** (``docs/references/consumer-contract.yaml``) — a checked-in,
   versioned VIEW of the contract, generated from ``sync.py``'s constants +
   classification predicates applied to this repo's ``templates/`` source tree
   (the lockfile pattern: sync.py stays the single source of truth; the manifest
   is checkable). :func:`manifest_text` renders it byte-stably (no timestamps,
   no SHAs — versioned by ``contract_schema``), so ``contract check`` is a plain
   string diff. The manifest is PRESCRIPTIVE, not descriptive (the #583 owner
   constraint): it states what a consumer tree MUST look like; divergence is
   fixed in the consumer, never absorbed as a manifest exception.

2. **The assumption lint** (:func:`lint_repo`) — scans this repo's CI surfaces
   (reusable workflows, composite actions, shipped workflow copies under
   ``templates/``) for any JOB whose steps reference a managed path (from the
   manifest's managed-path patterns) without a PRIOR build step in the
   same job (the ``arm-gate`` composite or an explicit ``release-core init``).
   This is the sweep that would have caught #579 the day WS7 merged.

Everything here derives from :mod:`release_core.sync` — extract, never
duplicate. YAML parsing goes through :mod:`release_core.yamlio` (the yq seam).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from . import sync, yamlio

# Where the committed manifest lives, relative to the release repo root.
MANIFEST_RELPATH = "docs/references/consumer-contract.yaml"

# The hand-maintained, SHRINK-ONLY lint baseline (see load_baseline /
# apply_baseline): pre-existing stale-shape jobs grandfathered when the lint
# landed (#584). Drained to zero and the FILE DELETED in release#588 — a
# missing file is an empty baseline. The mechanism stays (an entry that no
# longer matches any violation FAILS the lint: "stale baseline entry"), but
# re-creating the file is never the fix for a new finding — normalize the
# workflow to the standard recipe instead.
BASELINE_RELPATH = "docs/references/consumer-contract-lint-baseline.yaml"

# Schema version of the manifest document. Bump when the SHAPE of the document
# changes (keys added/renamed/removed) — content changes (a new mirror dest, a
# new retired-file entry) regenerate under the same schema.
CONTRACT_SCHEMA = 1

# The managed-path PREFIXES: a CI job referencing any path under these must
# build the managed tree first (arm-gate / release-core init) — post
# WS4/WS7 none of them exist in a fresh checkout. `bin/check` is deliberately a
# prefix (covers bin/check, bin/check-e2e, bin/check-gate, …, the #579 family);
# exact mirror dests (e.g. `.editorconfig`) come from the manifest's
# ``untracked_mirrors`` list, not from here.
MANAGED_PATH_PREFIXES: tuple[str, ...] = (
    ".release/",
    "bin/check",
    "lib/release_core/",
)

# Top-level templates/ dirs that are NOT consumer Kinds (mirrors
# hatch_build.py's _NON_SURFACE_TEMPLATE_DIRS — render/ holds release-cut-side
# render material, never synced to a consumer).
NON_SURFACE_TEMPLATE_DIRS: frozenset[str] = frozenset({"render"})


class ContractError(RuntimeError):
    """A fatal contract condition (maps to exit 1)."""


# ── Manifest generation (sync.py constants + predicates → data) ──────────────


def _template_kinds(repo_root: str) -> list[str]:
    """Every consumer Kind dir under templates/ (sorted): all top-level dirs
    except the commons/components subtrees and the non-surface dirs."""
    tdir = os.path.join(repo_root, "templates")
    if not os.path.isdir(tdir):
        raise ContractError(f"contract: no templates/ tree under {repo_root!r}")
    skip = {"commons", "components"} | set(NON_SURFACE_TEMPLATE_DIRS)
    return sorted(
        d for d in os.listdir(tdir) if os.path.isdir(os.path.join(tdir, d)) and d not in skip
    )


def _capability_names(repo_root: str) -> list[str]:
    """Every Component dir under templates/components/ (sorted; `_`-prefixed
    entries are shared fragments, not Components — mirrors should_skip_source)."""
    cdir = os.path.join(repo_root, "templates", "components")
    if not os.path.isdir(cdir):
        return []
    return sorted(
        d
        for d in os.listdir(cdir)
        if os.path.isdir(os.path.join(cdir, d)) and not d.startswith("_")
    )


def _all_consumer_dests(repo_root: str) -> set[str]:
    """The UNION of every dest the sync engine can build into a consumer,
    across all Kinds and all Components — each subtree walked exactly as
    ``build_plan`` walks it (same Source abstraction, same skip predicate,
    same prefix-strip), plus every distributed-skill dest (both catalogs:
    PUSH_ALL_SKILLS unconditionally and REPLACE_IF_PRESENT_SKILLS, which are
    part of the contract surface even though they sync conditionally)."""
    source = sync.BundleSource(repo_root)
    subtrees = ["templates/commons"]
    subtrees += [f"templates/components/{c}" for c in _capability_names(repo_root)]
    subtrees += [f"templates/{k}" for k in _template_kinds(repo_root)]

    dests: set[str] = set()
    for st in subtrees:
        prefix = st + "/"
        for rel, _mode in source.list_tree(st):
            if sync.should_skip_source(rel):
                continue
            dests.add(rel[len(prefix) :] if rel.startswith(prefix) else rel)

    for name in sorted(set(sync.PUSH_ALL_SKILLS) | set(sync.REPLACE_IF_PRESENT_SKILLS)):
        for rel, _mode in source.list_tree(f"skills/{name}"):
            if sync.should_skip_source(rel):
                continue
            dests.add(f".claude/{rel}")  # the _add_skill_dir dest mapping
    return dests


def build_manifest(repo_root: str) -> dict:
    """The manifest as plain data, every list sorted (deterministic by
    construction). Classification is sync.py's own predicates — never a
    re-statement of them."""
    dests = _all_consumer_dests(repo_root)

    bootstrap = sorted(sync.BOOTSTRAP_REAL_FILES)
    workflow_copies: list[str] = []
    mirrors: list[str] = []
    for dest in sorted(dests):
        if sync.is_release_internal(dest):
            continue  # lives only inside .release/ — covered by gitignored + gate_internal
        if sync.needs_real_file(dest):
            if dest not in sync.BOOTSTRAP_REAL_FILES:
                workflow_copies.append(dest)
        else:
            mirrors.append(dest)

    return {
        "contract_schema": CONTRACT_SCHEMA,
        "tracked_real_files": {
            "bootstrap": bootstrap,
            "workflow_copies": workflow_copies,
        },
        "gitignored": [".release/"],
        "untracked_mirrors": mirrors,
        "gate_internal": sorted(sync.GATE_INTERNAL_FILES),
        "tombstones": {
            "blob": sorted(sync.RETIRED_BLOB_FILES),
            "fingerprint": sorted(sync.RETIRED_FINGERPRINT_FILES),
        },
        "managed_path_prefixes": list(MANAGED_PATH_PREFIXES),
    }


# The provenance header. Deliberately NO volatile content (no timestamp, no
# commit SHA): the file must be byte-stable across regenerations of the same
# tree so `contract check` is a plain diff. Versioning is the contract_schema
# field + ordinary git history of the file itself.
_HEADER = """\
# consumer-contract.yaml — the consumer-tree contract (epic #583, WS-A #584).
#
# GENERATED — do not hand-edit. Source of truth: release_core/sync.py
# (constants + classification predicates) applied to this repo's templates/
# tree. Regenerate with:
#
#   release-core admin contract dump
#
# A PR that changes the contract (sync.py classification, templates/ dests,
# tombstones) MUST regenerate this file in the same PR — the root lefthook.yml
# `consumer-contract-check` gate entry fails when the file is out of sync.
#
# This manifest is PRESCRIPTIVE, not descriptive (#583 owner constraint): it
# states what a consumer tree MUST look like. Where a repo diverges, the fix
# is a one-time consumer normalization PR — never a manifest exception or a
# central knob. Escape hatches shrink, not grow.
#
# Sections:
#   tracked_real_files    — the only files release tracks in a consumer: the
#                           bootstrap quartet (must work on a fresh clone,
#                           BEFORE .release/ exists) + .github/workflows/*
#                           managed real copies (GitHub won't deref symlinks).
#   gitignored            — the ephemeral .release/ build dir (WS4).
#   untracked_mirrors     — symlink dests recomposed every init, excluded via
#                           .git/info/exclude, NEVER tracked (WS7).
#   gate_internal         — live only inside .release/ (WS3).
#   tombstones            — retired dests init removes (provenance-gated, WS6).
#   managed_path_prefixes — path prefixes whose presence in a CI job means the
#                           job MUST build first (arm-gate /
#                           release-core init); the assumption lint
#                           (bin-internal/lint-consumer-contract.sh) enforces
#                           this over every workflow + composite action.
"""


def _yaml_scalar(value: object) -> str:
    """Render one scalar deterministically. Strings via json.dumps (a JSON
    string is a valid YAML double-quoted scalar — no quoting edge cases)."""
    if isinstance(value, bool):  # before int — bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _render_node(node: object, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    if isinstance(node, dict):
        for key, value in node.items():  # insertion order — fixed by build_manifest
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                _render_node(value, indent + 1, lines)
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(value)}")
    elif isinstance(node, list):
        if not node:
            lines[-1] += " []"
            return
        for item in node:
            lines.append(f"{pad}- {_yaml_scalar(item)}")
    else:  # pragma: no cover - build_manifest only nests dict/list
        lines.append(f"{pad}{_yaml_scalar(node)}")


def render_manifest(data: dict) -> str:
    """Deterministic YAML for the manifest data: fixed key order (insertion),
    sorted lists (sorted at build time), two-space indent, double-quoted
    strings. Hand-rendered so the bytes depend on nothing but the data."""
    lines: list[str] = []
    _render_node(data, 0, lines)
    return _HEADER + "\n" + "\n".join(lines) + "\n"


def manifest_text(repo_root: str) -> str:
    """The full manifest document for ``repo_root``'s source tree."""
    return render_manifest(build_manifest(repo_root))


# ── The assumption lint ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    """One job step that references a managed path with no prior build step."""

    file: str  # repo-relative path of the workflow / action file
    job: str  # job id ("(composite)" for a composite action's steps)
    step: str  # step name, or "step N" when unnamed
    matched: str  # the managed-path reference that triggered


def managed_path_patterns(manifest: dict) -> list[str]:
    """The lint's pattern set, read from a (parsed) manifest document: the
    managed-path prefixes plus every exact untracked-mirror dest. All are
    matched as path PREFIXES with a left boundary (see _reference_regex)."""
    prefixes = manifest.get("managed_path_prefixes") or []
    mirrors = manifest.get("untracked_mirrors") or []
    if not isinstance(prefixes, list) or not prefixes:
        raise ContractError(
            "contract lint: manifest has no managed_path_prefixes — regenerate it "
            "(release-core admin contract dump)"
        )
    out: list[str] = [str(p) for p in prefixes]
    for m in mirrors:
        m = str(m)
        # Skip mirrors already covered by a prefix — keeps the regex small.
        if not any(m.startswith(p) for p in out):
            out.append(m)
    return out


def _reference_regex(patterns: list[str]) -> re.Pattern[str]:
    """One alternation over all patterns, each anchored at a path boundary.

    The char before a match must not be a path/word char — that is what keeps
    release's OWN source paths (``templates/commons/lib/release_core/…``,
    ``bin-internal/…``) from matching the consumer-root patterns
    (``lib/release_core/``, ``bin/check``): there the candidate is preceded by
    ``/`` or ``-``. Genuine workspace-anchored references are still caught via
    the explicit optional anchors (``./``, ``$GITHUB_WORKSPACE/``,
    ``${{ github.workspace }}/``, ``$PWD/``).
    """
    alts = "|".join(re.escape(p) for p in sorted(patterns, key=len, reverse=True))
    anchor = (
        r"(?:\./"
        r"|\$\{?GITHUB_WORKSPACE\}?/"
        r"|\$\{\{\s*github\.workspace\s*\}\}/"
        r"|\$\{?PWD\}?/"
        r")?"
    )
    # ``ref`` extends past the pattern to the full referenced path so the
    # provider check (a checkout INTO `.release/`, say) and the report are
    # precise about what was referenced.
    return re.compile(rf"(?<![A-Za-z0-9_./-]){anchor}(?P<ref>(?:{alts})[A-Za-z0-9_./-]*)")


def _strip_shell_comments(text: str) -> str:
    """Drop full-line comments from a run script so prose mentioning managed
    paths (pervasive in this repo's annotated workflows) doesn't trip the lint.
    Inline trailing comments are NOT stripped — `#` is meaningful in too many
    shell contexts to cut mid-line safely; full-line is where the prose lives."""
    kept = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(kept)


def _step_is_materialize(step: dict) -> bool:
    """A step that composes the managed tree: the arm-gate composite (its
    materialize step runs `release-core init`; matched local-path and @ref
    forms both) or an explicit `release-core init` run line. Exactly these two
    — the standard recipe is DEMANDED, not one option among many (#583 owner
    constraint: escape hatches shrink, not grow).

    arm-gate called with `materialize: 'false'` (release's own ci.yml — the one
    repo with real root configs) does NOT compose the tree, so it earns no
    credit; `toolset: 'false'` (#581, the build-only building block)
    still builds the tree and does."""
    uses = step.get("uses")
    if isinstance(uses, str) and uses.split("@", 1)[0].endswith("/arm-gate"):
        with_block = step.get("with")
        # str().lower() catches both the GH-conventional quoted 'false' and a
        # bare YAML `false` (parsed as a bool).
        return not (
            isinstance(with_block, dict) and str(with_block.get("materialize")).lower() == "false"
        )
    run = step.get("run")
    return isinstance(run, str) and "release-core init" in _strip_shell_comments(run)


def _step_texts(step: dict) -> list[str]:
    """The step fields the lint scans for managed-path references: the run
    script (full-line comments stripped), `with:` values, and `env:` values.
    Names/ids/conditions are prose-adjacent and deliberately not scanned."""
    texts: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        texts.append(_strip_shell_comments(run))
    for key in ("with", "env"):
        block = step.get(key)
        if isinstance(block, dict):
            texts.extend(str(v) for v in block.values() if v is not None)
    return texts


def _step_label(step: dict, index: int) -> str:
    name = step.get("name")
    return str(name) if name else f"step {index + 1}"


def _step_checkout_path(step: dict) -> str | None:
    """For an ``actions/checkout`` step with an explicit ``with.path``, the dir
    it populates (normalized to a trailing-`/` prefix), else None. A checkout
    INTO a managed-named path (tauri-app.yml / nvim-plugin.yml stage release's
    ``bin-internal`` at ``path: .release``) genuinely PROVIDES that subtree for
    later steps — referencing it is not an old-shape assumption."""
    uses = step.get("uses")
    if not isinstance(uses, str) or not uses.split("@", 1)[0].endswith("/checkout"):
        return None
    with_block = step.get("with")
    if not isinstance(with_block, dict):
        return None
    path = with_block.get("path")
    if not isinstance(path, str) or not path:
        return None
    return path.rstrip("/") + "/"


def _lint_steps(relpath: str, job_id: str, steps: list, regex: re.Pattern[str]) -> list[Violation]:
    """One job's steps, in order: a managed-path reference is a violation
    unless a PRIOR step in the SAME job either built the managed tree
    (arm-gate / release-core init) or checked content out INTO that path."""
    out: list[Violation] = []
    materialized = False
    provided: list[str] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if _step_is_materialize(step):
            materialized = True
            continue
        checkout_path = _step_checkout_path(step)
        if checkout_path is not None:
            provided.append(checkout_path)
            continue
        if materialized:
            continue
        ref = _first_unprovided_ref(step, regex, provided)
        if ref is not None:
            out.append(Violation(relpath, job_id, _step_label(step, i), ref))
    return out


def _first_unprovided_ref(step: dict, regex: re.Pattern[str], provided: list[str]) -> str | None:
    """The first managed-path reference in the step's scanned texts that is not
    under a checkout-provided prefix; None when the step is clean. One ref per
    step is enough — the fix (build first) is per-job anyway."""
    for text in _step_texts(step):
        for m in regex.finditer(text):
            ref = m.group("ref")
            if not any(ref == p.rstrip("/") or ref.startswith(p) for p in provided):
                return ref
    return None


def lint_file(repo_root: str, relpath: str, regex: re.Pattern[str]) -> list[Violation]:
    """Lint one workflow / composite-action YAML file.

    Workflows: each ``jobs.<id>`` with a ``steps`` list is a job (reusable-call
    jobs have no steps — nothing to lint). Composite actions (``runs.steps``,
    no ``jobs``): the whole steps list is ONE pseudo-job, ``(composite)``."""
    data = yamlio.load(os.path.join(repo_root, relpath))
    if not isinstance(data, dict):
        return []
    out: list[Violation] = []
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        for job_id, job in sorted(jobs.items()):
            if isinstance(job, dict) and isinstance(job.get("steps"), list):
                out.extend(_lint_steps(relpath, str(job_id), job["steps"], regex))
    runs = data.get("runs")
    if isinstance(runs, dict) and isinstance(runs.get("steps"), list):
        out.extend(_lint_steps(relpath, "(composite)", runs["steps"], regex))
    return out


def ci_surface_files(repo_root: str) -> list[str]:
    """Every CI surface the lint sweeps (sorted, repo-relative): reusable
    workflows, composite actions, and the workflow copies shipped to consumers
    under templates/ (any depth — they land as managed real copies)."""
    out: set[str] = set()

    wf_dir = os.path.join(repo_root, ".github", "workflows")
    if os.path.isdir(wf_dir):
        for name in os.listdir(wf_dir):
            if name.endswith((".yml", ".yaml")):
                out.add(f".github/workflows/{name}")

    actions_dir = os.path.join(repo_root, ".github", "actions")
    if os.path.isdir(actions_dir):
        for name in os.listdir(actions_dir):
            for fname in ("action.yml", "action.yaml"):
                if os.path.isfile(os.path.join(actions_dir, name, fname)):
                    out.add(f".github/actions/{name}/{fname}")

    tdir = os.path.join(repo_root, "templates")
    for dirpath, dirnames, filenames in os.walk(tdir):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")
        if rel_dir.endswith(".github/workflows"):
            for name in sorted(filenames):
                if name.endswith((".yml", ".yaml")):
                    out.add(f"{rel_dir}/{name}")

    return sorted(out)


def lint_repo(repo_root: str, manifest: dict) -> list[Violation]:
    """The full assumption sweep: every CI surface file, every job."""
    regex = _reference_regex(managed_path_patterns(manifest))
    out: list[Violation] = []
    for relpath in ci_surface_files(repo_root):
        out.extend(lint_file(repo_root, relpath, regex))
    return out


def lint_workflow_dir(repo_root: str, patterns: list[str]) -> list[Violation]:
    """The CONSUMER-side sweep (#581): scan only ``.github/workflows/**`` of a
    consumer repo for jobs that reference a managed ephemeral path (``patterns``
    — the init-resolved mirror dests + the managed prefixes) without a prior
    build step. Same scanner as :func:`lint_repo` (one scanner, two file
    enumerations); ``release-core init`` runs this at seed/session time as the
    tripwire warning for consumer-authored jobs (the supage#163 class).

    Resilient by design: a consumer workflow that fails to parse is SKIPPED
    (the consumer's own CI will surface its syntax error) so one broken file
    cannot silence the sweep over the rest."""
    regex = _reference_regex(patterns)
    out: list[Violation] = []
    wf_dir = os.path.join(repo_root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return out
    for name in sorted(os.listdir(wf_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        try:
            out.extend(lint_file(repo_root, f".github/workflows/{name}", regex))
        except yamlio.YamlError:
            continue
    return out


# ── The shrink-only baseline ──────────────────────────────────────────────────


def load_baseline(repo_root: str) -> list[dict]:
    """The hand-maintained baseline entries (``known_stale_jobs``: a list of
    ``{file, job, reason}``). A missing file is an empty baseline."""
    path = os.path.join(repo_root, BASELINE_RELPATH)
    if not os.path.isfile(path):
        return []
    data = yamlio.load(path)
    if not isinstance(data, dict):
        raise ContractError(f"contract lint: malformed baseline {BASELINE_RELPATH}")
    entries = data.get("known_stale_jobs") or []
    if not isinstance(entries, list):
        raise ContractError(f"contract lint: malformed baseline {BASELINE_RELPATH}")
    for e in entries:
        if not isinstance(e, dict) or "file" not in e or "job" not in e:
            raise ContractError(
                f"contract lint: baseline entry missing file/job: {e!r} ({BASELINE_RELPATH})"
            )
    return entries


def apply_baseline(
    violations: list[Violation], baseline: list[dict]
) -> tuple[list[Violation], list[Violation], list[dict]]:
    """Partition the sweep against the baseline.

    Returns ``(failing, grandfathered, stale_entries)``:
      failing        — violations NOT covered by a baseline entry (lint fails);
      grandfathered  — violations covered by an entry (reported, non-failing);
      stale_entries  — baseline entries matching NO violation (lint fails too:
                       the debt is gone, so the entry must be deleted — the
                       baseline only ever shrinks).
    """
    keyed = {(str(e["file"]), str(e["job"])): e for e in baseline}
    failing: list[Violation] = []
    grandfathered: list[Violation] = []
    hit: set[tuple[str, str]] = set()
    for v in violations:
        key = (v.file, v.job)
        if key in keyed:
            grandfathered.append(v)
            hit.add(key)
        else:
            failing.append(v)
    stale = [e for key, e in keyed.items() if key not in hit]
    return failing, grandfathered, stale
