"""init — install the managed files into a consumer repo (the pull-model boot).

Usage:
  release-core init [--dry-run] [--no-commit] [--push]
  release-core init [--commit] [--force]    # tolerated legacy no-ops (see Flags)

`release-core init` is the pull-model seam: the SessionStart boot
(`install-release-core`) pulls the `release_core` wheel and runs `init`, so a
consumer self-updates its managed files from the wheel bundle. Fleet
DISTRIBUTION is pull-only — there is no push mechanism (#476; `orc propagate`
was removed). The `--push` flag below is unrelated to distribution: it is an
opt-in plain `git push` of the LOCAL managed auto-commit.

A bare `release-core init` installs the files from the wheel bundle (`install_plan` →
`install_tree` → `diff_release` → `compute_mirror` → `decide_claude` → apply):
the `.release/` temp dir + every working-tree mirror (skills, configs,
per-Kind/Component files, real-file workflow copies, the CLAUDE.md header
block) — then AUTO-COMMITS ONLY the managed paths iff they actually changed.
Byte-identical result → no commit, so churn tracks release cadence, not session
count. This is what SessionStart runs, from the wheel. This is the ONLY mode:
the `--config-only` escape hatch (the pre-#476 config-subset behavior) was
REMOVED in release#532 — post-WS3 it wrote root configs whose gate referenced a
`.release/` it never created, an internally inconsistent path nothing on the
fleet used.

Provisioning (WS5/E, #762): after installing the managed tree, init PROVISIONS the
dev env — arms the gate toolset (FIRST, so the gate + hook have it), inits git
submodules, wires the pre-commit hook, and runs the per-repo
``app-bin/post-setup-hook.sh`` LAST. ``--cloud`` adds the cloud-snapshot steps
(tag fetch, dep caches, NSS cert import). This is the wheel-carried port of
``setup-dev-env.sh``'s provisioning; through the migration window the shell still
runs the same steps too (additive + safe — every step is idempotent + best-effort,
so it never fights the shell or breaks the boot). ``--no-provision`` skips it (the
arm-gate / test path that provisions separately).

Flags:
  --dry-run    compute + report the change count, write nothing.
  --cloud      also run the cloud-only provisioning steps (tag fetch, dep caches,
               NSS cert import) — set by the cloud SessionStart boot.
  --no-provision
               install the managed tree but skip the dev-env provisioning
               (toolset/hooks/caches/cert/submodules + post-setup hook). For CI
               (arm-gate runs `gate --provision` itself) and tests.
  --no-commit  install but skip the auto-commit (tests / CI inspection —
               CI must never auto-commit the managed files into a checkout).
  --push       fast-forward push the managed commit ONLY when on the repo's
               default branch with an otherwise-clean tree; on a feature
               branch (or a dirty tree) the commit stays local and rides the
               branch. Never force-pushes, never merges. Incompatible with
               --no-commit.

  --commit / --force are TOLERATED no-ops (warn + proceed), NOT errors: the
  deployed SessionStart resolver in a not-yet-migrated consumer still calls
  `init --commit`, and that stale call performs the first cutover pull —
  failing it would stall the fleet (the resolver can't update the managed files
  that update the resolver). The auto-commit is automatic and the install
  overwrites unconditionally, so both flags are redundant in this mode.

Auto-commit (the pull-model commit-hygiene seam): after an install, if (and
only if) managed content actually changed, init commits ONLY the exact managed
paths it wrote (never `git add -A`, never folding in a user's other staged or
unstaged work) with a deterministic message, on whatever branch is checked out
(the managed files are generated — needs no review). NO `[skip ci]` in the
message: on a pushed branch it would block a required-checks ruleset forever.
Conservative by construction: no changes → no commit; --dry-run → no commit; an
unborn branch or any git error makes the commit step a quiet no-op.

Source resolution: the content is installed from the wheel-bundled
templates (release_core/_bundled_templates/, staged at build time by
hatch_build.py) so init is self-contained — no release clone, no network. This
is the DEFAULT and the only path a pip-installed consumer ever takes.

A `$RELEASE_HOME` git checkout, when explicitly present (release-dev only),
OVERRIDES the bundle: init then installs from live templates via
sync.install_plan + sync.install_tree at $RELEASE_REF, the
same git-clone contract those steps use. In an editable/source checkout the
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

from .. import cli, contract, gh, manifest, sync, yamlio

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
    build reads through this.
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


# ── Full install: all managed files, from the bundle ─────────────────────────
#
# It runs the SAME steps init always runs
# (install_plan + install_tree + diff + compute_mirror + decide_claude + apply),
# sourced from the wheel bundle: BundleSource by default,
# or GitSource when a
# real $RELEASE_HOME clone is present (release-dev override, mirroring how the
# config path prefers $RELEASE_HOME over the bundle). It writes the
# `.release/` temp dir plus all working-tree mirrors (symlinks, real-file
# copies, the CLAUDE.md header block).


def _resolve_full_source(repo_root: str, repo_name: str) -> tuple[sync.Source, str, list[str]]:
    """Pick the sync Source for a full install and resolve Kind + components.

    Returns (source, kind, component_names). DEFAULT: BundleSource over the
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

        # The wheel version is tag-stamped at build (release#758), so this
        # ref_sha label already says which release line seeded the tree. (WS8
        # #765 removed the separate release-source.tag stamp the boot resolver
        # used to write — the index install no longer stamps a sidecar.)
        source: sync.Source = sync.BundleSource(
            bundle_root,
            ref_sha=f"release-core {_v}",
        )
    else:
        assert release_home is not None
        release_ref = os.environ.get("RELEASE_REF") or None
        ref = sync.select_ref(release_home, repo_name, kind, release_ref)
        ref_sha = gh.git_rev_parse(ref, cwd=release_home)
        source = sync.GitSource(release_home, ref, ref_sha)

    # Guard the Kind tree exists in the source — same early error the install step
    # raises. Without it a wheel/ref missing templates/<kind>/ would silently
    # install only commons/components/skills and still report success,
    # leaving an incomplete set of managed files.
    if not source.exists(f"templates/{kind}"):
        raise sync.SyncError(
            f"release-core init: source '{source.label}' has no templates/{kind}/ tree"
        )

    caps = sync.resolve_capabilities(source, kind, sync_yaml_text=sync_yaml_text)
    sync.validate_capabilities(source, caps.names)
    return source, kind, caps.names


