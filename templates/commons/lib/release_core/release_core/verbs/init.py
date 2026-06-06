"""init — materialize the per-repo COMMITTED config files into a consumer repo.

Usage:
  release-core init [--force] [--dry-run] [--commit] [--push]
  release-core init --full [--dry-run] [--no-commit] [--push]

`release-core init` is the seam (pip-bootstrap PoC) that replaces release-sync's
*config* materialization. The package arrives
via `pip install`; the files a consumer must have committed IN ITS OWN GIT TREE —
the gate definition (`lefthook.yml`) and the managed lint/format configs — are
written here.

Scope (the CONFIG subset of what release-sync materializes from
templates/commons/; see CONFIG_FILES below and sync.py for provenance):

  lefthook.yml            (the gate — composed from fragments by sync)
  .markdownlint.json      .markdownlintignore
  .yamllint
  .shellcheckrc
  .editorconfig
  .prettierignore

NOT in scope (deliberately small seam — the full sync->init migration is post-PoC):
  - package code (lib/release_core/**, incl. the folded prstate engine) — via pip
  - release-internal files (.release-sync-source, ORIENTATION.md)
  - the CLAUDE.md orientation block, skills, .claude/settings.json, CHANGELOG/
  - git-hook wiring (stays in setup-dev-env.sh)

Behavior:
  - create-if-absent: an existing file is LEFT UNTOUCHED (never overwrite a
    consumer edit in the PoC) unless --force is passed.
  - idempotent: a second run with everything present is a clean no-op (exit 0,
    no writes).
  - --force: overwrite managed files even when present.
  - --dry-run: print what WOULD happen, write nothing.
  - exits NON-ZERO on any real failure (cannot write a file it intended to). No
    silent best-effort swallowing for init's own writes.

Auto-commit (the pull-model commit-hygiene seam):
  - --commit: after a materialization that actually changed files, stage and
    commit ONLY the exact managed paths init wrote (never `git add -A`, never
    fold in a user's other staged/unstaged work) with a deterministic message.
    The managed config tree is fully GENERATED — nothing to review — so it can
    and should auto-commit itself instead of riding along, uncommitted, in some
    unrelated feature PR. Conservative by construction: no changes → no commit;
    --dry-run → no commit; an unborn branch (no HEAD) or any git error makes the
    commit *step* a quiet no-op; if the managed paths can't be staged cleanly it
    prints a notice and skips the commit. The commit step never fails init (init
    itself still requires its normal git context). Opt-in: a plain `init` stays
    non-committing.
  - --push (implies --commit): fast-forward push the managed commit ONLY when
    ALL hold — --push given, the current branch IS the repo's default branch,
    and the working tree is otherwise clean (no non-managed changes). On a
    feature branch (or a dirty tree) the commit stays local and just rides the
    branch — visible, and excluded from review as a managed change. Never
    force-pushes, never merges a PR.

  Scope: --commit/--push cover ONLY the CONFIG subset init materializes (see
  CONFIG_FILES) — NOT the full `.release/` tree (that is release-sync; the
  sync->init migration is separate).

Full materialize (--full, #476 part 2 — flag-gated, opt-in):
  `release-core init --full` runs the COMPLETE release-sync pipeline (build_plan
  + materialize + compute_mirror + apply) sourced from the wheel bundle — the
  whole `.release/` build dir + every working-tree mirror (skills, ORIENTATION,
  configs, per-Kind/Capability files, real-file workflow copies, the CLAUDE.md
  managed block). It is "release-sync sourced from the wheel", so its output is
  byte-identical to a `release-sync` run for the same Kind (modulo the
  provenance-marker line). A real $RELEASE_HOME git clone OVERRIDES the bundle
  (release-dev's live-templates path), exactly as the config path does.

  AUTO-COMMIT ON CHANGE (the decided commit-hygiene model): after a full
  materialize, if (and only if) managed content actually changed, --full commits
  ONLY the managed paths with a deterministic message, on whatever branch is
  checked out (the managed tree is generated — needs no review). Byte-identical
  result → no commit, so churn tracks release cadence, not session count.
  Idempotent: a second `init --full` with no upstream change makes no commit.
  --no-commit skips committing (for tests/inspection); --dry-run computes the
  change count and writes nothing.

  Plain `init` (no --full) is UNCHANGED — the config subset only. The cutover to
  make --full the default / wire it into the SessionStart boot is a later #476
  step, not this one.

Source resolution: the canonical config content is composed from the
wheel-bundled templates (release_core/_bundled_templates/, staged at build time
by hatch_build.py) so init is self-contained — no release clone, no network.
This is the DEFAULT and the only path a pip-installed consumer ever takes.

A `$RELEASE_HOME` git checkout, when explicitly present (release-dev only),
OVERRIDES the bundle: init then composes from live templates via the full
release-sync engine (sync.build_plan + sync.materialize) at $RELEASE_REF, the
same git-clone contract release-sync uses. In an editable/source checkout the
bundle is absent (a gitignored build artifact), so $RELEASE_HOME is required
there; a fresh wheel install needs neither.

Exit codes:
  0  — done (created/refreshed, or a clean no-op)
  1  — fatal error (cannot resolve source, or a write failed)
  64 — bad usage
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

from .. import cli, gh, manifest, sync, yamlio

USAGE = __doc__ or ""

# The CONFIG subset of what release-sync materializes from templates/commons/.
# Derived from sync.py: build_plan() walks templates/commons (subtree_list) and
# strips the `templates/commons/` prefix to the dest; materialize() writes each
# blob plus the composed `lefthook.yml`. Of those dests we take only the
# committed *config* files — excluding package code (is_release_internal:
# lib/release_*), .release-sync-source (SOURCE_MARKER), ORIENTATION.md
# (is_release_internal), the CLAUDE.md block, skills, .claude/settings.json, and
# CHANGELOG/ scaffolding. The remainder is the gate + the managed lint/format
# configs:
CONFIG_FILES: tuple[str, ...] = (
    "lefthook.yml",
    ".markdownlint.json",
    ".markdownlintignore",
    ".yamllint",
    ".shellcheckrc",
    ".editorconfig",
    ".prettierignore",
)


def _usage_block() -> str:
    """The --help body: the docstring (init has no bash predecessor to
    byte-match, so the whole docstring is the help text)."""
    return (USAGE.strip("\n")).rstrip("\n")


def _bundle_root() -> str | None:
    """Absolute path to the wheel-bundled source root (release_core/
    _bundled_templates/), or None if not bundled.

    This is the BundleSource root: its layout mirrors the repo —
    <root>/templates/… and <root>/skills/… — so a sync ``subtree`` like
    "templates/commons" or "skills/tdd" resolves directly. The full-tree
    materialize (init --full) reads through this; the config-subset path uses
    _bundle_templates_root() (the templates/ subdir) for its direct file copies.
    """
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # release_core/
    root = os.path.join(here, "_bundled_templates")
    return root if os.path.isdir(os.path.join(root, "templates")) else None


def _bundle_templates_root() -> str | None:
    """Absolute path to the wheel-bundled templates tree, or None if not bundled.

    The wheel ships the config templates under release_core/_bundled_templates/
    (staged at build time, see hatch_build.py) so init is self-contained — no
    release clone needed. In an editable/source checkout the dir is absent
    (it's a build artifact, gitignored), so this returns None and init falls
    back to the $RELEASE_HOME git path.
    """
    root = _bundle_root()
    if root is None:
        return None
    templates = os.path.join(root, "templates")
    return templates if os.path.isdir(templates) else None


def _read_sync_yaml(repo_root: str) -> str | None:
    sync_yaml = os.path.join(repo_root, ".release-sync.yaml")
    if os.path.isfile(sync_yaml):
        with open(sync_yaml, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return None


def _capabilities_from_bundle(tpl_root: str, kind: str) -> list[str]:
    """Kind-default capabilities from the bundled templates/<kind>/manifest.yaml
    (mirrors sync.resolve_capabilities' manifest branch, read from the bundle)."""
    manifest_path = os.path.join(tpl_root, kind, "manifest.yaml")
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8", errors="replace") as fh:
            return sync._yq_list_capabilities(fh.read())
    return []


def _materialize_config_from_bundle(
    tpl_root: str, repo_root: str, kind: str, capabilities: list[str]
) -> dict[str, str]:
    """Compose the config subset from the wheel-bundled templates (no git).

    Mirrors the release-sync composition for the CONFIG subset only: copy the
    static commons lint configs, then deep-merge the lefthook fragments (base <
    commons < each capability < kind) via yq — the same merge sync._write_lefthook
    does, sourced from the bundle instead of a git ref.
    """
    tmp = tempfile.mkdtemp(prefix=".release-core-init.")
    sources: dict[str, str] = {}

    for dest in CONFIG_FILES:
        if dest == "lefthook.yml":
            continue
        src = os.path.join(tpl_root, "commons", dest)
        if os.path.isfile(src):
            out = os.path.join(tmp, dest)
            shutil.copyfile(src, out)
            sources[dest] = out

    frag_rel = [
        os.path.join("components", "_lefthook-base.yaml"),
        os.path.join("commons", "lefthook.fragment.yaml"),
    ]
    frag_rel += [os.path.join("components", c, "lefthook.fragment.yaml") for c in capabilities if c]
    frag_rel.append(os.path.join(kind, "lefthook.fragment.yaml"))
    frags = [
        os.path.join(tpl_root, f) for f in frag_rel if os.path.isfile(os.path.join(tpl_root, f))
    ]

    if frags:
        frag_tmp = tempfile.mkdtemp()
        try:
            numbered: list[str] = []
            for i, fp in enumerate(frags):
                dirbase = os.path.basename(os.path.dirname(fp))
                np = os.path.join(frag_tmp, f"{i:02d}-{dirbase}.yaml")
                shutil.copyfile(fp, np)
                numbered.append(np)
            merged = yamlio.eval_all('. as $i ireduce({}; . *+ $i) | ... comments=""', numbered)
            out = os.path.join(tmp, "lefthook.yml")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(
                    "# Generated by release-core init from the bundled templates. Do not edit.\n"
                    "# Regenerate by running release-core init.\n\n"
                )
                fh.write(merged)
            sources["lefthook.yml"] = out
        finally:
            shutil.rmtree(frag_tmp, ignore_errors=True)

    return sources


def _materialize_config_sources(
    repo_root: str, repo_name: str, *, source_ref: dict[str, str] | None = None
) -> dict[str, str]:
    """Compose the canonical config content and return {dest -> absolute path in
    a temp tree} for every CONFIG_FILES dest produced.

    DEFAULT: compose from the wheel-bundled templates (self-contained, no clone).
    A `$RELEASE_HOME` git checkout, when explicitly present, OVERRIDES the bundle
    (release-dev uses live templates via the full sync engine). May raise
    manifest.KindError / sync.SyncError / yamlio.YamlError — main() maps each to
    a clean exit 1.

    ``source_ref``, when given, is populated with a ``"ref"`` key describing the
    source the config was composed from (the resolved tip on the git-engine path;
    the wheel version on the bundle path), for use in the --commit message.
    """
    release_home = os.environ.get("RELEASE_HOME")
    have_clone = bool(release_home) and gh.is_git_worktree(release_home)
    tpl_root = _bundle_templates_root()

    kind = manifest.detect_kind(repo_root)

    # Self-contained path: bundled templates, no git. Used unless a real
    # release clone is explicitly pointed at via $RELEASE_HOME.
    if tpl_root and not have_clone:
        sync_yaml_text = _read_sync_yaml(repo_root)
        if sync_yaml_text is not None:
            caps_names = sync._yq_list_capabilities(sync_yaml_text)
        else:
            caps_names = _capabilities_from_bundle(tpl_root, kind)
        if source_ref is not None:
            from .. import __version__ as _v

            source_ref["ref"] = f"release-core {_v}"
        return _materialize_config_from_bundle(tpl_root, repo_root, kind, caps_names)

    if not have_clone:
        raise sync.SyncError(
            "release-core init: no bundled templates and "
            f"$RELEASE_HOME='{release_home or ''}' is not a git clone"
        )

    release_ref = os.environ.get("RELEASE_REF") or None
    ref = sync.select_ref(release_home, repo_name, kind, release_ref)
    ref_sha = gh.git_rev_parse(ref, cwd=release_home)
    if source_ref is not None:
        # The short SHA is the most precise, reproducible description of what
        # the managed config was composed from.
        source_ref["ref"] = ref_sha[:12] if ref_sha else ref

    # Honor a consumer .release-sync.yaml capability override, exactly as
    # release-sync does (verbs/release_sync.py) — so init composes the SAME
    # config set the consumer would get from a sync.
    sync_yaml_text = None
    sync_yaml = os.path.join(repo_root, ".release-sync.yaml")
    if os.path.isfile(sync_yaml):
        with open(sync_yaml, encoding="utf-8", errors="replace") as fh:
            sync_yaml_text = fh.read()

    source = sync.GitSource(release_home, ref, ref_sha)
    caps = sync.resolve_capabilities(source, kind, sync_yaml_text=sync_yaml_text)
    plan = sync.build_plan(source, kind, caps.names, repo_root=repo_root)

    tmp_root = tempfile.mkdtemp(prefix=".release-core-init.")
    sync.materialize(source, ref_sha, plan, tmp_root)

    sources: dict[str, str] = {}
    for dest in CONFIG_FILES:
        candidate = os.path.join(tmp_root, dest)
        if os.path.isfile(candidate):
            sources[dest] = candidate
    return sources


# ── Full materialize (init --full): the whole managed tree, from the bundle ───
#
# This is "release-sync sourced from the wheel bundle": the SAME engine pipeline
# the release_sync verb runs (build_plan + materialize + diff + compute_mirror +
# decide_claude + apply), driven by BundleSource by default, or GitSource when a
# real $RELEASE_HOME clone is present (release-dev override, mirroring how the
# config path prefers $RELEASE_HOME over the bundle). It materializes the full
# .release/ build dir plus all working-tree mirrors (symlinks, real-file copies,
# the CLAUDE.md orientation block) — everything release-sync produces.


def _resolve_full_source(repo_root: str, repo_name: str) -> tuple[sync.Source, str, list[str]]:
    """Pick the sync Source for a full materialize and resolve Kind + capabilities.

    Returns (source, kind, capability_names). DEFAULT: BundleSource over the
    wheel bundle (self-contained, no clone). A real $RELEASE_HOME git checkout
    OVERRIDES it (release-dev's live-templates path), exactly as the config path
    does. May raise manifest.KindError / sync.SyncError / yamlio.YamlError —
    main() maps each to a clean exit 1.
    """
    release_home = os.environ.get("RELEASE_HOME")
    have_clone = bool(release_home) and gh.is_git_worktree(release_home)
    kind = manifest.detect_kind(repo_root)
    sync_yaml_text = _read_sync_yaml(repo_root)

    if not have_clone:
        bundle_root = _bundle_root()
        if bundle_root is None:
            raise sync.SyncError(
                "release-core init --full: no bundled templates and "
                f"$RELEASE_HOME='{release_home or ''}' is not a git clone"
            )
        from .. import __version__ as _v

        source: sync.Source = sync.BundleSource(bundle_root, ref_sha=f"release-core {_v}")
    else:
        assert release_home is not None
        release_ref = os.environ.get("RELEASE_REF") or None
        ref = sync.select_ref(release_home, repo_name, kind, release_ref)
        ref_sha = gh.git_rev_parse(ref, cwd=release_home)
        source = sync.GitSource(release_home, ref, ref_sha)

    # Guard the Kind tree exists in the source — same early error release-sync
    # raises. Without it a wheel/ref missing templates/<kind>/ would silently
    # materialize only commons/components/skills and still report success,
    # leaving an incomplete managed tree.
    if not source.exists(f"templates/{kind}"):
        raise sync.SyncError(
            f"release-core init --full: source '{source.label}' has no templates/{kind}/ tree"
        )

    caps = sync.resolve_capabilities(source, kind, sync_yaml_text=sync_yaml_text)
    sync.validate_capabilities(source, caps.names)
    return source, kind, caps.names


def _managed_paths_for_commit(mirror: sync.MirrorPlan, claude: sync.ClaudeDecision) -> list[str]:
    """The exact, repo-relative managed pathspecs a full sync produced or removed
    — the ONLY paths --commit stages (never `git add -A`).

    Covers: the whole .release/ build dir (one pathspec — git expands it to every
    materialized file and prunes removed ones); each symlink created or removed;
    each real-file copy written or removed; and CLAUDE.md when the orientation
    block was created/injected/refreshed. Deterministic order, de-duplicated.
    """
    paths: list[str] = [".release"]
    for s in mirror.symlinks_to_create:
        link, _, _ = s.partition(" -> ")
        paths.append(link)
    for link in mirror.symlinks_to_remove:
        # compute_mirror returns broken links as './…'; normalize for git.
        paths.append(link[2:] if link.startswith("./") else link)
    paths.extend(mirror.copies_to_write)
    paths.extend(mirror.copies_to_remove)
    if claude.action in ("create", "inject", "refresh"):
        paths.append(sync.CLAUDE_FILE)
    # de-dup, preserve first-seen order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _run_full_sync(
    repo_root: str, repo_name: str, *, dry_run: bool
) -> tuple[int, list[str], str, list[str]]:
    """Run the full release-sync pipeline (bundle- or clone-sourced) and apply it.

    Returns (changes, managed_paths, ref_label, conflicts):
      changes        — count of tree/mirror/claude changes (0 == already current).
      managed_paths  — the repo-relative pathspecs touched (for --commit staging).
      ref_label      — the source provenance (for the commit message).
      conflicts      — managed dests blocked by a real file/dir (symlink NOT
                       created); the caller surfaces these so a "no changes" run
                       that still has unresolved conflicts isn't reported clean.

    In --dry-run nothing is written/applied; the plan is still computed so the
    change count + paths + conflicts are reported. Mirrors release_sync.main's
    apply phase (atomic .release/ swap + release_sync._apply) so the result is
    byte-identical to a `release-sync` run for the same Kind.
    """
    from . import release_sync as _rs

    source, kind, caps_names = _resolve_full_source(repo_root, repo_name)
    plan = sync.build_plan(source, kind, caps_names, repo_root=repo_root)

    tmp_release = tempfile.mkdtemp(prefix=".release-build.", dir=repo_root)
    swapped = False
    try:
        sync.materialize(source, source.ref_sha, plan, tmp_release)
        file_diff, new_files = sync.diff_release(tmp_release, os.path.join(repo_root, ".release"))
        mirror = sync.compute_mirror(new_files, repo_root, tmp_release, migrate=False)
        claude = sync.decide_claude(repo_root, tmp_release)

        claude_change = 1 if claude.action in ("create", "inject", "refresh") else 0
        changes = (
            len(file_diff.added)
            + len(file_diff.modified)
            + len(file_diff.removed)
            + len(mirror.symlinks_to_create)
            + len(mirror.symlinks_to_remove)
            + len(mirror.migrated)
            + len(mirror.copies_to_write)
            + len(mirror.copies_to_remove)
            + claude_change
        )
        managed = _managed_paths_for_commit(mirror, claude)

        if dry_run:
            return changes, managed, source.ref_sha, list(mirror.conflicts)

        # Apply: atomic .release/ swap, then the mirror/CLAUDE.md apply phase.
        # _apply runs relative to cwd; init has already chdir'd into repo_root.
        release_dir = os.path.join(repo_root, ".release")
        if os.path.isdir(release_dir):
            shutil.rmtree(release_dir)
        os.rename(tmp_release, release_dir)
        swapped = True
        _rs._apply(mirror, claude)
    finally:
        if not swapped:
            shutil.rmtree(tmp_release, ignore_errors=True)

    return changes, managed, source.ref_sha, list(mirror.conflicts)


def _write_file(dest: str, src: str, *, exists: bool) -> None:
    """Copy ``src`` to ``dest``. On overwrite, replace atomically so a failed
    write never leaves a half-written managed file. Raises OSError on failure."""
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if exists:
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(dest) + ".tmp.", dir=parent or ".")
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            # mkstemp creates the temp file 0600; an atomic replace must not
            # silently tighten the managed file's permissions. Carry over the
            # mode the destination already had (which was itself created
            # umask-respecting on first materialize).
            shutil.copymode(dest, tmp)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    else:
        shutil.copyfile(src, dest)


