"""sync — installs the `.release/` temp dir (its files + the symlink mirrors, ADR-0001).

Ref selection, Kind+Component resolution, the plan for a fresh `.release/`,
lefthook fragment composition, release-internal classification,
symlink-target computation, the diff against an existing `.release/`,
broken-symlink detection, and the CLAUDE.md header block. ``verbs/init.py``
calls these steps (install_plan → install_tree → diff → compute_mirror →
decide_claude → _apply_mirror); init is the only caller (the standalone
wrapper verb was retired in WS4, release#521).

All git access goes through gh.py (the chokepoint). Filesystem reads/writes use
the stdlib. Behavior mirrors the bash byte-for-byte — see the per-function notes
that pin each bash construct.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

from . import gh

# ── Source abstraction ────────────────────────────────────────────────────────
#
# The install engine reads its template SOURCE through this minimal interface so the
# SAME plan/install logic serves two backends:
#
#   GitSource(release_home, ref)  — the original: a git ref in a release clone,
#       wrapping gh.git_ls_tree / git_show_bytes / git_cat_file_exists. Behavior
#       is IDENTICAL to the pre-abstraction engine's contract.
#   BundleSource(bundle_root)     — the wheel bundle: the on-disk template tree
#       hatch_build.py stages into release_core/_bundled_templates/. Lets `init`
#       install the managed files offline, no release clone, no network.
#
# Paths are always git-style (POSIX, '/'-separated, relative to the source root,
# e.g. "templates/commons/bin/check" or "skills/tdd/SKILL.md"). list_tree returns
# (relpath, mode) pairs with mode the git filemode string ("100755"/"100644").


class Source:
    """The template-source interface the engine reads through.

    Three operations, all keyed by git-style relative paths:
      list_tree(subtree) -> list[(relpath, mode)]   recursive file listing
      read_bytes(relpath) -> bytes                  a file's raw bytes
      exists(relpath) -> bool                       does this path resolve

    ``ref_sha`` is the provenance string written into .release-sync-source and
    the lefthook header (the resolved git SHA for GitSource; the wheel version /
    a sentinel for BundleSource). ``label`` is a human-readable description of
    the source used only in error messages (the git ref for GitSource).

    ``release_tag`` is the resolved release PROVENANCE the boot resolver stamped
    into the tool venv at install time (release#580) — e.g. "v2.17.1", or
    "from-source <shortsha>" for a --from-source install. Only the bundle path
    carries it (the wheel is what the stamp describes); GitSource composes from
    a live clone whose ref_sha already says exactly where the content came from.
    When set, install_tree() writes it into .release-sync-source alongside the
    ref_sha line, and init labels the managed auto-commit with it.
    """

    ref_sha: str = ""
    label: str = "source"
    release_tag: str | None = None

    def list_tree(self, subtree: str) -> list[tuple[str, str]]:  # pragma: no cover - interface
        raise NotImplementedError

    def read_bytes(self, relpath: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def exists(self, relpath: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class GitSource(Source):
    """Read templates from a git ref in a release clone (the original path).

    Wraps the gh.git_* chokepoint verbatim, so its behavior — including the
    ls-tree line format, the `|| true` tolerance of a missing tree, and the
    byte-exact `git show` — is byte-identical to the pre-abstraction engine.
    """

    def __init__(self, release_home: str, ref: str, ref_sha: str = "") -> None:
        self.release_home = release_home
        self.ref = ref
        self.ref_sha = ref_sha
        self.label = ref

    def list_tree(self, subtree: str) -> list[tuple[str, str]]:
        # `git ls-tree -r <ref> -- <subtree>` lines: "<mode> <type> <sha>\t<path>".
        listing = gh.git_ls_tree(self.ref, subtree, cwd=self.release_home, recursive=True)
        out: list[tuple[str, str]] = []
        for line in listing.splitlines():
            if not line:
                continue
            meta, tab, rel = line.partition("\t")
            if not tab or not rel:
                continue
            file_mode = meta.split(" ", 1)[0]
            out.append((rel, file_mode))
        return out

    def read_bytes(self, relpath: str) -> bytes:
        return gh.git_show_bytes(f"{self.ref}:{relpath}", cwd=self.release_home)

    def exists(self, relpath: str) -> bool:
        return gh.git_cat_file_exists(f"{self.ref}:{relpath}", cwd=self.release_home)


class BundleSource(Source):
    """Read templates from the on-disk wheel bundle.

    ``bundle_root`` is release_core/_bundled_templates/, whose layout mirrors the
    repo: bundle_root/templates/… and bundle_root/skills/…, so a ``subtree`` like
    "templates/commons" or "skills/tdd" resolves directly to a subdirectory.

    list_tree walks the subtree recursively and reports each file's git filemode
    from its on-disk mode bits (100755 if ANY execute bit is set — owner, group,
    or other — else 100644, exactly how git derives a blob's mode), with rel
    paths in sorted order for deterministic plan/output ordering across
    filesystems.
    """

    def __init__(self, bundle_root: str, ref_sha: str = "", release_tag: str | None = None) -> None:
        self.bundle_root = bundle_root
        self.ref_sha = ref_sha
        self.label = ref_sha or "wheel bundle"
        self.release_tag = release_tag

    def _abs(self, relpath: str) -> str:
        # relpath is always POSIX/'/'-separated; translate to the host separator.
        # CONTAINMENT GUARD: component names flow in from consumer-controlled
        # YAML (e.g. a subtree "templates/components/<cap>"), so a malicious
        # "../.." could otherwise escape the bundle and read arbitrary files.
        # Resolve against the real bundle root and refuse any path that lands
        # outside it. GitSource has no analogue (git refs can't traverse out).
        root = os.path.realpath(self.bundle_root)
        full = os.path.realpath(os.path.join(self.bundle_root, *relpath.split("/")))
        if full != root and not full.startswith(root + os.sep):
            raise SyncError(f"release-core init: bundle path escapes the bundle root: {relpath!r}")
        return full

    def list_tree(self, subtree: str) -> list[tuple[str, str]]:
        base = self._abs(subtree)
        if not os.path.isdir(base):
            return []
        out: list[tuple[str, str]] = []
        for dirpath, dirnames, filenames in os.walk(base):
            # Deterministic traversal regardless of readdir order.
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    # The bundle is a flat copy (symlinks=False at build); a
                    # symlink would be anomalous — skip rather than dereference.
                    continue
                rel_to_base = os.path.relpath(full, base).replace(os.sep, "/")
                rel = f"{subtree}/{rel_to_base}"
                # git records 100755 iff ANY execute bit (owner/group/other) is
                # set — check the stat mode bits, NOT os.access(X_OK) (which asks
                # whether the CURRENT user can execute and so misreports under
                # cross-user ownership).
                mode = "100755" if (os.stat(full).st_mode & 0o111) else "100644"
                out.append((rel, mode))
        return out

    def read_bytes(self, relpath: str) -> bytes:
        with open(self._abs(relpath), "rb") as fh:
            return fh.read()

    def exists(self, relpath: str) -> bool:
        return os.path.exists(self._abs(relpath))


# ── Constants (mirror the bash globals verbatim) ──────────────────────────────

# The header written into managed real-file workflow copies (the files GitHub
# can't deref a symlink for). WS4 (release#521) dropped the stale "release-sync"
# wording — the tree is composed by `release-core init` now.
MANAGED_MARKER = "# Managed by release — do not edit. Regenerate via release-core init."
# A stable, tool-agnostic PREFIX used to DETECT a managed copy (substring match on
# the first line). Deliberately a substring of BOTH the new marker AND the old
# "# Managed by release-sync …" one, so the stale-copy sweep still recognizes
# copies a pre-WS4 consumer committed — they get rewritten to the new marker on
# the next init rather than going unrecognized.
MANAGED_MARKER_SIGNATURE = "# Managed by release"
SOURCE_MARKER = ".release-sync-source"
# The resolved-release stamp the boot resolver writes into the tool venv at
# install time (release#580): <venv>/release-source.tag, one line — the release
# tag the wheel was downloaded from (e.g. "v2.17.1"), or "from-source
# [<shortsha>]" for a --from-source install. The FILE (not an env var) is the
# durable channel: a later bare `release-core init` (the SessionStart self-sync)
# runs without install-release-core in the chain, and sys.prefix — the venv the
# running release-core lives in — locates it on every run.
SOURCE_TAG_FILE = "release-source.tag"
GITIGNORE_FILE = ".gitignore"


def read_source_tag() -> str | None:
    """The resolved release provenance stamped by install-release-core, or None.

    Reads ``<sys.prefix>/release-source.tag``. None when absent — a wheel
    installed by an older (pre-#580) resolver, or a dev checkout venv — in
    which case callers fall back to the static wheel-version string (a
    one-session boot-window robustness, not a compatibility fallback). The
    $RELEASE_CORE_SOURCE_TAG env var, when SET, overrides the file (tests;
    empty value means "no stamp").
    """
    if "RELEASE_CORE_SOURCE_TAG" in os.environ:
        return os.environ["RELEASE_CORE_SOURCE_TAG"].strip() or None
    path = os.path.join(sys.prefix, SOURCE_TAG_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


# WS4 (release#521): the whole `.release/` temp dir is EPHEMERAL — gitignored
# and regenerated every session/CI from the pinned wheel, never committed. A
# `.gitignore` of `*` inside the dir makes it self-ignoring: git sees nothing
# under `.release/` (including this file), so the temp dir can't fall out of
# sync by construction (ADR-0005 supersedes the committed-tree model of
# ADR-0001/0002). This subsumes the old `__pycache__`-only ignore (release#450).
GITIGNORE_BODY = (
    f"{MANAGED_MARKER}\n"
    "# This .release/ build dir is ephemeral: composed on demand from the pinned\n"
    "# release_core wheel (release-core init) and never committed (release#521).\n"
    "*\n"
)

# ── `.release.major.txt` — the release-core LOGIC-version source of truth ──────
#
# WS3 (release#760): the committed single source for the release-core MAJOR line.
# This is one of the two version axes (proposal §"two version axes"):
#   - `.release.major.txt`  = release-core LOGIC version (FREQUENT; migrate = edit 1 file).
#   - `uses: …@vN`          = workflow STRUCTURE version (RARE).
# The bootstrap reads this file offline (`cat`) to know which wheel major to pull;
# the reusable workflow reads it from the consumer checkout (WS7). `init` SEEDS it
# when absent — derived from the consumer's `@vN` thin-caller pins — and thereafter
# it is authoritative (a present file carrying a valid major is never overwritten;
# a blank file counts as absent and is (re)seeded — self-healing). This makes the
# migration self-healing: one pull seeds the file, no consumer coordination.
RELEASE_MAJOR_FILE = ".release.major.txt"
# The release repo whose `@vN` pins declare the consumer's major (mirrors the
# bootstrap's REPO default). Used only by the transitional derive fallback.
RELEASE_REPO = "arthur-debert/release"
_CALLER_MAJOR_RE = re.compile(re.escape(RELEASE_REPO) + r"/[^@\"']*@v([0-9]+)")


def derive_caller_major(repo_root: str) -> str | None:
    """The release-core MAJOR line declared by this repo's `@vN` thin callers.

    Python port of the bootstrap shell's ``derive_caller_major`` (release#551):
    ``uses: <repo>/...@vN`` in ``.github/workflows/`` IS the consumer's pin
    declaration. The HIGHEST major wins — consumers legitimately mix lines (the
    copilot-review.yml caller stayed @v1 while the stack workflows moved on), and
    a mid-migration repo should follow the line it is moving TO. Returns the
    bare integer string ("3") or None when there is no workflows dir / no match.

    This is the transitional FALLBACK used to SEED ``.release.major.txt`` (and, in
    the bootstrap, to derive the major when the file is still absent). Removed in
    WS8 once the fleet carries the file.
    """
    wf_dir = os.path.join(repo_root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return None
    majors: set[int] = set()
    for dirpath, _dirs, files in os.walk(wf_dir):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for m in _CALLER_MAJOR_RE.finditer(text):
                majors.add(int(m.group(1)))
    if not majors:
        return None
    return str(max(majors))


def read_release_major(repo_root: str) -> str | None:
    """The committed major from ``.release.major.txt``, or None when absent.

    The file is one line — the bare major integer (e.g. "3"). Trailing
    whitespace/newline tolerated. A blank or absent file → None.

    This file is the OFFLINE source of truth for selecting the release-core major
    line, so a non-integer value is a misconfiguration that must FAIL LOUD here
    rather than silently mis-resolving a major downstream (raises ValueError).
    """
    path = os.path.join(repo_root, RELEASE_MAJOR_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read().strip()
    except OSError:
        return None
    if not content:
        return None
    if not content.isdigit():
        raise ValueError(
            f"{RELEASE_MAJOR_FILE} must contain a bare major integer (e.g. '3'), "
            f"got {content!r} — fix or delete the file to re-seed."
        )
    return content


def seed_release_major(repo_root: str) -> str | None:
    """SEED ``.release.major.txt`` when ABSENT, deriving the major from the
    consumer's `@vN` pins. Returns the repo-relative path when the file was
    WRITTEN (so init can stage + commit it), else None.

    A PRESENT file carrying a valid major is the single source of truth and is
    NEVER overwritten — once seeded, migrating the release-core logic version is
    "edit this one file". A BLANK file counts as absent (read_release_major →
    None) and is (re)seeded, so an empty/half-written file self-heals. When
    no major can be derived (no workflows dir / no `@vN` caller, e.g. a brand-new
    repo) the file is left unwritten and the bootstrap falls back to
    ``releases/latest`` — today's behavior.
    """
    if read_release_major(repo_root) is not None:
        return None
    major = derive_caller_major(repo_root)
    if major is None:
        return None
    path = os.path.join(repo_root, RELEASE_MAJOR_FILE)
    # Atomic same-dir replace; the file is a one-line bare integer + newline.
    fd, tmp = tempfile.mkstemp(prefix=RELEASE_MAJOR_FILE + ".tmp.", dir=repo_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{major}\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    return RELEASE_MAJOR_FILE


CLAUDE_FILE = "CLAUDE.md"
# WS4 (release#761): the managed orientation no longer SPLICES a BEGIN..END block
# into the consumer's CLAUDE.md. Instead init emits a managed target file —
# `.claude/IMPORTANT-RELEASE.md` — carrying the header content, and ensures
# CLAUDE.md @imports it with a ONE-LINE pointer. After insertion CLAUDE.md is 100%
# consumer-owned: the header rides the pull via the managed target, never by
# re-editing CLAUDE.md. The atomicity rule (proposal §"Three load-bearing safety
# rules" #2): the @import line and its target land TOGETHER — a dangling @import
# (target missing) breaks CLAUDE.md loading — and init NEVER re-stages CLAUDE.md
# (which would fold the consumer's uncommitted edits into the managed auto-commit).
CLAUDE_IMPORT_TARGET = ".claude/IMPORTANT-RELEASE.md"
CLAUDE_IMPORT_LINE = f"@{CLAUDE_IMPORT_TARGET}"
# The header content, now living in the managed target file (was CLAUDE_STUB_BODY,
# spliced into CLAUDE.md pre-WS4). `release-core how-to` remains the single source
# of orientation; this file just points at it.
CLAUDE_IMPORT_BODY = (
    "# Release-managed orientation\n"
    "\n"
    "<!-- Managed by release-core; do not edit. Regenerate via release-core init. -->\n"
    "\n"
    "This repo's quality gate, build, release, and PR/dev flow are provided by\n"
    "`release-core` (installed at session start; not stored in this repo).\n"
    "\n"
    "- **Start here:** run `release-core how-to` — the task playbook for *this* repo\n"
    "  (its dev cycle, incl. coordinating a complex / multi-PR feature with subagents).\n"
    "- Reference: `release-core --help`, `release-core <cmd> --help`, `release-core detect-kind`.\n"
    "- Quality gate (run every loop, after `git add`): `release-core gate`.\n"
)
# Pre-WS4 markers an already-seeded consumer may still carry in CLAUDE.md (the
# spliced BEGIN..END block). On the next init the block is STRIPPED and replaced by
# the one-line @import — recognized via either marker, never duplicated.
CLAUDE_BEGIN = "<!-- BEGIN release-managed orientation — managed by release-core; do not edit -->"
CLAUDE_BEGIN_LEGACY = (
    "<!-- BEGIN release-managed orientation — managed by release-sync; do not edit -->"
)
CLAUDE_END = "<!-- END release-managed orientation -->"

# ── Skill distribution catalogs ──────────────────────────────────────────────
#
# WS2 (release#523, "invoke don't discover"): the dev cycle + general-dev guidance
# now lives in `release-core how-to` (rendered from the binary), NOT synced as
# skill files. WS7 (release#528) trimmed PUSH_ALL_SKILLS to the ONE skill the
# harness needs a file on disk for — the `/`-triggered PR-loop driver ("at most
# one thin delegating skill", the WS2 exit). release-issue-relay was dropped from
# distribution: the escalation contract lives in the CLAUDE.md header block + `release-core
# how-to`, and the mechanism (`gh-release-issue`) is a console-script on PATH. The
# 15 general dev-cycle skills (tdd, review, diagnose, …), pr-review-respond, and
# now release-issue-relay are no longer pushed; on a consumer's next `init` their
# committed symlinks are swept by the broken-symlink cleanup (WS4) — automatic
# de-vendoring. A consumer owns ONLY its own application-domain skills. Two
# catalogs drive it:
#
#   PUSH_ALL_SKILLS         — pushed to EVERY consumer, unconditionally.
#   REPLACE_IF_PRESENT_SKILLS — upgrade-only: synced into a consumer ONLY when
#                               that consumer already has .claude/skills/<name>
#                               (real dir OR symlink). Never adds the skill to a
#                               consumer that doesn't already carry it.
#
# Everything else under skills/ (release-fleet-ops, release-fleet-triage,
# setup-matt-pocock-skills, gh-repo-setup) is
# release-only and NEVER distributed.
PUSH_ALL_SKILLS = [
    "gh-pr-review-loop",  # the `/`-triggered PR-loop driver (arms the guard)
]

REPLACE_IF_PRESENT_SKILLS = [
    "lex-primer",
    "lex-multirepo",
    "electron-e2e-testing",
    "macos-signing-notarization",
]


# ── Classification predicates (the bash case statements) ──────────────────────


def should_skip_source(rel: str) -> bool:
    """Mirror should_skip_source(): drop lefthook.fragment.yaml, manifest.yaml,
    templates/components/_*, and *.DS_Store.

    Also drops Python bytecode (__pycache__/, *.pyc, *.pyo): host- and
    Python-version-specific build artifacts that must never be built into
    a consumer's .release/ (release#450). The release repo gitignores these, so
    a clean ls-tree never lists them — this is defense-in-depth for a future
    source tree that tracks them, paired with the managed .release/.gitignore."""
    if rel.endswith("/lefthook.fragment.yaml"):
        return True
    if rel.endswith("/manifest.yaml"):
        return True
    if rel.startswith("templates/components/_"):
        return True
    # git paths are always '/'-separated, so a path-segment test is exact:
    # match a __pycache__ dir anywhere in the path, plus loose .pyc/.pyo files.
    if "/__pycache__/" in f"/{rel}":
        return True
    if rel.endswith((".pyc", ".pyo")):
        return True
    return rel.endswith(".DS_Store")


# The irreducible BOOTSTRAP files (WS5, release#526): the SessionStart chain
# must be readable/executable on a FRESH CLONE, i.e. BEFORE the ephemeral
# `.release/` exists — a symlink into `.release/` dangles there, so Claude Code
# could not even read the hooks config, and the boot could not start itself
# (the chicken-and-egg). These four are therefore written as REAL tracked
# copies (auto-refreshed by init exactly like the workflow copies); everything
# else stays an ephemeral-targeted symlink.
BOOTSTRAP_REAL_FILES: frozenset[str] = frozenset(
    {
        ".claude/settings.json",
        "bin/install-release-core",
        "bin/setup-dev-env.sh",
        "bin/pr-loop-guard",
    }
)


def needs_real_file(dest: str) -> bool:
    """Dests written as REAL copies, not symlinks into `.release/`:
    .github/workflows/* (GH reads workflow YAML from the tree and won't
    dereference a symlink) and the bootstrap files (must work on a fresh clone
    BEFORE `.release/` exists — see BOOTSTRAP_REAL_FILES)."""
    return dest.startswith(".github/workflows/") or dest in BOOTSTRAP_REAL_FILES


def is_distributed_skill_dest(dest: str) -> bool:
    """True for a managed skill discovery path (.claude/skills/<name>/…). These
    are release-owned: a pre-existing REAL file/dir at such a dest is a stale
    hand-copy and is replaced by the managed symlink unconditionally (no
    --migrate needed). This is the load-bearing fix for the lex pr-review-respond
    regression — a consumer shipping a stale real .claude/skills/<name>/SKILL.md
    must be upgraded to release's official copy, not left as a conflict."""
    return dest.startswith(".claude/skills/")


# The gate definition + most of its tool configs live ONLY in the ephemeral
# .release/ temp dir now (WS3, release#524): they are installed into .release/
# but no longer mirrored out to the consumer root. `release-core gate` points
# lefthook at .release/lefthook.yml (LEFTHOOK_CONFIG) and each tool is handed its
# config EXPLICITLY (markdownlint --config/--ignore-path, yamllint -c, prettier
# --ignore-path) from .release/.
#
# One config is deliberately NOT here — it stays mirrored to the consumer root
# because nothing can point its consumer at a .release/ copy:
#   - `.editorconfig` — editor-facing root convention (discovered by editors), not
#     a gate flag at all.
#
# `.shellcheckrc` moved INTO this set (release#531 F3): the gate-toolset
# equalization (release#536) pins shellcheck 0.11 everywhere via the
# shellcheck-py wheel, so `--rcfile .release/.shellcheckrc` (shellcheck >= 0.10)
# is now portable across the whole fleet and check-shell passes it explicitly —
# the root-discovered dotfile is no longer needed. The stale root symlink in
# already-seeded consumers is removed by the mirrored-dest sweep on re-init.
GATE_INTERNAL_FILES: frozenset[str] = frozenset(
    {
        "lefthook.yml",
        ".markdownlint.json",
        ".markdownlintignore",
        ".yamllint",
        ".prettierignore",
        ".shellcheckrc",
    }
)


def is_release_internal(dest: str) -> bool:
    """Content written into .release/ but NOT mirrored out as a symlink/copy:
    the provenance marker, the managed .gitignore (release#450), the Python engine
    package (lib/release_core/* — the folded PR state engine ships by pip wheel
    now, not sync; release#459), and the gate definition + tool configs
    (GATE_INTERNAL_FILES — WS3, release#524: the gate runs from .release/ via the
    binary, so lefthook.yml + the lint/format configs no longer reach the root).
    (ORIENTATION.md was retired in WS2, release#523 — the CLAUDE.md block is a stub
    pointing at `release-core how-to`.)"""
    if dest == SOURCE_MARKER:
        return True
    if dest == GITIGNORE_FILE:
        return True
    if dest in GATE_INTERNAL_FILES:
        return True
    return dest.startswith("lib/release_core/")


# ── Ref selection ─────────────────────────────────────────────────────────────


class SyncError(RuntimeError):
    """A fatal condition during the `.release/` temp dir install (maps to exit 1)."""


def select_ref(release_home: str, repo_name: str, kind: str, release_ref: str | None) -> str:
    """First-match-wins ref selection (mirrors the bash `--- Ref selection ---`).

    $RELEASE_REF (validated) → origin/release/beta/<repo-name> →
    origin/release/beta/<kind> → main. Fetches origin --prune only when
    RELEASE_REF is unset. Raises SyncError on a bad RELEASE_REF or no candidate.
    """
    if release_ref:
        if not gh.git_rev_parse_verify(release_ref, cwd=release_home):
            raise SyncError(f"release-core init: $RELEASE_REF='{release_ref}' is not a valid ref")
        return release_ref

    gh.git_fetch_prune(cwd=release_home)
    for candidate in (f"release/beta/{repo_name}", f"release/beta/{kind}", "main"):
        if gh.git_rev_parse_verify(f"refs/remotes/origin/{candidate}", cwd=release_home):
            return f"origin/{candidate}"
    raise SyncError("release-core init: no candidate branch found in $RELEASE_HOME")


# ── Capability resolution ─────────────────────────────────────────────────────


@dataclass
class Capabilities:
    names: list[str]
    manifest_source: str


def _yq_list_capabilities(text: str) -> list[str]:
    """Mirror `yq '.capabilities // [] | .[]'` over a YAML document: one element
    per line. Done via yamlio so the YAML seam stays single-sourced."""
    from . import yamlio

    data = yamlio.loads(text)
    if not isinstance(data, dict):
        return []
    caps = data.get("capabilities")
    if not isinstance(caps, list):
        return []
    # yq prints each scalar on its own line; mapfile splits on newlines. A
    # non-scalar element would render oddly, but capabilities are always scalars.
    return [str(c) for c in caps]


def resolve_capabilities(
    source: Source,
    kind: str,
    *,
    sync_yaml_text: str | None,
) -> Capabilities:
    """Mirror the `--- Capability resolution ---` block.

    Precedence: a consumer .release-sync.yaml (its text passed in) overrides the
    Kind manifest; a manifest-less Kind yields no capabilities. Returns the
    declared names + the human-readable manifest_source label.
    """
    if sync_yaml_text is not None:
        return Capabilities(
            names=_yq_list_capabilities(sync_yaml_text),
            manifest_source=".release-sync.yaml (consumer override)",
        )
    manifest_path = f"templates/{kind}/manifest.yaml"
    if source.exists(manifest_path):
        text = source.read_bytes(manifest_path).decode("utf-8", "replace")
        return Capabilities(
            names=_yq_list_capabilities(text),
            manifest_source=f"templates/{kind}/manifest.yaml (Kind default)",
        )
    return Capabilities(
        names=[],
        manifest_source="(none — manifest-less Kind; commons + Kind only)",
    )


def validate_capabilities(source: Source, capabilities: list[str]) -> None:
    """Per-component existence guard: each declared Component must have a
    templates/components/<c>/ tree in the source.

    A cheap existence probe (source.exists on the tree path) — NOT a recursive
    list_tree that install_plan immediately re-walks. A git tree is never empty,
    and the bundle never stages an empty dir, so existence == the original
    'non-empty tree' contract."""
    for c in capabilities:
        if not c:
            continue
        if not source.exists(f"templates/components/{c}"):
            raise SyncError(
                f"release-core init: declared Component '{c}' has no "
                f"templates/components/{c}/ tree in {source.label}"
            )


# ── Plan: source path → (mode, dest-relative-to-.release/) ────────────────────


@dataclass
class Plan:
    """The install plan. ``order`` preserves first-seen dest order
    (mirrors plan_order); ``mode``/``source`` map dest → git filemode / source
    path (last write wins, mirroring the bash assoc-array overwrite)."""

    order: list[str] = field(default_factory=list)
    mode: dict[str, str] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)
    lefthook_frags: list[str] = field(default_factory=list)


def subtree_list(kind: str, capabilities: list[str]) -> list[str]:
    """Mirror the subtrees array: commons < each capability < kind."""
    subtrees = ["templates/commons"]
    for c in capabilities:
        if c:
            subtrees.append(f"templates/components/{c}")
    subtrees.append(f"templates/{kind}")
    return subtrees


def install_plan(
    source: Source,
    kind: str,
    capabilities: list[str],
    *,
    repo_root: str | None = None,
) -> Plan:
    """Mirror the `--- Plan ---` block: walk each subtree, skip the
    should_skip_source paths, strip the subtree prefix to get the dest, then add
    the distributed skills and assemble the lefthook fragment list.

    ``repo_root`` is the consumer working tree. It gates the REPLACE_IF_PRESENT
    skills (synced only when the consumer already carries .claude/skills/<name>);
    when None (e.g. a clone-less init), those upgrade-only skills are skipped."""
    plan = Plan()

    for st in subtree_list(kind, capabilities):
        for rel, file_mode in source.list_tree(st):
            if should_skip_source(rel):
                continue
            # dest = ${rel#"$st"/}
            prefix = st + "/"
            dest = rel[len(prefix) :] if rel.startswith(prefix) else rel
            if dest not in plan.source:
                plan.order.append(dest)
            plan.mode[dest] = file_mode
            plan.source[dest] = rel

    # #348: distribute the official infra/dev-cycle skill set, sourced directly
    # from skills/ — whole-directory (every file under skills/<name>/), so
    # multi-file skills (tdd, triage, …) reach the consumer in full.
    for name in PUSH_ALL_SKILLS:
        _add_skill_dir(plan, source, name)
    if repo_root is not None:
        for name in REPLACE_IF_PRESENT_SKILLS:
            if _consumer_has_skill(repo_root, name):
                _add_skill_dir(plan, source, name)

    # Assemble lefthook.yml fragment list: base < commons < each capability < kind.
    frags: list[str] = []
    base = "templates/components/_lefthook-base.yaml"
    if source.exists(base):
        frags.append(base)
    commons = "templates/commons/lefthook.fragment.yaml"
    if source.exists(commons):
        frags.append(commons)
    for c in capabilities:
        if not c:
            continue
        fpath = f"templates/components/{c}/lefthook.fragment.yaml"
        if source.exists(fpath):
            frags.append(fpath)
    stack_frag = f"templates/{kind}/lefthook.fragment.yaml"
    if source.exists(stack_frag):
        frags.append(stack_frag)
    plan.lefthook_frags = frags

    return plan


def _add_skill_dir(plan: Plan, source: Source, name: str) -> None:
    """Add every file under skills/<name>/ in ``source`` to the plan, mapping each
    to a consumer dest of .claude/skills/<name>/<subpath>. Tolerant: a skill
    whose dir doesn't exist (empty listing) is silently skipped."""
    for rel, file_mode in source.list_tree(f"skills/{name}"):
        if should_skip_source(rel):
            continue
        # rel == "skills/<name>/<subpath>"; the consumer dest mirrors it under
        # .claude/ → ".claude/skills/<name>/<subpath>".
        dest = f".claude/{rel}"
        if dest not in plan.source:
            plan.order.append(dest)
        plan.mode[dest] = file_mode
        plan.source[dest] = rel


def _consumer_has_skill(repo_root: str, name: str) -> bool:
    """True iff the consumer working tree already carries .claude/skills/<name>
    as a real directory OR a symlink — the REPLACE_IF_PRESENT gate (upgrade an
    existing copy; never add the skill to a consumer that lacks it)."""
    return os.path.lexists(os.path.join(repo_root, ".claude", "skills", name))


# ── Symlink target computation ────────────────────────────────────────────────


def link_target(dest: str) -> str:
    """Mirror the relative-symlink target computation.

    For a top-level dest the target is `.release/<dest>`; otherwise prefix one
    `../` per path component of the dest's directory."""
    link_dir = os.path.dirname(dest)
    if link_dir in ("", "."):
        return f".release/{dest}"
    # depth = (number of '/' in link_dir) + 1  — bash: tr -cd '/' | wc -c, +1.
    depth = link_dir.count("/") + 1
    prefix = "../" * depth
    return f"{prefix}.release/{dest}"


# ── Build the new tree into a tempdir ─────────────────────────────────────────


def install_tree(source: Source, ref_sha: str, plan: Plan, tmp_release: str) -> None:
    """Write the new .release/ temp dir into a tempdir: write each
    planned blob (preserving the 100755/100644 mode), the assembled lefthook.yml,
    and the provenance marker into ``tmp_release``."""
    for dest in plan.order:
        src = plan.source[dest]
        fmode = plan.mode[dest]
        out_path = os.path.join(tmp_release, dest)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        content = source.read_bytes(src)
        with open(out_path, "wb") as fh:
            fh.write(content)
        os.chmod(out_path, 0o755 if fmode == "100755" else 0o644)

    if plan.lefthook_frags:
        _write_lefthook(source, ref_sha, plan.lefthook_frags, tmp_release)

    # Managed .gitignore (release#450): keeps bytecode out of the ephemeral
    # .release/ even when a consumer rebuilds from the working tree.
    gitignore = os.path.join(tmp_release, GITIGNORE_FILE)
    with open(gitignore, "w", encoding="utf-8") as fh:
        fh.write(GITIGNORE_BODY)

    # Provenance marker (ADR-0002): static comment lines + the full source SHA,
    # plus — when the boot resolver stamped one (release#580) — the resolved
    # release tag, so the marker can tell WHICH release line seeded this tree.
    # (Since release#758 the wheel version is tag-stamped too; the explicit tag
    # stamp remains the provenance channel recorded here.)
    marker = os.path.join(tmp_release, SOURCE_MARKER)
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write(
            "# release provenance — the arthur-debert/release commit that generated\n"
            "# this .release/. Purely informational (ADR-0002). Since WS4 (release#521)\n"
            "# the whole .release/ tree is gitignored + recomposed every session, so\n"
            "# this marker is transient and has no reader — the out-of-sync check was retired.\n"
            f"{ref_sha}\n"
        )
        if source.release_tag:
            fh.write(
                "# resolved release tag — stamped into the tool venv by\n"
                "# install-release-core at wheel-install time (release#580).\n"
                f"{source.release_tag}\n"
            )


def _write_lefthook(source: Source, ref_sha: str, frags: list[str], tmp_release: str) -> None:
    """Mirror the lefthook.yml generation: write each fragment to a
    NN-<dir>.yaml temp file (the numeric prefix fixes the merge order), then
    `yq eval-all '. as $i ireduce({}; . *+ $i) | ... comments=""'` over them,
    under the generated-by header. The `*+` deep-merges with array concat; the
    comment-strip drops fragment comments."""
    import shutil
    import tempfile

    from . import yamlio

    frag_tmp = tempfile.mkdtemp()
    try:
        frag_files: list[str] = []
        for i, fp in enumerate(frags):
            dirbase = os.path.basename(os.path.dirname(fp))
            out_path = os.path.join(frag_tmp, f"{i:02d}-{dirbase}.yaml")
            content = source.read_bytes(fp)
            with open(out_path, "wb") as fh:
                fh.write(content)
            frag_files.append(out_path)

        merged = yamlio.eval_all('. as $i ireduce({}; . *+ $i) | ... comments=""', frag_files)
        header = (
            f"# Generated by release from arthur-debert/release@{ref_sha[:12]}. Do not edit.\n"
            "# Regenerate by running release-core init.\n\n"
        )
        lefthook_out = os.path.join(tmp_release, "lefthook.yml")
        with open(lefthook_out, "w", encoding="utf-8") as fh:
            fh.write(header)
            fh.write(merged)
    finally:
        shutil.rmtree(frag_tmp, ignore_errors=True)


# ── Diff: new tree vs existing .release/ ──────────────────────────────────────


@dataclass
class FileDiff:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _find_files(root: str) -> list[str]:
    """All regular-file paths under ``root``, relative to it, in `find -type f`
    traversal order.

    Crucially this mirrors GNU/BSD `find`, NOT os.walk: find interleaves files
    and dirs in readdir order, recursing into a subdir AS SOON as it encounters
    it — whereas os.walk yields all of a directory's files first, then recurses.
    The two diverge whenever a directory holds files both before and after a
    subdir entry, which shifts the report's +file ordering. Matching find keeps
    the report byte-for-byte with the bash on the same filesystem."""
    out: list[str] = []

    def walk(d: str, rel: str) -> None:
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for entry in entries:  # readdir order, exactly as find consumes it
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                walk(entry.path, child_rel)
            elif entry.is_file(follow_symlinks=False):
                out.append(child_rel)

    walk(root, "")
    return out


def diff_release(tmp_release: str, existing_release: str) -> tuple[FileDiff, list[str]]:
    """Mirror `--- Compute changes ---`: added/modified/removed of files in
    .release/ comparing the new tree to the existing one. Returns the FileDiff
    plus the ordered list of new-tree relative paths (used downstream)."""
    new_files = _find_files(tmp_release)
    old_files = _find_files(existing_release) if os.path.isdir(existing_release) else []
    new_set = set(new_files)
    old_set = set(old_files)

    diff = FileDiff()
    for f in new_files:
        if f not in old_set:
            diff.added.append(f)
        elif not _files_equal(os.path.join(tmp_release, f), os.path.join(existing_release, f)):
            diff.modified.append(f)
    for f in old_files:
        if f not in new_set:
            diff.removed.append(f)
    return diff, new_files


def _files_equal(a: str, b: str) -> bool:
    """Mirror `cmp -s` — byte-identical comparison."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _expected_copy_bytes(f: str, tmp_release: str) -> bytes:
    """The exact bytes ``_apply_mirror`` would write for the real-file copy ``f`` —
    the installed source under ``tmp_release``, with the managed-marker header
    prepended for YAML (mirrors init._apply_mirror's copy branch). Used to tell a
    genuine copy change from a byte-identical rebuild so a steady-state
    sync is a true no-op (no phantom change count, no failed auto-commit)."""
    with open(os.path.join(tmp_release, f), "rb") as fh:
        body = fh.read()
    if f.endswith((".yml", ".yaml")):
        return (MANAGED_MARKER + "\n").encode("utf-8") + body
    return body


def _managed_copy_differs(f: str, repo_root: str, tmp_release: str) -> bool:
    """True iff the managed real-file copy ``f`` would actually change the working
    tree — absent dest, or dest bytes differ from what ``_apply`` would write.
    Byte-identical dest → False, so the copy is skipped (no churn, no spurious
    commit). A hand-edited / out-of-sync dest → True, so it is still repaired."""
    dest = os.path.join(repo_root, f)
    if not os.path.lexists(dest):
        return True
    if os.path.islink(dest):  # a stale symlink where a real file belongs → rewrite
        return True
    try:
        with open(dest, "rb") as fh:
            dest_bytes = fh.read()
        expected = _expected_copy_bytes(f, tmp_release)
        # Compare LF-normalized: a Windows checkout (core.autocrlf) can hold CRLF
        # while _expected_copy_bytes is LF, which would otherwise flag a phantom
        # change on every run. Defensive only — the supported macOS/Linux runners
        # never see CRLF here.
        return dest_bytes.replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n")
    except OSError:
        return True


# ── Symlink / copy / conflict plan against the consumer working tree ──────────


@dataclass
class MirrorPlan:
    symlinks_to_create: list[str] = field(default_factory=list)  # "f -> target"
    symlinks_to_remove: list[str] = field(default_factory=list)
    copies_to_write: list[str] = field(default_factory=list)
    copies_to_remove: list[str] = field(default_factory=list)
    retired_to_remove: list[str] = field(default_factory=list)  # WS6 retired files
    conflicts: list[str] = field(default_factory=list)
    migrated: list[str] = field(default_factory=list)
    # EVERY dest this sync mirrors out as a symlink (changed or not) — the
    # ephemeral-mirror population (WS7, release#528): these are built but
    # never tracked; init writes them into .git/info/exclude and untracks any a
    # pre-WS7 seed committed.
    mirror_dests: set[str] = field(default_factory=set)


def compute_mirror(
    new_files: list[str], repo_root: str, tmp_release: str, *, migrate: bool
) -> MirrorPlan:
    """Mirror the symlink/copy/conflict planning loop + the broken-symlink sweep
    + the stale managed-copy sweep. ``repo_root`` is the consumer cwd; paths are
    relative to it (the bash ran after `cd "$repo_root"`)."""
    mp = MirrorPlan()

    # A consumer's skill ROOT (.claude/skills/<name>) may itself be a SYMLINK —
    # REPLACE_IF_PRESENT treats that as "present", and an old hand-symlinked dir
    # is exactly the kind of stale copy we replace. If we did NOT handle it here,
    # the per-file abs_f below would resolve THROUGH that symlink, so _rm_f /
    # os.symlink would mutate the symlink's TARGET (possibly inside .release/)
    # instead of the consumer path. So: find each symlinked skill root carrying a
    # planned file, schedule its removal first (migrated → _rm_f removes the link,
    # not its target), and treat files under it as absent (plain create) below.
    symlinked_skill_roots = _symlinked_skill_roots(new_files, repo_root)
    for root in symlinked_skill_roots:
        mp.migrated.append(root)

    # Every managed real-file-copy dest this sync owns, whether or not it is being
    # (re)written. The stale sweep keys off THIS set — a managed copy that is
    # byte-identical (so not in copies_to_write) is still LIVE, not stale; only a
    # marker-bearing file absent from this set is a genuinely retired copy.
    live_copies: set[str] = set()

    for f in new_files:
        if is_release_internal(f):
            continue
        if needs_real_file(f):
            live_copies.add(f)
            # Only queue the copy when it would actually change the dest, so a
            # steady-state re-sync is a true no-op (no phantom change count, no
            # auto-commit that git then rejects for "nothing to commit").
            if _managed_copy_differs(f, repo_root, tmp_release):
                mp.copies_to_write.append(f)
            continue

        target = link_target(f)
        abs_f = os.path.join(repo_root, f)
        # If f sits under a symlinked skill root we're removing, the root is gone
        # by apply time — plan a plain create against the (soon-to-be) clean path,
        # never reading through the still-present symlink.
        if _under_any(f, symlinked_skill_roots):
            mp.symlinks_to_create.append(f"{f} -> {target}")
        elif os.path.islink(abs_f):
            current = os.readlink(abs_f)
            if current != target:
                mp.symlinks_to_create.append(f"{f} -> {target}")
        elif os.path.lexists(abs_f):
            # exists (non-symlink): a real file/dir at the managed location.
            # Distributed-skill dests are release-owned: a stale hand-copy there
            # is always replaced (no --migrate needed). Everything else keeps the
            # conflict-guard unless --migrate.
            if migrate or is_distributed_skill_dest(f):
                mp.migrated.append(f)
                mp.symlinks_to_create.append(f"{f} -> {target}")
            else:
                mp.conflicts.append(f)
        else:
            mp.symlinks_to_create.append(f"{f} -> {target}")

    # The dests this sync mirrors OUT as symlinks into .release/ (everything in
    # new_files that is neither release-internal nor a real-file copy). The sweep
    # removes any .release/-pointing symlink whose target dest is absent from this
    # set — a dropped target OR one no longer mirrored (WS3: root lefthook.yml + configs).
    mirrored_dests = {f for f in new_files if not is_release_internal(f) and not needs_real_file(f)}
    mp.mirror_dests = mirrored_dests
    mp.symlinks_to_remove = _find_broken_release_links(repo_root, mirrored_dests)
    mp.copies_to_remove = _find_stale_managed_copies(repo_root, live_copies)
    # A dest this sync still distributes is LIVE, never a retired file — guards a
    # future kind re-shipping a retired name against the sweep eating its mirror.
    planned = {f for f in new_files if not is_release_internal(f)}
    mp.retired_to_remove = [f for f in _find_retired_files(repo_root) if f not in planned]
    return mp


def _skill_root_of(dest: str) -> str | None:
    """The `.claude/skills/<name>` root for a distributed-skill dest, else None.
    `.claude/skills/tdd/mocking.md` → `.claude/skills/tdd`."""
    if not is_distributed_skill_dest(dest):
        return None
    parts = dest.split("/")
    # parts == [".claude", "skills", "<name>", ...] — need at least the name.
    if len(parts) < 4:
        return None
    return "/".join(parts[:3])


def _symlinked_skill_roots(new_files: list[str], repo_root: str) -> list[str]:
    """The distinct `.claude/skills/<name>` roots that (a) carry a planned file
    and (b) exist in the consumer tree as a SYMLINK. First-seen order, no dups."""
    seen: set[str] = set()
    out: list[str] = []
    for f in new_files:
        root = _skill_root_of(f)
        if root is None or root in seen:
            continue
        seen.add(root)
        if os.path.islink(os.path.join(repo_root, root)):
            out.append(root)
    return out


def _under_any(dest: str, roots: list[str]) -> bool:
    """True if ``dest`` is a file under one of the given `.claude/skills/<name>`
    roots (root + '/')."""
    return any(dest == r or dest.startswith(r + "/") for r in roots)


def _find_broken_release_links(repo_root: str, mirrored_dests: set[str]) -> list[str]:
    """Mirror the broken-symlink sweep: walk the repo (excluding .git/ and
    .release/) for symlinks whose target points into `.release/`; a link is stale
    iff its post-`.release/` target dest is NOT one this sync mirrors out as a
    symlink (``mirrored_dests`` — the planned symlink dests, i.e. ``new_files``
    minus the release-internal + real-file-copy dests).

    This single membership rule subsumes the older "target absent from the new
    tree" test: a target removed or dropped this sync is not a mirrored dest, so
    it is swept (the lex/#476 case — a retired file whose old `.release/` copy is
    still live). It ALSO sweeps a de-mirrored-but-still-resolving link: WS3
    (release#524) made the root `lefthook.yml` + lint/format configs
    release-internal (built into `.release/` but no longer mirrored out),
    so their `.release/` targets still EXIST — a filesystem-presence test would
    leave the stale root symlinks behind, but they are no longer mirrored dests,
    so they are swept. A hand-tampered `..` target is likewise not a clean
    mirrored dest → swept (the old explicit containment guard is now subsumed).

    Paths are returned relative to repo_root, prefixed `./` exactly as the bash
    `find . -type l` emitted them (e.g. './bin/stale-tool'), in find traversal
    order (readdir, recursing into a real dir as it is encountered; .git and
    .release pruned; a symlinked dir is `-type l` so find does not descend it)."""
    out: list[str] = []

    def walk(d: str, rel: str) -> None:
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for entry in entries:
            child_rel = f"{rel}/{entry.name}" if rel else f"./{entry.name}"
            if entry.is_symlink():
                target = os.readlink(entry.path)
                if ".release/" not in target:
                    continue
                # rel-after-marker = "${target##*.release/}" (text after the LAST).
                tgt_rel = target.rsplit(".release/", 1)[1]
                if tgt_rel not in mirrored_dests:
                    out.append(child_rel)
            elif entry.is_dir(follow_symlinks=False):
                # Prune .git and .release at the top level (find -not -path).
                if rel == "" and entry.name in (".git", ".release"):
                    continue
                walk(entry.path, child_rel)

    walk(repo_root, "")
    return out


def _find_stale_managed_copies(repo_root: str, copy_set: set[str]) -> list[str]:
    """Mirror the stale managed-copy sweep under .github/workflows/: real files
    carrying the MANAGED_MARKER header that are not being (re)written this sync."""
    out: list[str] = []
    wf_dir = os.path.join(repo_root, ".github/workflows")
    if not os.path.isdir(wf_dir):
        return out
    for dirpath, _dirnames, filenames in os.walk(wf_dir):
        for name in filenames:
            full = os.path.join(dirpath, name)
            # copy_set is keyed with forward slashes (git/POSIX paths); force the
            # separator so the membership test holds on every platform. On the
            # supported macOS/Linux runners os.sep is already "/", so this is a
            # no-op there and purely defensive for a hypothetical Windows host.
            rel = os.path.relpath(full, repo_root).replace(os.sep, "/")
            if rel in copy_set:
                continue
            if os.path.islink(full):
                continue
            if _first_line_has_marker(full):
                out.append(rel)
    return out


def _first_line_has_marker(path: str) -> bool:
    """True iff the file's first line carries the managed-copy signature. Matches
    on MANAGED_MARKER_SIGNATURE (a stable prefix), NOT the full marker, so a copy a
    pre-WS4 consumer committed with the old "release-sync" wording is still
    recognized as managed (and gets rewritten to the current marker)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return MANAGED_MARKER_SIGNATURE in first


# ── Retired distributed files (WS6, release#527) ──────────────────────────────
#
# Files release ONCE distributed as tracked REAL files and has since retired.
# The two mirror sweeps cannot touch them: the broken-symlink sweep only sees
# `.release/`-pointing symlinks, and the stale managed-copy sweep only sees
# MANAGED_MARKER-carrying files under `.github/workflows/`. A retired real copy
# from the pre-marker era is invisible to both — it sits tracked in the consumer
# forever unless removed here.
#
# Removal is provenance-gated — a retired-file entry must NEVER eat
# consumer-authored work. Three tests, the strongest available per path:
#   blob         the content's git blob SHA-1 is one release's template history
#                actually shipped (byte-exact; a consumer-MODIFIED copy no
#                longer matches and is deliberately left alone);
#   marker       the first line carries the managed-marker signature (a
#                managed file: content varies per repo, header is stable
#                across both marker wordings);
#   fingerprint  a distinctive header line (the bin/release callers were tailored
#                per repo at onboarding, so no stable blob exists — but the
#                header comment is verbatim across every variant).
# Inventory: the 2026-06 fleet audit for release#527, COMPLETED by the #563
# legacy audit — every variant of each path across all managed repos. Every
# blob SHA below was re-derived from release's template git history
# (`git rev-list --all -- <template path>` → `git rev-parse <rev>:<path>`),
# never taken from a consumer on faith.

RETIRED_BLOB_FILES: dict[str, frozenset[str]] = {
    # Pre-unified-gate lint/format entry points, superseded by the composed
    # lefthook gate (WS3): every blob each kind template ever shipped.
    "bin/check-fmt": frozenset(
        {
            "8c18fd5deedec10dc0e2d07c539b0c22ae8731e9",  # templates/rust
            "2419933941d5607732a488669188d77269a7f49b",  # templates/go
            "82c3c8dcbee4c6eaa0b852c6122a4e4b0a1037c2",  # templates/go (earlier)
            "a861acbedc7ec92ab1dca0fba0d99243a07583bf",  # templates/tauri-app
            "ada3177ecd4243298774ecb1829b0cdda61d63f3",  # templates/electron-app + vsce-ext
            "17f2dc417a6834789a071352a8a50babdd7adeee",  # templates/zed-extension
        }
    ),
    "bin/check-lint": frozenset(
        {
            "78d51287c747be86eedb879559b1188830b03d3a",  # templates/rust
            "310a55a9b7f6134b806e5a9d2a7280b0b685708a",  # templates/rust (earlier)
            "e09ccdbbefd8db5dee80127af0a52e534a4b8229",  # templates/go
            "8d5e38321459b0ec8b48b022bc0e0596ba905aac",  # templates/go (earlier)
            "c9d99017fb0e86517a3a7497086f58639f0969e4",  # templates/tauri-app
            "40ba96894af8e4ad7b21386463bb8b0588cd7b2c",  # templates/electron-app + vsce-ext
            "4b064e146d371e325647394af55a25fafbad6e2a",  # templates/zed-extension
        }
    ),
    # The earliest go/rust layout shipped the gate scripts under scripts/
    # (templates/{go,rust}/scripts/*). BLOB-ONLY on purpose: these are extremely
    # common consumer-owned names — never marker/fingerprint them.
    "scripts/check": frozenset(
        {
            "3af8598957d75b45128863315ca3811d45ada3c5",  # templates/go (earlier)
            "689d763378c34541c5c987a8a204a7f0fcb99aa5",  # templates/go + rust
        }
    ),
    "scripts/check-fmt": frozenset(
        {
            "8c18fd5deedec10dc0e2d07c539b0c22ae8731e9",  # templates/rust
            "82c3c8dcbee4c6eaa0b852c6122a4e4b0a1037c2",  # templates/go
        }
    ),
    "scripts/check-lint": frozenset(
        {
            "310a55a9b7f6134b806e5a9d2a7280b0b685708a",  # templates/rust
            "8d5e38321459b0ec8b48b022bc0e0596ba905aac",  # templates/go
            "2dfd86206942093756d7a86365929047a6a5f9d1",  # templates/go (earlier)
        }
    ),
    "scripts/check-tests": frozenset(
        {
            "a1a9fcd68c3fc37957018dd1a2ade7ad014ce897",  # templates/rust
            "c10b23f55b77cf7e0a14b61ada351074932b1428",  # templates/go
        }
    ),
    # The pre-console-script changelog scripts (retired with the pip cutover,
    # #476): every blob templates/commons/bin/changelog* ever held.
    "bin/changelog": frozenset(
        {
            "2e0b25bd9840912b84a046742286c5a04f423301",
            "7acce23e154460a7873a77e9598d59eb93b0e03c",
            "8b4ccbb9df84cf9705e100b4d1aee05cbc03a4df",
            "a18249c94af945dd60dfeedd93471d973e12f715",
            "a6ceaedd85d369fa7a8b2f0486f60d00188acdd6",
            "c004847983baa499d6b484fa8d329883e3b8e343",
        }
    ),
    "bin/changelog-add": frozenset(
        {
            "27eb9e197bdc4f49458527695492e5c5aedddee6",
            "557d2b6989de81fd2c18d27107cde715a6966bdf",
            "6a4b0d352565ca673f8ab3ffd54815eca99d8160",
            "c6939dc5153ab50f27c377d3297720d3b41af9c0",
            "c819d943e23fd05c4babe367ad9a89c9ed637693",
            "f2bb961a9c6b92e36f69b81ab28314013dcc7c73",
        }
    ),
    "bin/changelog-cut": frozenset(
        {
            "4232a08838661a027c2bb1e95bbd13d2bb307dc1",
            "4afeb9bfe6380859d9034edf3347cba895689f71",
            "5396b96dc989aa9945d62348c7c70bef49cf986a",
            "59cda47d6a73df674576c86c8fc14aad08d54346",
            "a1cce5dcabd2c3b27ff6930eaa647c97dc8dac9e",
            "b1b7e5924153538b865c82602c5905008cb7b07f",
            "b27ca862e9c7eaea329b0fd15e37eb04d38fb5dd",
            "b98e645769530bff8ec2da6319ac7844a2853e72",
            "d78e1089dfd90dcf74fdc506238cf439ea9d354a",
            "fef0504e23bad186097bf46ff69b36ca9bbb8766",
        }
    ),
    "bin/changelog-render": frozenset(
        {
            "1ae45dcf9604a9e4c961bfece9055483febb4e22",
            "1bb2657355caf01f66011b0dacbfab12ca476ab2",
            "2c8cddee8f997e04ca9ee7f14fc05c94a3838fbf",
            "42b3c55dd56532a665f9f753c800274a2ef2cb7a",
            "62d3b8c965ed58366455c2b60558e7fb1a2273d2",
            "70a61005f61a9a5d0d048502798e25d8acd0e352",
            "7d9f8870eccb51254acf90dd319735415fe65fff",
            "91755cad5e69aafbeeb1167672838a6c7b60e1c9",
            "95fa04838a865c6e264e0562ff54ffd294ff3c6e",
            "a8ed8104af4833dacb444db68f62d401b293838b",
            "c487c76488fdb23b16cc4251013e58e53e174101",
        }
    ),
    # The synced semver script (retired #476). BLOB-ONLY by design: the name
    # collides with the KEPT pip console-script `semver` and with common
    # consumer-authored scripts — never fingerprint or marker-match this dest.
    "bin/semver": frozenset(
        {
            "5e4dca7340577810c5bf1ceefb2098a8a4878565",
            "8312a43759740ac125bdda64d7933a9b2b3a7aca",
        }
    ),
    # The pre-console-script gh scripts (retired #476; now pip console-scripts).
    "bin/gh-task-status": frozenset(
        {
            "2413816060a323697d853edcd65f72dce40b44c9",
            "38fe81eb8d61ada17931d0bfb92d4e30597b0521",
            "944a80f490b0f2fe9b9589281642e4b6cef893c8",
        }
    ),
    "bin/gh-release-issue": frozenset(
        {
            "077c4fa89e8014f6c515e2376c15c702c1fb48f3",
            "159f6ca35403a41decaeaab31d5522c8b1a46017",
            "24954254743ec72bec9398c455c7156dde2ac329",
            "5350cf2c6c08c5588f61d61b8c9324864a38043d",
            "6a2450901e7dfc9aab2bce412b96ff55d063807d",
            "8838a9c96fcb1a0cf7dfcac12c3b5d6672674e09",
        }
    ),
    # The vendored semver-tool (retired #414), at BOTH historical dests
    # (vendor/… and bin/share/…). One blob each — the vendored files never
    # changed. The emptied directory husk is pruned only when the sweep
    # removes its last file.
    "vendor/semver-tool/semver": frozenset({"a16042505af81862afa6d028b72b355c1572d144"}),
    "vendor/semver-tool/LICENSE": frozenset({"261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64"}),
    "vendor/semver-tool/README.md": frozenset({"9951bafcd0b7d484dcf40ae4034e57c72f39c5aa"}),
    "bin/share/semver-tool/semver": frozenset({"a16042505af81862afa6d028b72b355c1572d144"}),
    "bin/share/semver-tool/LICENSE": frozenset({"261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64"}),
    "bin/share/semver-tool/README.md": frozenset({"9951bafcd0b7d484dcf40ae4034e57c72f39c5aa"}),
    # The pre-path-mirror SessionStart script (retired when the bootstrap moved
    # to bin/setup-dev-env.sh). Consumers TAILORED their copies (extras appended
    # below a marker), so the blob set alone misses — the fingerprint entry in
    # RETIRED_FINGERPRINT_FILES covers tailored copies; these blobs cover the
    # verbatim ones (templates/setup-dev-env.sh + templates/commons/scripts/).
    "scripts/setup-dev-env.sh": frozenset(
        {
            "025d3ad81b6366fabd1a51f27460c5515f91e072",
            "05e794c9969abfec9e3a9e343d290c098e505039",
            "13f82fd963e53fc567aa38e31263c029d3fe5ce0",
            "1778a3cee9762cff4f11aa626ea98aaaa4c0c1cb",
            "240b9ebeb7bb2b4fe366499fa030e9c8aa95ab05",
            "29891fc971e1acb3037d7c0a7fa4f3c96d8e2fe4",
            "6699a6e62c3606b12ce0640deb5787683ee9f335",
            "6bdcda3264e5bb935f5f73f2da2d39a1646b635e",
            "6c3770e280254e4b1ca3a28ef24ba2cb77cede19",
            "6c6bba5886042578dd8a9001bfc57404cb67145f",
            "7ae2a4c5a4702942e1db5ed66c02ca9d80c467ba",
            "8d28dd149d9ac553e28c8a53986ce5533564db1a",
            "9474047cc270d74aaf63e3733ce2c5bcc0cc0c79",
            "ad4d521892761e61a54c14b94a38ab661460f0fc",
            "b5bee9aeb96bb4cdcf0a9032154d41ad299dc447",
            "cbf6332226b18880f71e67932963a87247ee9273",
            "cf511b5115d0ca20df3e49a712c4b4553ce95783",
            "d2a4154fa2c447aa018b1f7ab3d69a1804da40ef",
            "e35ec7e2dbaf1bd38ad9df26d4653e86f4a4a13d",
            "e9e249ac0d99d856beba8a74955da5cdbc09ccb7",
            "ed83d25608c54f04ace1ee59d343ed17e644dfca",
        }
    ),
    # The root lefthook.yml REAL-FILE seeds ONLY (pre-compose era; WS3 #524
    # moved the gate into .release/). HIGHEST collision risk on this list —
    # consumers (and release itself) legitimately author this file — so ONLY
    # the two exact seed blobs are matched; composed-era copies (varying
    # generated header → no stable blob) are deliberately NOT covered.
    "lefthook.yml": frozenset(
        {
            "e64de6321351b7247f38631a20bf46f63d68597c",  # templates/rust seed
            "55cb72795267855dd0c02a782e7b40738f4e1991",  # templates/go seed
        }
    ),
    # ORIENTATION.md (retired WS2 #523; release#563): every blob
    # templates/commons/ORIENTATION.md ever held. Pre-WS4 seeds still TRACK
    # this in the consumer; the recompose removes the on-disk copy, the WS4
    # untracking removes the index entry, and this retired-file entry pins the
    # dest in the managed commit pathspec so the deletion is recorded explicitly.
    ".release/ORIENTATION.md": frozenset(
        {
            "0efc67ac39d2fdb796977400582c07def0f720be",
            "14e47858107003ac5c8a7f00f12fa3daa12872db",
            "1c24c4ebb09d5028d09344d73d10b9a1fab0ab08",
            "1e7637e571ff743a6e3900da46735cc07fa007ac",
            "71b01946adbe28bc0c406d5175bc6cb36f88b758",
            "a94c5f6828aeebfba7b46b1977bed4f4bb7c9d90",
            "b8828dd6c9bbe6242ff29b42a5df24184de41d89",
            "be19a263d761659713f350e796eb4bd519eac103",
            "c7b5ac95bcadae345e46c122440dcbfa3a048801",
            "d11d02ff8d5d90e4a604b78365c08d7bd3f89684",
            "df499a28311ba0b180a823dfae5f8881495c0e9d",
            "eb0cd6aef6322f24367f1d4f2475b88229ec7f89",
        }
    ),
    # The packaged-binary smoke hooks (retired release#590): dead since WS7
    # (#528) made the mirrors untracked — `hashFiles('app-bin/smoke-hook.sh')`
    # on a plain checkout never matched, so the hook never executed; #592
    # removed the dead workflow branches and this retired-file entry sweeps the seeded
    # copies. One dest, both kinds' blobs (electron-app + tauri-app shipped
    # the same path). A consumer-OVERRIDDEN hook (the documented customization
    # path) no longer matches a shipped blob and is left alone.
    "app-bin/smoke-hook.sh": frozenset(
        {
            "e66cefd08e3fdd2bc196d7ddd2273092045e46fa",  # templates/electron-app
            "0d51b774a26ea753285c180d7918c439d3e7abf1",  # templates/tauri-app
        }
    ),
    # The same hooks at their pre-#270 dest (templates/<kind>/scripts/smoke.sh,
    # moved to app-bin/ in #270). BLOB-ONLY on purpose: `scripts/smoke.sh` is a
    # plausible consumer-owned name — never marker/fingerprint this dest.
    "scripts/smoke.sh": frozenset(
        {
            "746a5c585fed3fd3627ff1fc2d50141b6437ff58",  # templates/electron-app (earlier)
            "b1fd39538285eb97647aadd4980c789dbcb4d14b",  # templates/electron-app
            "8c59bc21b47bd5b49cf61518ac9fdd5892b7adfc",  # templates/tauri-app
        }
    ),
}

RETIRED_FINGERPRINT_FILES: dict[str, str] = {
    # The release-cut caller (retired by the CLI cutover, #468/#476): body was
    # tailored per repo at onboarding, header line is verbatim everywhere. The
    # string value is the verbatim fingerprint matched against consumer files —
    # it is data, not prose, and must stay byte-exact.
    "bin/release": "Thin shim around the canonical release-cut CLI",
    # The pre-path-mirror SessionStart script: tailored per repo (extras
    # appended below the rsync marker), so the blob set misses tailored
    # copies — the header comment is verbatim across every template revision
    # (verified against all 21 historical blobs) and across the fleet's
    # tailored copies. The whole script is dead either way: nothing invokes
    # scripts/ since the bootstrap moved to bin/setup-dev-env.sh.
    "scripts/setup-dev-env.sh": "scripts/setup-dev-env.sh — per-session dev-environment setup",
}


def _git_blob_sha1(path: str) -> str | None:
    """The git blob SHA-1 of the file's content (sha1 over ``blob <len>\\0`` +
    bytes — what ``git hash-object`` computes), so a retired-file entry can match
    the consumer's tracked blob without shelling out to git."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    h = hashlib.sha1(b"blob %d\x00" % len(data))
    h.update(data)
    return h.hexdigest()


def _find_retired_files(repo_root: str) -> list[str]:
    """Retired dests present as REAL files whose provenance confirms they are
    release's retired copies. Symlinks are skipped (the broken-symlink sweep owns
    those); provenance misses are skipped (consumer-authored, or consumer-modified
    enough to no longer match — either way not ours to delete). De-duplicated:
    a dest may carry BOTH a blob set and a fingerprint (scripts/setup-dev-env.sh)
    and must still appear once."""
    out: set[str] = set()
    for dest, blobs in RETIRED_BLOB_FILES.items():
        full = os.path.join(repo_root, dest)
        if os.path.isfile(full) and not os.path.islink(full) and _git_blob_sha1(full) in blobs:
            out.add(dest)
    for dest, needle in RETIRED_FINGERPRINT_FILES.items():
        full = os.path.join(repo_root, dest)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        if _has_fingerprint_header(full, needle):
            out.add(dest)
    return sorted(out)


def _has_fingerprint_header(path: str, needle: str) -> bool:
    """True iff one of the file's first lines is a COMMENT starting with the
    verbatim fingerprint — `# <needle>…`. Header-anchored on purpose: a loose
    substring search could false-positive on a consumer-owned file that merely
    mentions the phrase somewhere in its body. The retired scripts carry it as the
    first comment under the shebang; 10 lines is generous slack."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = [fh.readline() for _ in range(10)]
    except OSError:
        return False
    return any(line.startswith(f"# {needle}") for line in head)


# ── CLAUDE.md @import (#348, WS4 #761) ────────────────────────────────────────
#
# The orientation is delivered as a managed TARGET file
# (`.claude/IMPORTANT-RELEASE.md`) plus a one-line `@import` of it in CLAUDE.md —
# replacing the pre-WS4 splice that injected a BEGIN..END block into CLAUDE.md.
# Two parts, with strict ownership:
#   - target_action  → write/none for `.claude/IMPORTANT-RELEASE.md` (MANAGED,
#                      committed by init's auto-commit, refreshed on the pull).
#   - import_action  → insert/none for the CLAUDE.md `@import` line. init writes
#                      the line to disk when absent (so loading works) but NEVER
#                      stages/commits CLAUDE.md — after insertion CLAUDE.md is 100%
#                      consumer-owned; folding it into the managed commit would
#                      drag in the consumer's unrelated uncommitted edits.
# Atomicity (safety rule #2): _apply_mirror writes the target file AND the import
# line together, target first — there is never a window with a dangling @import.


@dataclass
class ClaudeDecision:
    # The managed target file `.claude/IMPORTANT-RELEASE.md`:
    #   write → (re)write it (content in ``target_desired``); none → already current.
    target_action: str = "none"  # write | none
    target_desired: str | None = None
    # The CLAUDE.md `@import` line:
    #   create → CLAUDE.md is ABSENT: write it as the pure one-line pointer. Safe
    #            to STAGE (no consumer content to fold in — it IS 100% the managed
    #            line), so the fresh-seed tree stays clean.
    #   insert → CLAUDE.md EXISTS: strip any pre-WS4 managed block, then prepend the
    #            one line. Written to disk but NEVER staged — CLAUDE.md is
    #            consumer-owned, and staging would fold in the consumer's unrelated
    #            uncommitted edits.
    #   none   → the @import line is already present.
    #   skip-symlink → CLAUDE.md is a symlink, leave it alone.
    import_action: str = "none"  # create | insert | none | skip-symlink
    import_content: str | None = None  # the full CLAUDE.md bytes to write


def decide_claude(repo_root: str, tmp_release: str) -> ClaudeDecision:
    """Decide the managed target write + the CLAUDE.md `@import` insertion.

    ``tmp_release`` is unused (kept for signature stability). The managed target
    file always carries the current header; the `@import` line is inserted into
    CLAUDE.md only when it is not already present (an existing line is left
    untouched — CLAUDE.md is consumer-owned once seeded).
    """
    # 1. The managed target file: write iff content differs from what's on disk.
    target_path = os.path.join(repo_root, CLAUDE_IMPORT_TARGET)
    target_action = "none"
    if not os.path.isfile(target_path) or _read_text(target_path) != CLAUDE_IMPORT_BODY:
        target_action = "write"

    decision = ClaudeDecision(
        target_action=target_action,
        target_desired=CLAUDE_IMPORT_BODY if target_action == "write" else None,
    )

    # 2. The CLAUDE.md `@import` line.
    claude_path = os.path.join(repo_root, CLAUDE_FILE)
    if os.path.islink(claude_path):
        decision.import_action = "skip-symlink"
        return decision

    if not os.path.lexists(claude_path):
        # No CLAUDE.md yet → create it as the one-line pointer. This file IS the
        # managed line (no consumer content), so it is safe to stage+commit — the
        # fresh-seed tree stays clean.
        decision.import_action = "create"
        decision.import_content = f"{CLAUDE_IMPORT_LINE}\n"
        return decision

    existing = _read_text(claude_path)
    if _has_import_line(existing) and not _has_claude_begin(existing):
        # Already a clean pointer + consumer content; nothing to do.
        decision.import_action = "none"
        return decision

    # Present but missing the import line (or still carrying a pre-WS4 spliced
    # block): strip any managed block, then PREPEND the one-line @import.
    if _has_claude_begin(existing):
        rest = _strip_managed_block_text(existing)
    else:
        rest = existing.rstrip("\n")
    content = f"{CLAUDE_IMPORT_LINE}\n"
    if rest:
        content += f"\n{rest}\n"
    decision.import_action = "insert"
    decision.import_content = content
    return decision


def _strip_managed_block_text(text: str) -> str:
    """Strip a pre-WS4 spliced BEGIN..END managed block from CLAUDE.md text,
    dropping leading blank lines, returning the rest WITHOUT a trailing newline.
    Used to migrate an already-seeded consumer off the old splice onto the
    one-line @import."""
    lines = text.split("\n")
    kept: list[str] = []
    skip = False
    for line in lines:
        if _has_claude_begin(line):
            skip = True
        if not skip:
            kept.append(line)
        if CLAUDE_END in line:
            skip = False
    while kept and kept[0] == "":
        kept.pop(0)
    return "\n".join(kept).rstrip("\n")


def _has_import_line(text: str) -> bool:
    """True iff CLAUDE.md already imports the managed target — the verbatim
    `@.claude/IMPORTANT-RELEASE.md` line appears (as its own line)."""
    return any(line.strip() == CLAUDE_IMPORT_LINE for line in text.split("\n"))


def _has_claude_begin(text: str) -> bool:
    """True iff ``text`` carries a pre-WS4 managed-block BEGIN marker — the
    CLAUDE_BEGIN OR the legacy CLAUDE_BEGIN_LEGACY. Used only to migrate an
    existing spliced block onto the one-line @import."""
    return CLAUDE_BEGIN in text or CLAUDE_BEGIN_LEGACY in text


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# A few constants other modules / tests may want.
_LEFTHOOK_HEADER_RE = re.compile(r"^# Generated by release")