def _claude_md_clean(repo_root: str) -> bool:
    """True when CLAUDE.md has no uncommitted change (or is absent).

    An ``insert`` migration rewrites an EXISTING CLAUDE.md; committing it folds in
    any concurrent consumer edit. Only commit the migration when the file was clean
    (the normal first-pull moment); a mid-edit consumer keeps their dirty CLAUDE.md
    and commits the @import themselves. Conservative on any git error: don't commit."""
    try:
        out = gh.git(["status", "--porcelain", "--", sync.CLAUDE_FILE], cwd=repo_root)
    except Exception:
        return False
    return not out.strip()


def _managed_paths_for_commit(
    mirror: sync.MirrorPlan,
    claude: sync.ClaudeDecision,
    seeded_major: str | None = None,
    claude_insert_committable: bool = True,
) -> list[str]:
    """The exact, repo-relative managed MIRROR pathspecs a full install produced or
    removed — the ONLY paths --commit stages (never `git add -A`).

    Covers: each symlink removed (swept from disk — the deletion must commit);
    each real-file copy written or removed; each retired file removed
    (WS6, release#527); the managed `.claude/IMPORTANT-RELEASE.md` target when
    (re)written (WS4, #761); and the seeded `.release.major.txt` when init created
    it (WS3, #760). Deterministic order, de-duplicated.

    ``CLAUDE.md`` (WS4, #761) rides the commit ONLY on the one-time managed
    insertion (`create` or `insert` — see below); after that it is 100%
    consumer-owned and is NEVER staged again, so the managed auto-commit can't fold
    the consumer's unrelated edits. Only the managed TARGET file (`.claude/
    IMPORTANT-RELEASE.md`) rides every managed commit.

    Notably NOT the created symlink mirrors: since WS7 (release#528) they are
    EPHEMERAL — built every init, excluded via .git/info/exclude, never
    tracked. Staging one (git add -f) would re-track it.

    And NOT `.release/`: since WS4 (release#521) the temp dir is gitignored +
    ephemeral, never committed.
    """
    paths: list[str] = []
    for link in mirror.symlinks_to_remove:
        # compute_mirror returns broken links as './…'; normalize for git.
        paths.append(link[2:] if link.startswith("./") else link)
    paths.extend(mirror.copies_to_write)
    paths.extend(mirror.copies_to_remove)
    paths.extend(mirror.retired_to_remove)
    if claude.target_action == "write":
        paths.append(sync.CLAUDE_IMPORT_TARGET)
    # CLAUDE.md rides the commit on the ONE-TIME managed insertion: a fresh CREATE
    # (no prior file, nothing to fold) ALWAYS; an INSERT into an existing one (strip
    # the pre-WS4 block, prepend the @import) ONLY when it had no uncommitted edits
    # (``claude_insert_committable``) — so the managed commit can't fold a consumer's
    # concurrent CLAUDE.md edits. The clean case MUST commit, else the @import sits
    # uncommitted and the next init re-stages it; a dirty-CLAUDE.md insert is written
    # to disk but left for the consumer to commit with their own edits. After the
    # insertion import_action is "none" → CLAUDE.md is never staged again.
    if claude.import_action == "create" or (
        claude.import_action == "insert" and claude_insert_committable
    ):
        paths.append(sync.CLAUDE_FILE)
    if seeded_major:
        paths.append(seeded_major)
    # de-dup, preserve first-seen order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _source_label(source: sync.Source) -> str:
    """The provenance label for the auto-commit message + summary.

    The source's ``ref_sha`` IS the provenance: the tag-stamped wheel version
    for a BundleSource ("release-core 3.1.0", release#758) or the resolved git
    SHA for a GitSource. (WS8 #765 removed the separate release-source.tag stamp
    the boot resolver used to write, so there is no longer a distinct release-tag
    label channel.)
    """
    return source.ref_sha