def _commit_message(source_ref: dict[str, str]) -> str:
    """The deterministic auto-commit message. Includes the source ref/tip when
    init resolved one; otherwise omits the trailing ' to <ref>'."""
    ref = source_ref.get("ref")
    if ref:
        return f"chore(release): sync managed config to {ref}"
    return "chore(release): sync managed config"


def _full_commit_message(ref_label: str) -> str:
    """The deterministic auto-commit message for a full managed-tree sync. The
    managed tree is fully generated (no review needed), so SessionStart can
    auto-commit it; [skip ci] keeps the managed-only commit from spinning CI."""
    label = ref_label or "release"
    return f"chore(release): sync managed tree from {label} [skip ci]"


def _auto_commit(repo_root: str, written: list[str], message: str, *, push: bool) -> None:
    """Stage + commit ONLY ``written`` (paths relative to repo_root that init
    just created/overwrote/repaired), then optionally fast-forward push.

    Conservative and never-fail: any git error or unmet precondition prints a
    notice and returns without raising — init's own exit code is unaffected.
    NEVER stages anything beyond ``written`` (no `git add -A`); a user's other
    staged/unstaged work is left exactly as it was.
    """
    # Not a git repo, git unavailable, or an unborn branch (no commits yet) →
    # quiet no-op (init still succeeded). git_rev_parse_verify("HEAD") is the one
    # consistent probe across every layout (standard repo, submodule, worktree):
    # it is True iff a real HEAD commit exists. A pathspec-scoped commit cannot
    # run on an unborn branch (`fatal: cannot do partial commit during
    # bootstrap`), so gating on HEAD here also avoids that noisy failure.
    try:
        if not gh.git_rev_parse_verify("HEAD", cwd=repo_root):
            return
    except Exception:
        return

    try:
        gh.git_add(written, cwd=repo_root)
        # Commit ONLY the managed pathspecs. A pathspec-scoped commit ignores any
        # other staged changes, so a user's in-progress staging is never folded
        # in. If staging produced nothing to commit (e.g. the managed bytes were
        # already identical in the index/HEAD), git commit exits non-zero — caught
        # below as a benign skip, not a failure.
        gh.git_commit_paths(written, message, cwd=repo_root)
    except Exception as exc:  # ProcError or anything git surfaces
        print(
            f"release-core init: --commit skipped (could not commit managed config: {exc})",
            file=sys.stderr,
        )
        return

    print(f"committed {len(written)} managed file(s): {message}")

    if not push:
        return

    # --push guard: ONLY when on the default branch AND the tree is otherwise
    # clean (no non-managed changes — the managed paths are now committed, so a
    # clean check needs no exceptions). Otherwise the commit stays local.
    branch = gh.git_current_branch(cwd=repo_root)
    default = gh.git_default_branch(cwd=repo_root)
    if branch is None or default is None or branch != default:
        print(
            f"  push skipped: on '{branch or 'detached HEAD'}', not the default "
            f"branch ('{default or 'unknown'}') — commit kept local.",
            file=sys.stderr,
        )
        return
    if not gh.git_is_clean(cwd=repo_root):
        print(
            "  push skipped: working tree has other uncommitted changes — commit kept local.",
            file=sys.stderr,
        )
        return
    try:
        gh.git_push_ff(branch, cwd=repo_root)
    except Exception as exc:
        print(f"  push skipped: {exc}", file=sys.stderr)
        return
    print(f"  pushed to {branch}.")


