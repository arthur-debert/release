"""Hatch build hook: bundle the FULL managed files into the wheel.

`release-core init` installs per-repo managed content from these bundled
sources. For the pull-model the wheel must be self-contained — it ships the
template DATA (and the distributable skills) so `init` needs no release clone.

The sources live OUTSIDE the package build root (templates at <repo>/templates/…,
skills at <repo>/skills/…), so we stage them into
release_core/_bundled_templates/ at build time; hatch then packages it like any
in-package data. The staged dir is gitignored (a build artifact, never
committed) and `finalize()` removes it post-build so an editable/source checkout
never carries a stale bundle (a stale bundle silently flips init to the offline
path).

What gets bundled (release#476, part 1 — full-tree bundling):
  - The FULL template tree: all of templates/commons/, all of
    templates/components/, and EACH per-kind dir templates/<kind>/ — copied
    whole (directories, not just the few config files), into
    _bundled_templates/templates/<same-rel-path>.
  - The DISTRIBUTED skills only — the PUSH_ALL_SKILLS + REPLACE_IF_PRESENT_SKILLS
    catalog defined in release_core.sync — from <repo>/skills/<name>/ into
    _bundled_templates/skills/<name>/. Release-only skills are NOT shipped.

Recursion guard: templates/commons/lib/release_core/ IS this very package (the
build root). It is excluded entirely so the bundle does not contain a copy of
itself. Other templates/commons/lib/ content is kept.

NOTE — config subset vs. full tree: `init` currently CONSUMES only the
config subset listed in _COMMONS_CONFIGS (lefthook.yml + lint configs) plus
the per-kind/capability lefthook fragments + manifests. The rest of the
now-bundled tree is PRESENT but unused until step 2 (#476) teaches `init`
to do a full offline install. This part-1 change is purely additive data.
"""

from __future__ import annotations

import ast
import os
import shutil

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Files under templates/commons/ that init currently CONSUMES verbatim
# (kind-independent config subset). The full commons/ tree is now bundled too
# (see module docstring); this list documents the subset init reads today.
_COMMONS_CONFIGS = (
    ".markdownlint.json",
    ".markdownlintignore",
    ".yamllint",
    ".shellcheckrc",
    ".editorconfig",
    ".prettierignore",
    "lefthook.fragment.yaml",
)

_BUNDLE_DIRNAME = "_bundled_templates"

# Junk that must never land in the bundle (mirrors
# release_core.sync.should_skip_source where it concerns build artifacts/cruft).
# Includes the local dev-tool cache dirs (.pytest_cache/.ruff_cache/.mypy_cache):
# gitignored at repo root, but copytree reads the working tree, so a local build
# could otherwise pick them up and bloat the wheel.
_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        _BUNDLE_DIRNAME,
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
_SKIP_FILE_SUFFIXES = (".pyc", ".pyo")
_SKIP_FILE_NAMES = frozenset({".DS_Store"})

# Top-level templates/ dirs that are NOT part of the managed init surface
# and must not be bundled. `render/` holds render-side material (the Homebrew
# formula template) consumed at release-cut in CI, not a per-repo managed Kind —
# init only ever installs from commons/, components/, and a resolved Kind dir
# (one carrying a manifest.yaml). Bundling render/ is dead weight.
_NON_SURFACE_TEMPLATE_DIRS = frozenset({"render"})


def _skip_names(_dir: str, names: list[str]) -> set[str]:
    """`shutil.copytree(ignore=…)` callback: drop bytecode dirs/files, .git,
    .DS_Store, and the bundle dir itself, consistently at every depth."""
    drop: set[str] = set()
    for n in names:
        if n in _SKIP_DIR_NAMES or n in _SKIP_FILE_NAMES or n.endswith(_SKIP_FILE_SUFFIXES):
            drop.add(n)
    return drop