def _run_full_sync(
    repo_root: str, repo_name: str, *, dry_run: bool
) -> tuple[int, list[str], str, list[str]]:
    """Run the full install (bundle- or clone-sourced) and apply it.

    Returns (changes, managed_paths, ref_label, conflicts):
      changes        — count of file/mirror/claude changes (0 == already current).
      managed_paths  — the repo-relative pathspecs touched (for --commit staging).
      ref_label      — the source provenance (for the commit message).
      conflicts      — managed dests blocked by a real file/dir (symlink NOT
                       created); the caller surfaces these so a "no changes" run
                       that still has unresolved conflicts isn't reported clean.

    In --dry-run nothing is written/applied; the plan is still computed so the
    change count + paths + conflicts are reported. The apply phase (atomic
    `.release/` swap + :func:`_apply_mirror`) installs all managed files
    for the detected Kind.
    """
    source, kind, caps_names = _resolve_full_source(repo_root, repo_name)
    plan = sync.install_plan(source, kind, caps_names, repo_root=repo_root)

    tmp_release = tempfile.mkdtemp(prefix=".release-build.", dir=repo_root)
    swapped = False
    try:
        sync.install_tree(source, source.ref_sha, plan, tmp_release)
        file_diff, new_files = sync.diff_release(tmp_release, os.path.join(repo_root, ".release"))
        mirror = sync.compute_mirror(new_files, repo_root, tmp_release, migrate=False)
        claude = sync.decide_claude(repo_root, tmp_release)
        # Capture CLAUDE.md cleanliness BEFORE _apply_mirror rewrites it: an `insert`
        # migration is only committed when the file had no uncommitted edits, so the
        # managed commit never folds in a consumer's concurrent CLAUDE.md work.
        claude_insert_committable = _claude_md_clean(repo_root)

        # Consumer-side tripwire (#581): warn — never fail — when a workflow job
        # references one of the ephemeral mirror dests this install owns without
        # installing the `.release/` temp dir first. Every init runs it (a consumer
        # can add a bad job any day), dry-run included (read-only scan).
        _warn_unbuilt_workflow_refs(repo_root, mirror.mirror_dests)

        # WS3 (#760): seed `.release.major.txt` from the consumer's `@vN` pins when
        # absent. Read-only probe here (would-seed?); the WRITE happens after the
        # dry-run gate so --dry-run never touches the tree. ``seeded_major`` is the
        # repo-relative path when init created the file (→ staged + committed).
        would_seed = (
            sync.read_release_major(repo_root) is None
            and sync.derive_caller_major(repo_root) is not None
        )

        # The CLAUDE.md @import counts as a change when the managed TARGET is
        # (re)written OR the import line is created/inserted into CLAUDE.md.
        claude_change = 1 if claude.target_action == "write" else 0
        if claude.import_action in ("create", "insert"):
            claude_change += 1
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
            + (1 if would_seed else 0)
        )

        if dry_run:
            managed = _managed_paths_for_commit(
                mirror,
                claude,
                sync.RELEASE_MAJOR_FILE if would_seed else None,
                claude_insert_committable,
            )
            return changes, managed, _source_label(source), list(mirror.conflicts)

        # Apply: atomic `.release/` swap, then the mirror/CLAUDE.md apply phase.
        # _apply_mirror runs relative to cwd; init has already chdir'd into repo_root.
        release_dir = os.path.join(repo_root, ".release")
        if os.path.isdir(release_dir):
            shutil.rmtree(release_dir)
        os.rename(tmp_release, release_dir)
        swapped = True
        _apply_mirror(mirror, claude)
        # WS3 (#760): actually SEED `.release.major.txt` now (after the swap, in the
        # repo root). Returns the path iff it wrote — a present file is never
        # overwritten. Staged + committed via ``managed``.
        seeded_major = sync.seed_release_major(repo_root)
        managed = _managed_paths_for_commit(mirror, claude, seeded_major, claude_insert_committable)
    finally:
        if not swapped:
            shutil.rmtree(tmp_release, ignore_errors=True)

    return changes, managed, _source_label(source), list(mirror.conflicts)


