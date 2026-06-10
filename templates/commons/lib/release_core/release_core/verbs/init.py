"""init — materialize the managed tree into a consumer repo (the pull-model boot).

Usage:
  release-core init [--dry-run] [--no-commit] [--push]
  release-core init [--commit] [--force]    # tolerated legacy no-ops (see Flags)

`release-core init` is the pull-model seam: the SessionStart boot
(`install-release-core`) pulls the `release_core` wheel and runs `init`, so a
consumer self-updates its entire managed tree from the wheel bundle. Fleet
DISTRIBUTION is pull-only — there is no push mechanism (#476; `orc propagate`
was removed). The `--push` flag below is unrelated to distribution: it is an
opt-in plain `git push` of the LOCAL managed auto-commit.

A bare `release-core init` runs the COMPLETE release-sync pipeline (build_plan +
materialize + compute_mirror + apply) sourced from the wheel bundle — the whole
`.release/` build dir + every working-tree mirror (skills, configs,
per-Kind/Capability files, real-file workflow copies, the CLAUDE.md managed
block) — then AUTO-COMMITS ONLY the managed paths iff they actually changed.
Byte-identical result → no commit, so churn tracks release cadence, not session
count. This is what SessionStart runs; it is "release-sync sourced from the
wheel". This is the ONLY mode: the `--config-only` escape hatch (the pre-#476
config-subset behavior) was REMOVED in release#532 — post-WS3 it materialized
root configs whose gate referenced a `.release/` it never created, an
internally inconsistent path nothing on the fleet used.

Flags:
  --dry-run    compute + report the change count, write nothing.
  --no-commit  materialize but skip the auto-commit (tests / CI inspection —
               CI must never auto-commit the managed tree into a checkout).
  --push       fast-forward push the managed commit ONLY when on the repo's
               default branch with an otherwise-clean tree; on a feature
               branch (or a dirty tree) the commit stays local and rides the
               branch. Never force-pushes, never merges. Incompatible with
               --no-commit.

  --commit / --force are TOLERATED no-ops (warn + proceed), NOT errors: the
  deployed SessionStart resolver in a not-yet-migrated consumer still calls
  `init --commit`, and that stale call performs the first cutover pull —
  failing it would stall the fleet (the resolver can't update the tree that
  updates the resolver). The auto-commit is automatic and the materialize
  overwrites unconditionally, so both flags are redundant in this mode.

Auto-commit (the pull-model commit-hygiene seam): after a materialize, if (and
only if) managed content actually changed, init commits ONLY the exact managed
paths it wrote (never `git add -A`, never folding in a user's other staged or
unstaged work) with a deterministic message, on whatever branch is checked out
(the managed tree is generated — needs no review). NO `[skip ci]` in the
message: on a pushed branch it would block a required-checks ruleset forever.
Conservative by construction: no changes → no commit; --dry-run → no commit; an
unborn branch or any git error makes the commit step a quiet no-op.

Source resolution: the canonical content is composed from the wheel-bundled
templates (release_core/_bundled_templates/, staged at build time by
hatch_build.py) so init is self-contained — no release clone, no network. This
is the DEFAULT and the only path a pip-installed consumer ever takes.

A `$RELEASE_HOME` git checkout, when explicitly present (release-dev only),
OVERRIDES the bundle: init then composes from live templates via the full
release-sync engine (sync.build_plan + sync.materialize) at $RELEASE_REF, the
same git-clone contract release-sync used. In an editable/source checkout the
bundle is absent (a gitignored build artifact), so $RELEASE_HOME is required
there; a fresh wheel install needs neither.

Exit codes:
  0  — done (created/refreshed, or a clean no-op)
  1  — fatal error (cannot resolve source, or a write failed)
  64 — bad usage
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile

from .. import cli, gh, manifest, sync, yamlio

USAGE = __doc__ or ""


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
    materialize reads through this.
    """
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # release_core/
    root = os.path.join(here, "_bundled_templates")
    return root if os.path.isdir(os.path.join(root, "templates")) else None