def _main_full(
    repo_root: str, repo_name: str, *, dry_run: bool, no_commit: bool, push: bool
) -> int:
    """The `init --full` path: full managed-tree materialize + auto-commit-on-change.

    Runs the complete release-sync pipeline (build_plan + materialize +
    compute_mirror + apply), sourced from the wheel bundle by default (or a real
    $RELEASE_HOME clone), then — unless --no-commit/--dry-run — stages ONLY the
    managed paths and commits iff they actually changed. Idempotent: a second run
    with no upstream change computes zero changes → no commit.
    """
    try:
        changes, managed, ref_label, conflicts = _run_full_sync(
            repo_root, repo_name, dry_run=dry_run
        )
    except manifest.KindError:
        print(f"release-core init --full: could not detect kind of {repo_root}", file=sys.stderr)
        return 1
    except sync.SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except yamlio.YamlError as exc:
        print(f"release-core init --full: {exc}", file=sys.stderr)
        return 1

    # Surface conflicts (real file/dir at a managed location blocked a managed
    # symlink/copy). These mean the tree is NOT in steady state even when the
    # change count is 0 — never silently report "already current".
    if conflicts:
        print(
            "conflicts: a real file/dir blocks these managed paths (not applied) — "
            "remove them and re-run init --full:",
            file=sys.stderr,
        )
        for f in conflicts:
            print(f"  !file  {f}", file=sys.stderr)

    if dry_run:
        print(
            f"summary: {changes} managed-tree change(s), {len(conflicts)} conflict(s) "
            f"(dry-run, no writes){' from ' + ref_label if ref_label else ''}"
        )
        return 0

    if changes:
        suffix = f", {len(conflicts)} conflict(s)" if conflicts else ""
        print(
            f"summary: {changes} managed-tree change(s) applied from "
            f"{ref_label or 'release'}{suffix}."
        )
    elif conflicts:
        print(f"summary: 0 changes but {len(conflicts)} unresolved conflict(s) — see stderr.")
    else:
        print("summary: managed tree already current (no changes).")

    # AUTO-COMMIT ON CHANGE: commit only the managed paths, only when something
    # changed. --no-commit skips the commit (for tests/inspection). Conservative
    # and never-fail (see _auto_commit). On any branch — the managed tree is
    # generated, needs no review.
    if changes and not no_commit:
        _auto_commit(repo_root, managed, _full_commit_message(ref_label), push=push)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        values, _ = cli.parse(
            argv if argv is not None else [],
            [
                cli.Opt("--force"),
                cli.Opt("--dry-run"),
                cli.Opt("--commit"),
                cli.Opt("--push"),
                cli.Opt("--full"),
                cli.Opt("--no-commit"),
            ],
            doc=_usage_block(),
        )
    except SystemExit as exc:
        return int(exc.code or 0)

    force = bool(values["force"])
    dry_run = bool(values["dry-run"])
    push = bool(values["push"])
    full = bool(values["full"])
    no_commit = bool(values["no-commit"])
    # --push implies --commit (you cannot push a commit you never made).
    commit = bool(values["commit"]) or push

    # --push implies a commit; --no-commit suppresses it — the two contradict.
    # Reject the combo as bad usage rather than silently making --push a no-op.
    if push and no_commit:
        print("release-core init: --push and --no-commit are mutually exclusive", file=sys.stderr)
        return 64
    # --no-commit only governs --full's auto-commit-on-change; in plain init
    # (which doesn't commit unless --commit) it would be silently ignored — flag
    # the contract mismatch rather than hide it.
    if no_commit and not full:
        print("release-core init: --no-commit is only valid with --full", file=sys.stderr)
        return 64

    try:
        repo_root = gh.repo_root()
    except Exception:
        print("release-core init: not inside a git repo", file=sys.stderr)
        return 1
    # Resolve a relative RELEASE_HOME against the ORIGINAL cwd before we chdir
    # into the repo — otherwise a relative override (e.g. RELEASE_HOME=.) would
    # later resolve against repo_root and miss the release clone.
    release_home = os.environ.get("RELEASE_HOME")
    if release_home:
        os.environ["RELEASE_HOME"] = os.path.abspath(release_home)
    os.chdir(repo_root)
    repo_name = os.path.basename(repo_root)

    # --full: materialize the WHOLE managed tree (the full release-sync pipeline
    # sourced from the wheel bundle), then AUTO-COMMIT ON CHANGE. Flag-gated and
    # opt-in for now — plain `init` keeps the unchanged config-subset behavior
    # (the cutover to make --full the default is a later #476 step).
    if full:
        return _main_full(repo_root, repo_name, dry_run=dry_run, no_commit=no_commit, push=push)

    source_ref: dict[str, str] = {}
    try:
        sources = _materialize_config_sources(repo_root, repo_name, source_ref=source_ref)
    except manifest.KindError:
        print(f"release-core init: could not detect kind of {repo_root}", file=sys.stderr)
        return 1
    except sync.SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except yamlio.YamlError as exc:
        # Missing yq, a malformed manifest/.release-sync.yaml, or a
        # lefthook-fragment merge failure — caught at the CLI boundary and
        # mapped to a clean exit 1, exactly as release_sync does, never a
        # traceback escaping.
        print(f"release-core init: {exc}", file=sys.stderr)
        return 1

    created: list[str] = []
    overwritten: list[str] = []
    repaired: list[str] = []
    skipped: list[str] = []
    missing_source: list[str] = []

    for dest in CONFIG_FILES:
        src = sources.get(dest)
        if src is None:
            # The engine produced no such file for this Kind (e.g. a Kind whose
            # gate composes no lefthook). Report it; not a failure.
            missing_source.append(dest)
            continue
        # A dangling symlink (a leftover .release/-style link whose target is
        # gone) is broken config, not a consumer edit — lexists() reports it as
        # present, which would silently skip it and leave the repo effectively
        # uninitialized. Treat it as needing repair: materialize the real file
        # over it regardless of --force.
        is_broken_link = os.path.islink(dest) and not os.path.exists(dest)
        present = os.path.lexists(dest) and not is_broken_link
        if present and not force:
            skipped.append(dest)
            continue
        action_list = repaired if is_broken_link else (overwritten if present else created)
        if dry_run:
            action_list.append(dest)
            continue
        try:
            if is_broken_link:
                # Clear the dangling link so the create path writes a real file.
                os.unlink(dest)
            _write_file(dest, src, exists=present)
        except OSError as exc:
            # init's OWN writes must hard-fail (no best-effort swallowing).
            print(f"release-core init: failed to write {dest}: {exc}", file=sys.stderr)
            return 1
        action_list.append(dest)

    verb = "would " if dry_run else ""
    for f in created:
        print(f"  {verb}create  {f}")
    for f in overwritten:
        print(f"  {verb}force   {f} (overwritten)")
    for f in repaired:
        print(f"  {verb}repair  {f} (was a broken symlink)")
    for f in skipped:
        print(f"  skip    {f} (exists; --force to overwrite)")
    for f in missing_source:
        print(f"  absent  {f} (not produced for this kind)", file=sys.stderr)

    changed = len(created) + len(overwritten) + len(repaired)
    # The repaired clause is omitted when nothing was repaired so the common
    # summary line stays stable (created/overwritten/unchanged).
    repaired_clause = f", {len(repaired)} repaired" if repaired else ""
    print()
    print(
        f"summary: {len(created)} created, {len(overwritten)} overwritten"
        f"{repaired_clause}, {len(skipped)} unchanged"
        + (" (dry-run, no writes)" if dry_run else "")
    )
    if not dry_run:
        if missing_source:
            # The engine produced no source for some config files (an
            # incomplete materialization for this Kind), so the repo is NOT
            # fully initialized — don't claim it is.
            print(f"done. ({len(missing_source)} config file(s) had no source — see stderr)")
        elif not changed:
            print("done. (no changes — already initialized)")
        else:
            print("done.")

    # Auto-commit (opt-in). Only when changes were actually written, not in
    # dry-run. Idempotent by construction: changed == 0 → nothing to commit.
    if commit and not dry_run and changed:
        written = created + overwritten + repaired
        _auto_commit(repo_root, written, _commit_message(source_ref), push=push)
    return 0