def _warn_unbuilt_workflow_refs(repo_root: str, mirror_dests: set[str]) -> None:
    """The seed-time / every-session tripwire (#581, the supage#163 class).

    Post-WS7 the mirror dests (``bin/check*``, ``lib/release_core/``, …) are
    EPHEMERAL — untracked, reinstalled by every init — so a consumer-authored
    workflow job that invokes one WITHOUT installing the `.release/` temp dir
    first goes red on a fresh CI checkout ("No such file or directory", exit 127).
    Scan the consumer's ``.github/workflows/**`` with the SAME assumption-lint
    scanner the release-side contract lint uses (contract.lint_workflow_dir —
    it ships in the wheel, one scanner everywhere) and print a LOUD stderr
    warning naming each ``file → job → step`` plus the one next action.

    A WARNING, never a failure: init must never break the boot over a
    consumer-authored job — the consumer's own CI produces the real red. Any
    scan error (missing yq, odd tree) is swallowed for the same reason.
    """
    try:
        patterns = contract.managed_path_patterns(
            {
                "managed_path_prefixes": list(contract.MANAGED_PATH_PREFIXES),
                "untracked_mirrors": sorted(mirror_dests),
            }
        )
        violations = contract.lint_workflow_dir(repo_root, patterns)
    except Exception:
        return  # best-effort tripwire — the boot is never the casualty
    if not violations:
        return
    print(
        "WARNING: consumer workflow job(s) invoke managed ephemeral paths without\n"
        "installing the `.release/` temp dir first. Post-WS7 these paths are untracked\n"
        "(rewritten by `release-core init`), so they DO NOT EXIST on a fresh CI\n"
        "checkout — each job below will fail with 'No such file or directory':",
        file=sys.stderr,
    )
    for v in violations:
        print(f"  {v.file} -> {v.job} -> {v.step}  (references {v.matched})", file=sys.stderr)
    print(
        "Each listed job must install the `.release/` temp dir first — add, BEFORE the\n"
        "referencing step:\n"
        "  - uses: arthur-debert/release/.github/actions/arm-gate@v3\n"
        "    with:\n"
        "      toolset: 'false'   # build-only; drop to also arm the lint gate\n"
        "See `release-core how-to`.",
        file=sys.stderr,
    )