def _read_sync_yaml(repo_root: str) -> str | None:
    sync_yaml = os.path.join(repo_root, ".release-sync.yaml")
    if os.path.isfile(sync_yaml):
        with open(sync_yaml, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return None


# ── Full materialize: the whole managed tree, from the bundle ────────────────
#
# The full materialize is the SAME engine pipeline the retired release-sync verb
# ran (build_plan + materialize + diff + compute_mirror + decide_claude + apply),
# now sourced from the wheel bundle and driven by init: BundleSource by default,
# or GitSource when a
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
                "release-core init: no bundled templates and "
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
            f"release-core init: source '{source.label}' has no templates/{kind}/ tree"
        )

    caps = sync.resolve_capabilities(source, kind, sync_yaml_text=sync_yaml_text)
    sync.validate_capabilities(source, caps.names)
    return source, kind, caps.names


def _managed_paths_for_commit(mirror: sync.MirrorPlan, claude: sync.ClaudeDecision) -> list[str]:
    """The exact, repo-relative managed MIRROR pathspecs a full sync produced or
    removed — the ONLY paths --commit stages (never `git add -A`).

    Covers: each symlink removed (swept from disk — the deletion must commit);
    each real-file copy written or removed; each retired tombstoned file removed
    (WS6, release#527); and CLAUDE.md when the orientation block was
    created/injected/refreshed. Deterministic order, de-duplicated.

    Notably NOT the created symlink mirrors: since WS7 (release#528) they are
    EPHEMERAL — materialized every init, excluded via .git/info/exclude, never
    tracked. Staging one (git add -f) would re-track it; a pre-WS7 seed's
    committed mirrors are untracked separately in :func:`_auto_commit`.

    And NOT `.release/`: since WS4 (release#521) the build dir is gitignored +
    ephemeral, never committed. A previously-committed `.release/` is untracked
    separately in :func:`_auto_commit` (the one-time consumer migration).
    """
    paths: list[str] = []
    for link in mirror.symlinks_to_remove:
        # compute_mirror returns broken links as './…'; normalize for git.
        paths.append(link[2:] if link.startswith("./") else link)
    paths.extend(mirror.copies_to_write)
    paths.extend(mirror.copies_to_remove)
    paths.extend(mirror.retired_to_remove)
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
    change count + paths + conflicts are reported. The apply phase (atomic
    .release/ swap + :func:`_apply_mirror`) composes the same managed tree the
    retired ``release-sync`` verb produced for the same Kind.
    """
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
            + len(mirror.retired_to_remove)
            + claude_change
        )
        managed = _managed_paths_for_commit(mirror, claude)

        if dry_run:
            return changes, managed, source.ref_sha, list(mirror.conflicts)

        # Apply: atomic .release/ swap, then the mirror/CLAUDE.md apply phase.
        # _apply_mirror runs relative to cwd; init has already chdir'd into repo_root.
        release_dir = os.path.join(repo_root, ".release")
        if os.path.isdir(release_dir):
            shutil.rmtree(release_dir)
        os.rename(tmp_release, release_dir)
        swapped = True
        _apply_mirror(mirror, claude)
    finally:
        if not swapped:
            shutil.rmtree(tmp_release, ignore_errors=True)

    return changes, managed, source.ref_sha, list(mirror.conflicts)


def _apply_mirror(mirror: sync.MirrorPlan, claude: sync.ClaudeDecision) -> None:
    """The apply phase: --migrate removals, symlink create/remove, managed-copy
    write/remove, and the CLAUDE.md write. Runs relative to cwd (init has chdir'd
    into the repo root). Formerly ``release_sync._apply`` — relocated here when the
    standalone sync verb was retired (WS4, release#521); init is its sole caller."""
    # If --migrate, delete real files at managed locations first.
    for f in mirror.migrated:
        _rm_f(f)

    # Create / update symlinks.
    for s in mirror.symlinks_to_create:
        link, _, target = s.partition(" -> ")
        d = os.path.dirname(link)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.islink(link):
            os.remove(link)
        os.symlink(target, link)

    # Remove broken symlinks (paths are './…' relative to repo root), then prune
    # the now-empty parent dirs a swept skill leaves behind (.claude/skills/<name>/
    # holds nothing but its mirrors, so a retired skill would otherwise linger as
    # an empty husk). os.rmdir refuses a non-empty dir — that's the stop condition.
    for link in mirror.symlinks_to_remove:
        os.remove(link)
        d = os.path.dirname(link)
        while d and d not in (".", "./"):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)

    # Write managed copies (real files for paths GH can't dereference, plus the
    # bootstrap files that must exist on a fresh clone — sync.BOOTSTRAP_REAL_FILES).
    # ATOMIC replace (temp + os.replace), never truncate-in-place: the bootstrap
    # set includes RUNNING scripts — bin/install-release-core triggers this very
    # init, so an in-place truncation would yank the script out from under its
    # own execution; a rename leaves the running copy its old inode (WS5, #526).
    for f in mirror.copies_to_write:
        d = os.path.dirname(f)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.islink(f):
            os.remove(f)
        src = os.path.join(".release", f)
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(f) + ".tmp.", dir=d or ".")
        try:
            with os.fdopen(fd, "wb") as dfh:
                if f.endswith((".yml", ".yaml")):
                    with open(src, "rb") as sfh:
                        dfh.write((sync.MANAGED_MARKER + "\n").encode("utf-8"))
                        dfh.write(sfh.read())
                else:
                    with open(src, "rb") as sfh:
                        shutil.copyfileobj(sfh, dfh)
            # Permissions: mkstemp creates 0600 — set the normal umask-style mode,
            # carrying the executable bit over from the source.
            mode = 0o755 if os.access(src, os.X_OK) else 0o644
            os.chmod(tmp, mode)
            os.replace(tmp, f)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # Remove stale managed copies.
    for f in mirror.copies_to_remove:
        os.remove(f)

    # Remove retired tombstoned files (WS6, release#527) — provenance-verified
    # in sync._find_retired_files, so only release's own retired copies land here.
    for f in mirror.retired_to_remove:
        _rm_f(f)

    # WS7 (release#528): the symlink mirrors are EPHEMERAL — materialized above,
    # never tracked. Keep `git status` clean by listing them in the local
    # .git/info/exclude (NOT the consumer's .gitignore: zero tracked footprint,
    # and info/exclude is per-clone, recomposed by every init just like the
    # mirrors themselves).
    _write_mirror_excludes(mirror.mirror_dests)

    # Write the consumer CLAUDE.md orientation block.
    if claude.action in ("create", "inject", "refresh"):
        # Atomic same-filesystem replace via a sibling temp file.
        assert claude.desired is not None
        fd, tmp = tempfile.mkstemp(prefix=sync.CLAUDE_FILE + ".tmp.", dir=".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(claude.desired)
            os.chmod(tmp, 0o644)
            os.replace(tmp, sync.CLAUDE_FILE)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def _rm_f(path: str) -> None:
    """`rm -rf` — remove if present (file, symlink, or directory), ignore absence
    but surface real errors (permission/IO), like `rm -f` does for a file.

    A pre-existing managed dest is usually a real file (e.g. a stale hand-copied
    .claude/skills/<name>/SKILL.md). It can also be a real directory; remove that
    too so the managed symlink can take its place.

    Absence (FileNotFoundError) is ignored — matching `rm -f` — including the
    TOCTOU window where the dir vanishes between the isdir() check and the
    rmtree (a concurrent/CI race). But a real failure (permission/IO) must
    propagate rather than be silently swallowed (which would leave the path in
    place and make the later os.symlink fail with a confusing FileExistsError),
    so we do NOT pass ignore_errors=True; instead we suppress ONLY
    FileNotFoundError."""
    with contextlib.suppress(FileNotFoundError):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def _full_commit_message(ref_label: str) -> str:
    """The deterministic auto-commit message for a full managed-tree sync. The
    managed tree is fully generated (no review needed), so SessionStart can
    auto-commit it.

    NO `[skip ci]`: when the managed commit is the head of a pushed branch — a
    consumer's first-migration PR, or any feature branch where it lands last —
    `[skip ci]` makes GitHub skip ALL workflows for that push, so a
    required-status-checks ruleset can never be satisfied and the PR is BLOCKED
    forever. Managed changes track release cadence (byte-identical → no commit),
    so letting CI run on them is cheap and is the only way they reach a protected
    branch."""
    label = ref_label or "release"
    return f"chore(release): sync managed tree from {label}"


def _commit_untracking(
    repo_root: str,
    commit_paths: list[str],
    message: str,
    *,
    stash_release: bool,
    mirror_links: list[tuple[str, str]],
) -> None:
    """Pathspec-commit the managed paths AND untrack the ephemeral content a
    pre-migration seed committed: the `.release/` build dir (WS4, release#521)
    and the symlink mirrors (WS7, release#528).

    The wrinkle: a pathspec commit (`git commit -- <paths>`) does a PARTIAL commit
    that re-reads the WORKING TREE for the listed paths. The recomposed content is
    still on disk (gitignored / excluded), so a naive pathspec commit would
    *resurrect* every tracked path that survives recomposition instead of
    recording its removal. So we take the ephemeral content off-disk for the
    duration of the commit — `.release/` via an O(1) dir rename, each tracked
    mirror symlink via remove-and-recreate (`mirror_links` carries (path, target),
    and a symlink is recreated byte-identically from its target) — and restore it
    in `finally`, so the ephemeral tree stays live for the session.
    """
    release_dir = os.path.join(repo_root, ".release")
    stash = os.path.join(repo_root, ".release.untrack-commit.tmp")
    moved = False
    removed_links: list[tuple[str, str]] = []
    try:
        if stash_release and os.path.exists(release_dir):
            # A leftover stash from a previously-interrupted run would block the
            # rename; clear it first (it is never the live tree). _rm_f handles
            # a file/symlink/dir alike (rm -f semantics) and lets a real removal
            # failure surface — caught by _auto_commit as a skipped commit — rather
            # than silently leaving a non-dir that breaks the rename.
            if os.path.lexists(stash):
                _rm_f(stash)
            os.rename(release_dir, stash)
            moved = True
        for rel, target in mirror_links:
            full = os.path.join(repo_root, rel)
            if os.path.islink(full):
                os.remove(full)
                removed_links.append((full, target))
        gh.git_commit_paths(commit_paths, message, cwd=repo_root)
    finally:
        if moved:
            os.rename(stash, release_dir)
        for full, target in removed_links:
            if not os.path.lexists(full):
                os.symlink(target, full)


def _tracked_release_symlinks(repo_root: str) -> list[tuple[str, str]]:
    """Tracked paths that on disk are symlinks pointing into `.release/` — i.e.
    managed mirrors a pre-WS7 seed committed (only release's mirrors ever point
    there). Returns (repo-relative path, symlink target) pairs; the target rides
    along so :func:`_commit_untracking` can recreate the link after the
    untracking commit. Checked on DISK rather than by index mode so a real file
    the apply phase just migrated to a symlink (its index entry still 100644) is
    caught too."""
    try:
        out = gh.git(["ls-files", "-z"], cwd=repo_root)
    except Exception:
        return []
    links: list[tuple[str, str]] = []
    for rel in out.split("\0"):
        if not rel:
            continue
        full = os.path.join(repo_root, rel)
        if os.path.islink(full) and ".release/" in os.readlink(full):
            links.append((rel, os.readlink(full)))
    return links


_EXCLUDE_BEGIN = "# >>> release-core managed mirrors (rewritten by every init) >>>"
_EXCLUDE_END = "# <<< release-core managed mirrors <<<"


def _write_mirror_excludes(dests: set[str]) -> None:
    """Rewrite the managed block in `.git/info/exclude` listing every ephemeral
    mirror dest (WS7, release#528), so the untracked symlinks never show up in
    `git status`. Runs relative to cwd (init has chdir'd into the repo root).
    info/exclude — not the consumer's .gitignore — because the point is ZERO
    tracked footprint; it is per-clone state recomposed by every init, exactly
    like the mirrors it covers. Idempotent: the block is replaced wholesale.
    Quietly a no-op outside a git work tree."""
    try:
        ex_path = gh.git(["rev-parse", "--git-path", "info/exclude"]).strip()
    except Exception:
        return
    try:
        with open(ex_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    if _EXCLUDE_BEGIN in lines and _EXCLUDE_END in lines:
        i, j = lines.index(_EXCLUDE_BEGIN), lines.index(_EXCLUDE_END)
        if i < j:
            del lines[i : j + 1]
    while lines and not lines[-1].strip():
        lines.pop()
    if dests:
        if lines:
            lines.append("")
        lines.append(_EXCLUDE_BEGIN)
        # Leading "/" roots each pattern at the repo top level, matching the
        # repo-relative dests exactly (never a same-named nested path).
        lines.extend(f"/{d}" for d in sorted(dests))
        lines.append(_EXCLUDE_END)
    content = "\n".join(lines) + "\n" if lines else ""
    d = os.path.dirname(ex_path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="exclude.tmp.", dir=d or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, ex_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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
        # WS4 migration (release#521): untrack a previously-committed `.release/`.
        # The build dir is now gitignored + ephemeral. Include `.release` in the
        # commit pathspec ONLY when it was actually tracked — a bare
        # `git commit -- .release` errors with "pathspec did not match" on a fresh
        # consumer where nothing under it is tracked.
        release_tracked = gh.git_path_tracked(".release", cwd=repo_root)
        # WS7 migration (release#528): untrack the symlink mirrors a pre-WS7 seed
        # committed — they are ephemeral now (materialized + excluded every init).
        mirror_links = _tracked_release_symlinks(repo_root)
        # A `written` path can be a swept EPHEMERAL symlink: gone from disk and
        # never tracked, so `git add` would error "pathspec did not match" and
        # void the whole commit. Stage only what git can see.
        stageable = [
            p
            for p in written
            if os.path.lexists(os.path.join(repo_root, p)) or gh.git_path_tracked(p, cwd=repo_root)
        ]
        commit_paths = stageable + [rel for rel, _ in mirror_links]
        if release_tracked:
            commit_paths.append(".release")
        if not commit_paths:
            # Nothing managed to commit — e.g. the only delta was inside the now
            # gitignored .release/ tree (ephemeral, never committable). Skip rather
            # than run a pathspec-less `git commit` that would fold in unrelated work.
            return
        # force=True: managed real-file paths are release-owned and must be tracked
        # even if the consumer's .gitignore covers one (e.g. `.claude/` shadowing
        # the managed `.claude/skills/`) — otherwise the migration commit silently
        # fails on the ignored path. NEVER `.release/` or a symlink mirror: those
        # are ephemeral on purpose and are NOT in `written`, so force-add can't
        # re-track them.
        gh.git_add(stageable, cwd=repo_root, force=True)
        # Commit ONLY the managed pathspecs. A pathspec-scoped commit ignores any
        # other staged changes, so a user's in-progress staging is never folded in.
        if release_tracked or mirror_links:
            _commit_untracking(
                repo_root,
                commit_paths,
                message,
                stash_release=release_tracked,
                mirror_links=mirror_links,
            )
        else:
            gh.git_commit_paths(commit_paths, message, cwd=repo_root)
    except Exception as exc:  # ProcError or anything git surfaces
        print(
            f"release-core init: --commit skipped (could not commit managed config: {exc})",
            file=sys.stderr,
        )
        return

    # Report the REAL number of files in the commit, not len(written): `written`
    # is a list of pathspecs (".release" is ONE entry that git expands to every
    # materialized file), so len(written) badly under-counts a full sync.
    n = gh.git_commit_file_count(cwd=repo_root) or len(written)
    print(f"committed {n} managed file(s): {message}")

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
    """The default init path: full managed-tree materialize + auto-commit-on-change.

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
        print(f"release-core init: could not detect kind of {repo_root}", file=sys.stderr)
        return 1
    except sync.SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except yamlio.YamlError as exc:
        print(f"release-core init: {exc}", file=sys.stderr)
        return 1

    # Surface conflicts (real file/dir at a managed location blocked a managed
    # symlink/copy). These mean the tree is NOT in steady state even when the
    # change count is 0 — never silently report "already current".
    if conflicts:
        print(
            "conflicts: a real file/dir blocks these managed paths (not applied) — "
            "remove them and re-run release-core init:",
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

    # One-time migrations that are commit-worthy even at changes == 0 (the
    # composed tree can be byte-identical while the INDEX still carries ephemeral
    # content) — detect independently so the migration commit still fires:
    # a previously-committed `.release/` (WS4, release#521) and previously-
    # committed symlink mirrors (WS7, release#528).
    release_was_tracked = gh.git_path_tracked(".release", cwd=repo_root)
    mirrors_were_tracked = bool(_tracked_release_symlinks(repo_root))

    if changes:
        suffix = f", {len(conflicts)} conflict(s)" if conflicts else ""
        print(
            f"summary: {changes} managed-tree change(s) applied from "
            f"{ref_label or 'release'}{suffix}."
        )
    elif conflicts:
        print(f"summary: 0 changes but {len(conflicts)} unresolved conflict(s) — see stderr.")
    elif release_was_tracked:
        print("summary: managed tree already current; untracking committed .release/ (WS4).")
    elif mirrors_were_tracked:
        print("summary: managed tree already current; untracking committed mirrors (WS7).")
    else:
        print("summary: managed tree already current (no changes).")

    # AUTO-COMMIT: commit the managed mirror paths when something changed, OR when
    # previously-committed ephemeral content still needs untracking (the WS4/WS7
    # migrations, commit-worthy even at changes == 0). --no-commit skips the commit
    # (for tests/inspection). Conservative and never-fail (see _auto_commit). On
    # any branch — the managed tree is generated, needs no review.
    if (changes or release_was_tracked or mirrors_were_tracked) and not no_commit:
        _auto_commit(repo_root, managed, _full_commit_message(ref_label), push=push)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        values, _ = cli.parse(
            argv if argv is not None else [],
            [
                # --force / --commit are TOLERATED legacy no-ops (see the
                # docstring: a stale pre-migration SessionStart resolver still
                # passes --commit, and rejecting it would stall the fleet's
                # first cutover pull). --config-only / --full were REMOVED in
                # release#532 — an unknown flag is now bad usage (exit 64).
                cli.Opt("--force"),
                cli.Opt("--dry-run"),
                cli.Opt("--commit"),
                cli.Opt("--push"),
                cli.Opt("--no-commit"),
            ],
            doc=_usage_block(),
        )
    except SystemExit as exc:
        return int(exc.code or 0)

    force = bool(values["force"])
    dry_run = bool(values["dry-run"])
    push = bool(values["push"])
    no_commit = bool(values["no-commit"])

    # --push implies a commit; --no-commit suppresses it — the two contradict.
    # Reject the combo as bad usage rather than silently making --push a no-op.
    if push and no_commit:
        print("release-core init: --push and --no-commit are mutually exclusive", file=sys.stderr)
        return 64
    # The commit is automatic (auto-commit-on-change; --no-commit to skip) and
    # the materialize overwrites unconditionally — so an explicit --commit is
    # redundant and --force a no-op. TOLERATE them (warn, don't fail): the
    # deployed SessionStart resolver in not-yet-migrated consumers still calls
    # `release-core init --commit`, and that stale invocation is exactly what
    # performs the FIRST cutover pull. Failing it would stall the whole fleet —
    # the resolver can't materialize the new tree that would in turn update the
    # resolver (bootstrap chicken-and-egg). After the first successful pull the
    # managed resolver no longer passes --commit, so the warning self-clears.
    if values["commit"] or force:
        print(
            "release-core init: --commit/--force are redundant "
            "(init auto-commits managed changes) — ignoring",
            file=sys.stderr,
        )

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

    return _main_full(repo_root, repo_name, dry_run=dry_run, no_commit=no_commit, push=push)
