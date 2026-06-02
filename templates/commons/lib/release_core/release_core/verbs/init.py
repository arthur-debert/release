"""init — materialize the per-repo COMMITTED config files into a consumer repo.

Usage:
  release-core init [--force] [--dry-run]

`release-core init` is the seam (pip-bootstrap PoC, docs/proposals/pip-bootstrap-contract.md
§2) that replaces release-sync's *config* materialization. The package arrives
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
  - package code (lib/release_core/**, lib/release_gh/**) — arrives via pip
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

Source resolution (PoC): the canonical config content is composed by the
release-sync engine (sync.build_plan + sync.materialize) from $RELEASE_HOME — the
same git clone + $RELEASE_REF env contract release-sync uses. This is a PoC
simplification; folding fragment composition into the installed package so init
needs no release checkout is a post-PoC follow-up.

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


def _materialize_config_sources(repo_root: str, repo_name: str) -> dict[str, str]:
    """Compose the canonical config content via the release-sync engine and
    return {dest -> absolute path in a temp tree} for every CONFIG_FILES dest
    that the engine produced.

    Reuses sync.build_plan + sync.materialize (faithful composition, including
    the fragment-merged lefthook.yml) rather than reimplementing it; init only
    SELECTS the config subset and copies it create-if-absent. May raise
    manifest.KindError (undetectable Kind), sync.SyncError (no $RELEASE_HOME
    clone / no candidate ref), or yamlio.YamlError (missing yq, malformed
    manifest/.release-sync.yaml, or a lefthook-fragment merge failure) — main()
    catches all three and maps them to a clean exit 1.

    The returned temp tree leaks intentionally for the process lifetime (a few
    KB of config); the OS reaps it. Keeping init small beats threading cleanup.
    """
    release_home = os.environ.get("RELEASE_HOME") or os.path.join(
        os.path.expanduser("~"), "release"
    )
    if not os.path.isdir(os.path.join(release_home, ".git")):
        raise sync.SyncError(
            f"release-core init: $RELEASE_HOME='{release_home}' is not a git clone"
        )

    kind = manifest.detect_kind(repo_root)
    release_ref = os.environ.get("RELEASE_REF") or None
    ref = sync.select_ref(release_home, repo_name, kind, release_ref)
    ref_sha = gh.git_rev_parse(ref, cwd=release_home)

    # Honor a consumer .release-sync.yaml capability override, exactly as
    # release-sync does (verbs/release_sync.py) — so init composes the SAME
    # config set the consumer would get from a sync.
    sync_yaml_text = None
    sync_yaml = os.path.join(repo_root, ".release-sync.yaml")
    if os.path.isfile(sync_yaml):
        with open(sync_yaml, encoding="utf-8", errors="replace") as fh:
            sync_yaml_text = fh.read()

    caps = sync.resolve_capabilities(release_home, ref, kind, sync_yaml_text=sync_yaml_text)
    plan = sync.build_plan(release_home, ref, kind, caps.names)

    tmp_root = tempfile.mkdtemp(prefix=".release-core-init.")
    sync.materialize(release_home, ref, ref_sha, plan, tmp_root)

    sources: dict[str, str] = {}
    for dest in CONFIG_FILES:
        candidate = os.path.join(tmp_root, dest)
        if os.path.isfile(candidate):
            sources[dest] = candidate
    return sources


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
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    else:
        shutil.copyfile(src, dest)


def main(argv: list[str] | None = None) -> int:
    try:
        values, _ = cli.parse(
            argv if argv is not None else [],
            [
                cli.Opt("--force"),
                cli.Opt("--dry-run"),
            ],
            doc=_usage_block(),
        )
    except SystemExit as exc:
        return int(exc.code or 0)

    force = bool(values["force"])
    dry_run = bool(values["dry-run"])

    try:
        repo_root = gh.repo_root()
    except Exception:
        print("release-core init: not inside a git repo", file=sys.stderr)
        return 1
    os.chdir(repo_root)
    repo_name = os.path.basename(repo_root)

    try:
        sources = _materialize_config_sources(repo_root, repo_name)
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
    skipped: list[str] = []
    missing_source: list[str] = []

    for dest in CONFIG_FILES:
        src = sources.get(dest)
        if src is None:
            # The engine produced no such file for this Kind (e.g. a Kind whose
            # gate composes no lefthook). Report it; not a failure.
            missing_source.append(dest)
            continue
        exists = os.path.lexists(dest)
        if exists and not force:
            skipped.append(dest)
            continue
        action_list = overwritten if exists else created
        if dry_run:
            action_list.append(dest)
            continue
        try:
            _write_file(dest, src, exists=exists)
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
    for f in skipped:
        print(f"  skip    {f} (exists; --force to overwrite)")
    for f in missing_source:
        print(f"  absent  {f} (not produced for this kind)", file=sys.stderr)

    changed = len(created) + len(overwritten)
    print()
    print(
        f"summary: {len(created)} created, {len(overwritten)} overwritten, "
        f"{len(skipped)} unchanged" + (" (dry-run, no writes)" if dry_run else "")
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
    return 0