def _apply_mirror(mirror: sync.MirrorPlan, claude: sync.ClaudeDecision) -> None:
    """The apply phase: --migrate removals, symlink create/remove, managed-copy
    write/remove, and the CLAUDE.md write. Runs relative to cwd (init has chdir'd
    into the repo root). Formerly ``release_sync._apply`` — relocated here when the
    standalone sync verb was RETIRED (WS4, release#521); init is its sole caller."""
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
        _prune_empty_parents(os.path.dirname(link))

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

    # Remove retired files (WS6, release#527) — provenance-verified
    # in sync._find_retired_files, so only release's own retired copies land here.
    # Prune the now-empty parent dirs too (release#563): a fully-swept
    # vendor/semver-tool/ or .claude/skills/<name>/ must not linger as an empty
    # husk — but os.rmdir refuses a non-empty dir, so a directory holding ANY
    # consumer file survives untouched.
    for f in mirror.retired_to_remove:
        _rm_f(f)
        _prune_empty_parents(os.path.dirname(f))

    # WS7 (release#528): the symlink mirrors are EPHEMERAL — built above,
    # never tracked. Keep `git status` clean by listing them in the local
    # .git/info/exclude (NOT the consumer's .gitignore: zero tracked footprint,
    # and info/exclude is per-clone, recomposed by every init just like the
    # mirrors themselves). CONFLICT dests are left out: no symlink was applied
    # there (a real file/dir blocks the managed path), and excluding the path
    # would hide that untracked file from `git status` — masking the very
    # conflict the user is told to resolve.
    _write_mirror_excludes(mirror.mirror_dests - set(mirror.conflicts))

    # WS4 (#761): the managed orientation = a managed TARGET file
    # (.claude/IMPORTANT-RELEASE.md) + a one-line `@import` of it in CLAUDE.md.
    # ATOMICITY (safety rule #2): write the TARGET FIRST, then the import line —
    # so the @import never dangles (target always exists before CLAUDE.md points
    # at it). The target is committed by init's auto-commit; the CLAUDE.md import
    # line is written to disk but NEVER staged (CLAUDE.md is consumer-owned).
    if claude.target_action == "write":
        assert claude.target_desired is not None
        d = os.path.dirname(sync.CLAUDE_IMPORT_TARGET)
        if d:
            os.makedirs(d, exist_ok=True)
        _atomic_write_text(sync.CLAUDE_IMPORT_TARGET, claude.target_desired)

    if claude.import_action in ("create", "insert"):
        assert claude.import_content is not None
        _atomic_write_text(sync.CLAUDE_FILE, claude.import_content)