def _distributed_skills(repo_root: str) -> list[str]:
    """The skill catalog to ship to consumers, read from the single source of
    truth (release_core.sync: PUSH_ALL_SKILLS + REPLACE_IF_PRESENT_SKILLS).

    Parsed statically with `ast` from sync.py rather than imported: importing the
    package would run release_core/__init__.py (which pulls in click via the CLI
    subpackage) and need it installed at build time. Static parse avoids that
    while still reading the source lists — an out-of-sync hand-copy is a bug."""
    sync_py = os.path.join(
        repo_root, "templates", "commons", "lib", "release_core", "release_core", "sync.py"
    )
    with open(sync_py, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=sync_py)
    wanted = {"PUSH_ALL_SKILLS", "REPLACE_IF_PRESENT_SKILLS"}
    found: dict[str, list[str]] = {}
    for node in tree.body:
        # Handle both plain (`X = [...]`) and annotated (`X: list[str] = [...]`)
        # assignments so a future type annotation on the catalogs can't silently
        # break the parse.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    found[target.id] = ast.literal_eval(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
            and node.target.id in wanted
        ):
            found[node.target.id] = ast.literal_eval(node.value)
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(
            f"hatch_build: could not parse skill catalog {sorted(missing)} from {sync_py!r}"
        )
    # De-dup defensively while preserving order; the two lists are disjoint today.
    seen: set[str] = set()
    out: list[str] = []
    for name in found["PUSH_ALL_SKILLS"] + found["REPLACE_IF_PRESENT_SKILLS"]:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        # self.root = the project dir (…/templates/commons/lib/release_core).
        repo_templates = os.path.normpath(os.path.join(self.root, "..", "..", ".."))  # …/templates
        if os.path.basename(repo_templates) != "templates":
            raise RuntimeError(f"hatch_build: expected …/templates, got {repo_templates!r}")
        repo_root = os.path.dirname(repo_templates)  # …/<repo>

        bundle_root = os.path.join(self.root, "release_core", _BUNDLE_DIRNAME)
        dest_templates = os.path.join(bundle_root, "templates")
        dest_skills = os.path.join(bundle_root, "skills")
        shutil.rmtree(bundle_root, ignore_errors=True)

        # THE recursion/bloat guard: templates/commons/lib/release_core IS this
        # package (the build root). Copy it into itself and the wheel balloons /
        # nests forever. Exclude that one subtree; keep the rest of commons/lib.
        pkg_subtree = os.path.normpath(
            os.path.join(repo_templates, "commons", "lib", "release_core")
        )

        def _copytree(src: str, dst: str) -> None:
            """Copy a directory tree, skipping junk and the package subtree."""

            def ignore(cur_dir: str, names: list[str]) -> set[str]:
                drop = _skip_names(cur_dir, names)
                # Drop the package subtree wherever it appears under commons/lib.
                for n in names:
                    if os.path.normpath(os.path.join(cur_dir, n)) == pkg_subtree:
                        drop.add(n)
                return drop

            shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True, symlinks=False)

        # 1. FULL template tree: commons/, components/, and each per-kind dir.
        for entry in sorted(os.listdir(repo_templates)):
            src = os.path.join(repo_templates, entry)
            if not os.path.isdir(src):
                continue
            if entry in _SKIP_DIR_NAMES or entry in _NON_SURFACE_TEMPLATE_DIRS:
                continue
            _copytree(src, os.path.join(dest_templates, entry))

        # 2. DISTRIBUTED skills only (from <repo>/skills/<name>/) — never the
        #    whole skills/ dir; release-only skills must not ship to consumers.
        skills_root = os.path.join(repo_root, "skills")
        for name in _distributed_skills(repo_root):
            src = os.path.join(skills_root, name)
            if not os.path.isdir(src):
                # A catalog name with no source dir is a packaging bug — fail loud.
                raise RuntimeError(f"hatch_build: distributed skill {name!r} has no dir at {src!r}")
            _copytree(src, os.path.join(dest_skills, name))

        # Ensure hatch includes the staged tree in the wheel.
        build_data.setdefault("artifacts", []).append(f"release_core/{_BUNDLE_DIRNAME}/**")

    def finalize(self, version, build_data, artifact_path):
        # The staged tree only needs to exist WHILE hatch packages the wheel.
        # Remove it afterwards so a build leaves no trace in the source tree —
        # otherwise a stale bundle next to an editable install would make
        # `release-core init` silently take the offline path. It is gitignored,
        # so this is hygiene, not correctness for git; ignore_errors keeps a
        # cleanup hiccup from failing an otherwise-good build.
        shutil.rmtree(os.path.join(self.root, "release_core", _BUNDLE_DIRNAME), ignore_errors=True)