def _atomic_write_text(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically (sibling temp + os.replace) at
    0o644. Same-filesystem replace so a partially-written file is never observed
    — the CLAUDE.md @import target and the import line both rely on this."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=d or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _prune_empty_parents(d: str) -> None:
    """Remove now-empty parent directories upward from ``d`` (repo-relative),
    stopping at the repo root or the first non-empty dir. os.rmdir refuses a
    non-empty dir — that's the stop condition, so a directory holding any
    consumer-owned file is never touched."""
    while d and d not in (".", "./"):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


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
    """The deterministic auto-commit message for a full managed-files install. The
    managed files are fully generated (no review needed), so SessionStart can
    auto-commit them.

    NO `[skip ci]`: when the managed commit is the head of a pushed branch — a
    consumer's first-migration PR, or any feature branch where it lands last —
    `[skip ci]` makes GitHub skip ALL workflows for that push, so a
    required-status-checks ruleset can never be satisfied and the PR is BLOCKED
    forever. Managed changes track release cadence (byte-identical → no commit),
    so letting CI run on them is cheap and is the only way they reach a protected
    branch."""
    label = ref_label or "release"
    return f"chore(release): sync managed tree from {label}"


_EXCLUDE_BEGIN = "# >>> release-core managed mirrors (rewritten by every init) >>>"
_EXCLUDE_END = "# <<< release-core managed mirrors <<<"


def _write_mirror_excludes(dests: set[str]) -> None:
    """Rewrite the managed block in `.git/info/exclude` listing every ephemeral
    mirror dest (WS7, release#528), so the untracked symlinks never show up in
    `git status`. Runs relative to cwd (init has chdir'd into the repo root).
    info/exclude — not the consumer's .gitignore — because the point is ZERO
    tracked footprint; it is per-clone state reinstalled by every init, exactly
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
        # A `written` path can be a swept EPHEMERAL symlink: gone from disk and
        # never tracked, so `git add` would error "pathspec did not match" and
        # void the whole commit. Stage only what git can see.
        stageable = [
            p
            for p in written
            if os.path.lexists(os.path.join(repo_root, p)) or gh.git_path_tracked(p, cwd=repo_root)
        ]
        if not stageable:
            # Nothing managed to commit — e.g. the only delta was inside the now
            # gitignored .release/ tree (ephemeral, never committable). Skip rather
            # than run a pathspec-less `git commit` that would fold in unrelated work.
            return
        # force=True: managed real-file paths are release-owned and must be tracked
        # even if the consumer's .gitignore covers one (e.g. `.claude/` shadowing
        # the managed `.claude/skills/`) — otherwise the commit silently fails on
        # the ignored path.
        gh.git_add(stageable, cwd=repo_root, force=True)
        # Commit ONLY the managed pathspecs. A pathspec-scoped commit ignores any
        # other staged changes, so a user's in-progress staging is never folded in.
        gh.git_commit_paths(stageable, message, cwd=repo_root)
    except Exception as exc:  # ProcError or anything git surfaces
        print(
            f"release-core init: --commit skipped (could not commit managed config: {exc})",
            file=sys.stderr,
        )
        return

    # Report the REAL number of files in the commit, not len(written): `written`
    # is a list of pathspecs (".release" is ONE entry that git expands to every
    # built file), so len(written) badly under-counts a full sync.
    n = gh.git_commit_file_count(cwd=repo_root) or len(written)
    print(f"committed {n} managed file(s): {message}")

    branch = gh.git_current_branch(cwd=repo_root)
    default = gh.git_default_branch(cwd=repo_root)

    pushed = False
    if push:
        # --push guard: ONLY when on the default branch AND the tree is otherwise
        # clean (no non-managed changes — the managed paths are now committed, so a
        # clean check needs no exceptions). Otherwise the commit stays local.
        if branch is None or default is None or branch != default:
            print(
                f"  push skipped: on '{branch or 'detached HEAD'}', not the default "
                f"branch ('{default or 'unknown'}') — commit kept local.",
                file=sys.stderr,
            )
        elif not gh.git_is_clean(cwd=repo_root):
            print(
                "  push skipped: working tree has other uncommitted changes — commit kept local.",
                file=sys.stderr,
            )
        else:
            try:
                gh.git_push_ff(branch, cwd=repo_root)
                pushed = True
                print(f"  pushed to {branch}.")
            except Exception as exc:
                print(f"  push skipped: {exc}", file=sys.stderr)

    # Loud hint (release#566): the auto-commit just landed on the checked-out
    # DEFAULT branch and stays LOCAL. An agent that now branches from local
    # <default> carries this alien sync commit straight into its feature PR
    # diff (the #525 probe burned a review cycle on exactly that) — say so,
    # with the remedy, every time it actually happens.
    if not pushed and branch is not None and branch == default:
        print(
            f"NOTE: managed sync committed on '{branch}' (local only — not pushed).\n"
            f"      When branching for a PR, branch from origin/{default} (or push\n"
            f"      this commit first) so the sync commit does not ride into your PR diff."
        )


def _main_full(
    repo_root: str,
    repo_name: str,
    *,
    dry_run: bool,
    no_commit: bool,
    push: bool,
    provision: bool,
    cloud: bool,
) -> int:
    """The default init path: install all managed files + auto-commit-on-change.

    Runs the complete install (install_plan + install_tree +
    diff_release + compute_mirror + decide_claude + apply), sourced from the wheel
    bundle by default (or a real
    $RELEASE_HOME clone), then — unless --no-commit/--dry-run — stages ONLY the
    managed paths and commits iff they actually changed. Idempotent: a second run
    with no upstream change computes zero changes → no commit.

    Then (unless --no-provision / --dry-run) PROVISIONS the dev env (WS5/E, #762):
    toolset arming FIRST, submodule init, hook wiring, cloud steps under --cloud,
    and the per-repo post-setup hook LAST. Best-effort + idempotent — runs AFTER
    the managed-tree install so the hook has a built `.release/` gate to wire.
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
    # symlink/copy). These mean the managed files are NOT in steady state even when
    # the change count is 0 — never silently report "already current".
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
            f"summary: {changes} managed-file change(s), {len(conflicts)} conflict(s) "
            f"(dry-run, no writes){' from ' + ref_label if ref_label else ''}"
        )
        return 0

    if changes:
        suffix = f", {len(conflicts)} conflict(s)" if conflicts else ""
        print(
            f"summary: {changes} managed-file change(s) applied from "
            f"{ref_label or 'release'}{suffix}."
        )
    elif conflicts:
        print(f"summary: 0 changes but {len(conflicts)} unresolved conflict(s) — see stderr.")
    else:
        print("summary: managed files already current (no changes).")

    # AUTO-COMMIT: commit the managed mirror paths when something changed.
    # --no-commit skips the commit (for tests/inspection). Conservative and
    # never-fail (see _auto_commit). On any branch — the managed files are
    # generated, needs no review.
    if changes and not no_commit:
        _auto_commit(repo_root, managed, _full_commit_message(ref_label), push=push)

    # PROVISION the dev env (WS5/E, #762) — AFTER the managed-tree install so the
    # hook wiring finds a built `.release/` gate. Best-effort + idempotent; the
    # managed `provision.run` arms the toolset FIRST (the gate/hook need it). The
    # auto-commit above ran first so a provisioning step (npm/pip install) can
    # never dirty the managed commit. Skipped under --no-provision (CI / tests).
    if provision:
        from .. import provision as _provision

        _provision.run(repo_root, cloud=cloud)
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
                cli.Opt("--cloud"),
                cli.Opt("--no-provision"),
            ],
            doc=_usage_block(),
        )
    except SystemExit as exc:
        return int(exc.code or 0)

    force = bool(values["force"])
    dry_run = bool(values["dry-run"])
    push = bool(values["push"])
    no_commit = bool(values["no-commit"])
    cloud = bool(values["cloud"])
    provision = not bool(values["no-provision"])

    # --push implies a commit; --no-commit suppresses it — the two contradict.
    # Reject the combo as bad usage rather than silently making --push a no-op.
    if push and no_commit:
        print("release-core init: --push and --no-commit are mutually exclusive", file=sys.stderr)
        return 64
    # The commit is automatic (auto-commit-on-change; --no-commit to skip) and
    # the install overwrites unconditionally — so an explicit --commit is
    # redundant and --force a no-op. TOLERATE them (warn, don't fail): the
    # deployed SessionStart resolver in not-yet-migrated consumers still calls
    # `release-core init --commit`, and that stale invocation is exactly what
    # performs the FIRST cutover pull. Failing it would stall the whole fleet —
    # the resolver can't install the managed files that would in turn update the
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

    return _main_full(
        repo_root,
        repo_name,
        dry_run=dry_run,
        no_commit=no_commit,
        push=push,
        provision=provision,
        cloud=cloud,
    )
