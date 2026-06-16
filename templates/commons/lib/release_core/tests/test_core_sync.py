"""release_sync engine: the pure logic in release_core.sync.

Fixture-driven, no network. Git access (gh.git_*) is monkeypatched at the data
layer — recorded ls-tree/cat-file/show results — never at subprocess. The
filesystem-walk + symlink + CLAUDE.md helpers run against real tmp_path trees.

These pin the byte-for-byte contract: ref-selection precedence, capability
resolution, the plan/lefthook composition order, is_release_internal
classification, relative symlink-target math, broken-symlink detection, the
find-style traversal order, and the orientation-block computation.
"""

from __future__ import annotations

import os
import shutil

import pytest
from release_core import sync

# ── Classification predicates ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rel", "skip"),
    [
        ("templates/commons/lefthook.fragment.yaml", True),
        ("templates/rust-cli/manifest.yaml", True),
        ("templates/components/_lefthook-base.yaml", True),
        ("templates/commons/.DS_Store", True),
        # Bytecode never materializes into a consumer's .release/ (release#450).
        ("templates/commons/lib/release_core/release_core/__pycache__/cli.cpython-313.pyc", True),
        ("templates/commons/lib/release_core/release_core/sync.pyc", True),
        ("templates/commons/lib/release_core/release_core/sync.pyo", True),
        # path-segment match, not a loose substring: a file merely *named* with
        # the substring is kept (it's a real authored source, not bytecode).
        ("templates/commons/docs/my__pycache__notes.md", False),
        ("templates/commons/bin/check", False),
        ("templates/commons/lefthook.yml", False),
    ],
)
def test_should_skip_source(rel, skip):
    assert sync.should_skip_source(rel) is skip


@pytest.mark.parametrize(
    ("dest", "real"),
    [
        (".github/workflows/release.yml", True),
        (".github/workflows/ci.yaml", True),
        ("bin/check", False),
        (".github/dependabot.yml", False),
    ],
)
def test_needs_real_file(dest, real):
    assert sync.needs_real_file(dest) is real


@pytest.mark.parametrize(
    ("dest", "internal"),
    [
        (".release-sync-source", True),
        (".gitignore", True),  # managed .release/.gitignore — release#450
        ("lib/release_core/release_core/sync.py", True),
        # the PR state engine folded into release_core (release#459); its files
        # are now under lib/release_core/ and covered by the branch above.
        ("lib/release_core/release_core/prstate/state.py", True),
        # NOT internal — consumer-facing lib/ + everything else. ORIENTATION.md was
        # retired in WS2 (#523); it's no longer composed, so not a special case.
        ("ORIENTATION.md", False),
        ("lib/bats-harness.bash", False),
        ("bin/check-shell", False),
        ("lib/release_other/x.py", False),
        ("docs/ORIENTATION.md", False),
    ],
)
def test_is_release_internal(dest, internal):
    assert sync.is_release_internal(dest) is internal


# ── read_source_tag: the resolved-release stamp in the tool venv (#580) ───────


def test_read_source_tag_reads_the_venv_stamp(tmp_path, monkeypatch):
    monkeypatch.delenv("RELEASE_CORE_SOURCE_TAG", raising=False)
    monkeypatch.setattr(sync.sys, "prefix", str(tmp_path))
    (tmp_path / sync.SOURCE_TAG_FILE).write_text("v2.17.1\n")
    assert sync.read_source_tag() == "v2.17.1"


def test_read_source_tag_absent_or_blank_is_none(tmp_path, monkeypatch):
    # No stamp (a wheel installed by an older resolver) → None, the fallback.
    monkeypatch.delenv("RELEASE_CORE_SOURCE_TAG", raising=False)
    monkeypatch.setattr(sync.sys, "prefix", str(tmp_path))
    assert sync.read_source_tag() is None
    (tmp_path / sync.SOURCE_TAG_FILE).write_text("\n")
    assert sync.read_source_tag() is None


def test_read_source_tag_env_override_wins(tmp_path, monkeypatch):
    # The env var, when SET, shadows the file (the test channel); empty means
    # "no stamp" so tests can neutralize a real host venv stamp.
    monkeypatch.setattr(sync.sys, "prefix", str(tmp_path))
    (tmp_path / sync.SOURCE_TAG_FILE).write_text("v2.16.0\n")
    monkeypatch.setenv("RELEASE_CORE_SOURCE_TAG", "v2.17.1")
    assert sync.read_source_tag() == "v2.17.1"
    monkeypatch.setenv("RELEASE_CORE_SOURCE_TAG", "")
    assert sync.read_source_tag() is None


# ── Symlink target computation (relative, path-mirror) ────────────────────────


@pytest.mark.parametrize(
    ("dest", "target"),
    [
        ("lefthook.yml", ".release/lefthook.yml"),
        (".editorconfig", ".release/.editorconfig"),
        ("bin/check", "../.release/bin/check"),
        (".claude/skills/x/SKILL.md", "../../../.release/.claude/skills/x/SKILL.md"),
        ("bin/check-shell", "../.release/bin/check-shell"),
    ],
)
def test_link_target(dest, target):
    assert sync.link_target(dest) == target


# ── BundleSource (filesystem-backed source) ───────────────────────────────────


def test_bundle_source_list_tree_modes_and_paths(tmp_path):
    # Layout: bundle_root/templates/commons/{bin/check (exec), .editorconfig}.
    root = tmp_path / "bundle"
    (root / "templates" / "commons" / "bin").mkdir(parents=True)
    cfg = root / "templates" / "commons" / ".editorconfig"
    cfg.write_text("x\n")
    tool = root / "templates" / "commons" / "bin" / "check"
    tool.write_text("#!/bin/sh\n")
    os.chmod(tool, 0o755)

    src = sync.BundleSource(str(root))
    listing = dict(src.list_tree("templates/commons"))
    assert listing["templates/commons/.editorconfig"] == "100644"
    assert listing["templates/commons/bin/check"] == "100755"
    # rel paths are git-style (subtree-prefixed, '/'-separated).
    assert all(k.startswith("templates/commons/") for k in listing)
    # deterministic sorted order regardless of readdir.
    rels = [r for r, _ in src.list_tree("templates/commons")]
    assert rels == sorted(rels)


def test_bundle_source_missing_subtree_is_empty(tmp_path):
    root = tmp_path / "bundle"
    (root / "templates").mkdir(parents=True)
    assert sync.BundleSource(str(root)).list_tree("templates/nope") == []


def test_bundle_source_read_bytes_and_exists(tmp_path):
    root = tmp_path / "bundle"
    (root / "skills" / "tdd").mkdir(parents=True)
    (root / "skills" / "tdd" / "SKILL.md").write_bytes(b"# tdd\n")
    src = sync.BundleSource(str(root))
    assert src.exists("skills/tdd/SKILL.md")
    assert not src.exists("skills/tdd/nope.md")
    assert src.read_bytes("skills/tdd/SKILL.md") == b"# tdd\n"


def test_bundle_source_ref_sha_and_label(tmp_path):
    src = sync.BundleSource(str(tmp_path), ref_sha="release-core 1.2.3")
    assert src.ref_sha == "release-core 1.2.3"
    assert src.label == "release-core 1.2.3"
    assert sync.BundleSource(str(tmp_path)).label == "wheel bundle"


def test_bundle_source_refuses_path_traversal(tmp_path):
    # Capability names flow in from consumer YAML; a "../.." must not escape the
    # bundle root (path-traversal / arbitrary-file read).
    root = tmp_path / "bundle"
    (root / "templates").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("top secret\n")
    src = sync.BundleSource(str(root))
    with pytest.raises(sync.SyncError, match="escapes the bundle root"):
        src.exists("templates/components/../../../secret.txt")
    with pytest.raises(sync.SyncError, match="escapes the bundle root"):
        src.read_bytes("templates/components/../../../secret.txt")
    with pytest.raises(sync.SyncError, match="escapes the bundle root"):
        src.list_tree("templates/../../elsewhere")


# ── Ref selection precedence ──────────────────────────────────────────────────


def _fake_gh(monkeypatch, *, existing_refs, sha="deadbeef"):
    """Patch gh.git_rev_parse_verify / git_fetch_prune / git_rev_parse so
    select_ref runs offline against a known set of resolvable refs."""
    calls = {"fetched": False}

    def verify(ref, *, cwd):
        return ref in existing_refs

    def fetch(*, cwd, remote="origin"):
        calls["fetched"] = True

    monkeypatch.setattr(sync.gh, "git_rev_parse_verify", verify)
    monkeypatch.setattr(sync.gh, "git_fetch_prune", fetch)
    monkeypatch.setattr(sync.gh, "git_rev_parse", lambda ref, *, cwd: sha)
    return calls


def test_select_ref_explicit_release_ref_validated(monkeypatch):
    _fake_gh(monkeypatch, existing_refs={"my-tag"})
    assert sync.select_ref("/home", "repo", "rust-cli", "my-tag") == "my-tag"


def test_select_ref_explicit_release_ref_invalid_raises(monkeypatch):
    _fake_gh(monkeypatch, existing_refs=set())
    with pytest.raises(sync.SyncError, match="not a valid ref"):
        sync.select_ref("/home", "repo", "rust-cli", "bogus")


def test_select_ref_explicit_skips_fetch(monkeypatch):
    calls = _fake_gh(monkeypatch, existing_refs={"x"})
    sync.select_ref("/home", "repo", "rust-cli", "x")
    assert calls["fetched"] is False


def test_select_ref_prefers_repo_name_branch(monkeypatch):
    _fake_gh(
        monkeypatch,
        existing_refs={
            "refs/remotes/origin/release/beta/myrepo",
            "refs/remotes/origin/release/beta/rust-cli",
            "refs/remotes/origin/main",
        },
    )
    assert sync.select_ref("/home", "myrepo", "rust-cli", None) == "origin/release/beta/myrepo"


def test_select_ref_falls_to_kind_branch(monkeypatch):
    _fake_gh(
        monkeypatch,
        existing_refs={
            "refs/remotes/origin/release/beta/rust-cli",
            "refs/remotes/origin/main",
        },
    )
    assert sync.select_ref("/home", "myrepo", "rust-cli", None) == "origin/release/beta/rust-cli"


def test_select_ref_falls_to_main(monkeypatch):
    _fake_gh(monkeypatch, existing_refs={"refs/remotes/origin/main"})
    assert sync.select_ref("/home", "myrepo", "rust-cli", None) == "origin/main"


def test_select_ref_fetches_when_unset(monkeypatch):
    calls = _fake_gh(monkeypatch, existing_refs={"refs/remotes/origin/main"})
    sync.select_ref("/home", "myrepo", "rust-cli", None)
    assert calls["fetched"] is True


def test_select_ref_no_candidate_raises(monkeypatch):
    _fake_gh(monkeypatch, existing_refs=set())
    with pytest.raises(sync.SyncError, match="no candidate branch"):
        sync.select_ref("/home", "myrepo", "rust-cli", None)


# ── Capability resolution ─────────────────────────────────────────────────────


def _git_source(release_home="/home", ref="ref", ref_sha="deadbeef"):
    """A GitSource for the engine tests. It delegates to gh.git_*, so the existing
    monkeypatches at sync.gh.* keep driving the data layer unchanged — the source
    abstraction is byte-transparent over the original git path."""
    return sync.GitSource(release_home, ref, ref_sha)


def test_resolve_capabilities_consumer_override(monkeypatch):
    monkeypatch.setattr(sync, "_yq_list_capabilities", lambda text: ["mkdocs", "bats"])
    caps = sync.resolve_capabilities(
        _git_source(), "docs-site", sync_yaml_text="capabilities:\n  - mkdocs\n  - bats\n"
    )
    assert caps.names == ["mkdocs", "bats"]
    assert caps.manifest_source == ".release-sync.yaml (consumer override)"


def test_resolve_capabilities_kind_manifest(monkeypatch):
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: True)
    monkeypatch.setattr(sync.gh, "git_show_bytes", lambda rp, *, cwd: b"capabilities:\n  - x\n")
    monkeypatch.setattr(sync, "_yq_list_capabilities", lambda text: ["x"])
    caps = sync.resolve_capabilities(_git_source(), "rust-cli", sync_yaml_text=None)
    assert caps.names == ["x"]
    assert caps.manifest_source == "templates/rust-cli/manifest.yaml (Kind default)"


def test_resolve_capabilities_manifestless(monkeypatch):
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    caps = sync.resolve_capabilities(_git_source(), "tree-sitter", sync_yaml_text=None)
    assert caps.names == []
    assert caps.manifest_source == "(none — manifest-less Kind; commons + Kind only)"


def test_validate_capabilities_missing_tree_raises(monkeypatch):
    # cheap existence probe → git_cat_file_exists; absent tree raises.
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    with pytest.raises(sync.SyncError, match="has no templates/components/ghost/"):
        sync.validate_capabilities(_git_source(), ["ghost"])


@pytest.mark.skipif(shutil.which("yq") is None, reason="`yq` not on PATH")
def test_resolve_capabilities_malformed_yaml_raises_yamlerror():
    # A malformed consumer override drives the real yq seam to a parse error,
    # which yamlio surfaces as YamlError; the verb catches it at the CLI boundary.
    from release_core import yamlio

    with pytest.raises(yamlio.YamlError):
        sync.resolve_capabilities(
            _git_source(), "docs-site", sync_yaml_text="capabilities: [a, b\n  : : :\n"
        )


def test_validate_capabilities_ok(monkeypatch):
    # The templates/components/x tree exists → cheap existence probe passes.
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: True)
    sync.validate_capabilities(_git_source(), ["x"])  # no raise


# ── subtree precedence + plan composition order ───────────────────────────────


def test_subtree_list_order():
    assert sync.subtree_list("rust-cli", ["a", "b"]) == [
        "templates/commons",
        "templates/components/a",
        "templates/components/b",
        "templates/rust-cli",
    ]


def test_build_plan_precedence_last_write_wins(monkeypatch):
    """A dest present in both commons and the kind subtree resolves to the kind's
    source (kind is later in precedence) but keeps its first-seen order slot."""
    trees = {
        "templates/commons": "100644 blob aaa\ttemplates/commons/bin/check\n"
        "100644 blob bbb\ttemplates/commons/lefthook.fragment.yaml\n",
        "templates/rust-cli": "100755 blob ccc\ttemplates/rust-cli/bin/check\n",
    }

    def ls_tree(ref, path, *, cwd, recursive=False, dirs_only=False, name_only=False):
        return trees.get(path, "")

    monkeypatch.setattr(sync.gh, "git_ls_tree", ls_tree)
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)

    plan = sync.build_plan(_git_source(), "rust-cli", [])
    assert plan.order == ["bin/check"]  # fragment skipped; single dest
    assert plan.source["bin/check"] == "templates/rust-cli/bin/check"  # last wins
    assert plan.mode["bin/check"] == "100755"


def _skill_tree_ls(skill_files):
    """Build a git_ls_tree fake that serves recursive listings for skills/<name>
    from a {skill_name: [subpath, ...]} map, and "" for everything else."""

    def ls_tree(ref, path, *, cwd, recursive=False, dirs_only=False, name_only=False):
        if path.startswith("skills/"):
            name = path[len("skills/") :]
            subs = skill_files.get(name)
            if not subs:
                return ""
            return "".join(
                f"100644 blob {i:040x}\tskills/{name}/{sub}\n" for i, sub in enumerate(subs)
            )
        return ""

    return ls_tree


def test_build_plan_distributes_push_all_skills(monkeypatch):
    """Every PUSH_ALL skill that exists at the ref materializes whole-directory:
    each file under skills/<name>/ → .claude/skills/<name>/<subpath>."""
    files = {name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS}
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    for name in sync.PUSH_ALL_SKILLS:
        dest = f".claude/skills/{name}/SKILL.md"
        assert dest in plan.order
        assert plan.source[dest] == f"skills/{name}/SKILL.md"
        assert plan.mode[dest] == "100644"


def test_build_plan_multifile_skill_distributes_all_files(monkeypatch):
    """A multi-file skill (extra .md alongside SKILL.md) reaches the consumer in
    full, not just its SKILL.md. Uses a kept PUSH_ALL skill (gh-pr-review-loop)."""
    multi = "gh-pr-review-loop"
    files = {
        multi: ["SKILL.md", "mocking.md", "tests.md", "refactoring.md"],
        # the rest exist with just SKILL.md so the loop is well-formed
        **{name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS if name != multi},
    }
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    for sub in ("SKILL.md", "mocking.md", "tests.md", "refactoring.md"):
        dest = f".claude/skills/{multi}/{sub}"
        assert dest in plan.order
        assert plan.source[dest] == f"skills/{multi}/{sub}"


def test_build_plan_tolerates_missing_skill_dir(monkeypatch):
    """A PUSH_ALL skill whose dir is absent at the ref is silently skipped."""
    # Only gh-pr-review-loop exists; the rest return "" (missing).
    files = {"gh-pr-review-loop": ["SKILL.md"]}
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    assert ".claude/skills/gh-pr-review-loop/SKILL.md" in plan.order
    # A missing skill contributes nothing.
    assert ".claude/skills/diagnose/SKILL.md" not in plan.order


def test_build_plan_replace_if_present_only_when_consumer_has_it(monkeypatch, tmp_path):
    """REPLACE_IF_PRESENT skills are synced ONLY when the consumer already carries
    .claude/skills/<name>; otherwise they are not added to the plan."""
    files = {name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS}
    files.update({name: ["SKILL.md"] for name in sync.REPLACE_IF_PRESENT_SKILLS})
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)

    # Consumer already carries lex-primer (real dir) but not the others.
    have = sync.REPLACE_IF_PRESENT_SKILLS[0]
    (tmp_path / ".claude" / "skills" / have).mkdir(parents=True)

    plan = sync.build_plan(_git_source(), "tree-sitter", [], repo_root=str(tmp_path))
    assert f".claude/skills/{have}/SKILL.md" in plan.order
    for name in sync.REPLACE_IF_PRESENT_SKILLS[1:]:
        assert f".claude/skills/{name}/SKILL.md" not in plan.order


def test_build_plan_replace_if_present_detects_symlink(monkeypatch, tmp_path):
    """An existing .claude/skills/<name> SYMLINK also counts as present."""
    files = {name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS}
    files.update({name: ["SKILL.md"] for name in sync.REPLACE_IF_PRESENT_SKILLS})
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)

    name = sync.REPLACE_IF_PRESENT_SKILLS[0]
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    os.symlink("/nowhere", str(skills_dir / name))  # dangling symlink still counts

    plan = sync.build_plan(_git_source(), "tree-sitter", [], repo_root=str(tmp_path))
    assert f".claude/skills/{name}/SKILL.md" in plan.order


def test_build_plan_replace_if_present_skipped_without_repo_root(monkeypatch):
    """No repo_root (clone-less init) ⇒ REPLACE_IF_PRESENT skills are skipped."""
    files = {name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS}
    files.update({name: ["SKILL.md"] for name in sync.REPLACE_IF_PRESENT_SKILLS})
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    for name in sync.REPLACE_IF_PRESENT_SKILLS:
        assert f".claude/skills/{name}/SKILL.md" not in plan.order


def test_build_plan_never_distributes_release_only_skills(monkeypatch):
    """Release-only skills are never in either catalog ⇒ never planned, even if
    they exist at the ref."""
    release_only = ["release-fleet-ops", "release-fleet-triage", "gh-repo-setup"]
    files = {name: ["SKILL.md"] for name in sync.PUSH_ALL_SKILLS}
    files.update({name: ["SKILL.md"] for name in release_only})
    monkeypatch.setattr(sync.gh, "git_ls_tree", _skill_tree_ls(files))
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    for name in release_only:
        assert f".claude/skills/{name}/SKILL.md" not in plan.order


def test_build_plan_lefthook_fragment_order(monkeypatch):
    monkeypatch.setattr(sync.gh, "git_ls_tree", lambda *a, **k: "")
    present = {
        "ref:templates/components/_lefthook-base.yaml",
        "ref:templates/commons/lefthook.fragment.yaml",
        "ref:templates/components/cap/lefthook.fragment.yaml",
        "ref:templates/rust-cli/lefthook.fragment.yaml",
    }
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: rp in present)
    plan = sync.build_plan(_git_source(), "rust-cli", ["cap"])
    assert plan.lefthook_frags == [
        "templates/components/_lefthook-base.yaml",
        "templates/commons/lefthook.fragment.yaml",
        "templates/components/cap/lefthook.fragment.yaml",
        "templates/rust-cli/lefthook.fragment.yaml",
    ]


def test_build_plan_skips_skip_sources(monkeypatch):
    listing = (
        "100644 blob a\ttemplates/commons/manifest.yaml\n"
        "100644 blob b\ttemplates/commons/lefthook.fragment.yaml\n"
        "100644 blob c\ttemplates/commons/.DS_Store\n"
        "100644 blob e\ttemplates/commons/lib/rc/__pycache__/cli.cpython-313.pyc\n"
        "100644 blob f\ttemplates/commons/lib/rc/cli.pyc\n"
        "100644 blob d\ttemplates/commons/bin/real\n"
    )
    monkeypatch.setattr(
        sync.gh,
        "git_ls_tree",
        lambda ref, path, *, cwd, **k: listing if path == "templates/commons" else "",
    )
    monkeypatch.setattr(sync.gh, "git_cat_file_exists", lambda rp, *, cwd: False)
    plan = sync.build_plan(_git_source(), "tree-sitter", [])
    assert plan.order == ["bin/real"]  # bytecode + skip-sources dropped


def test_build_tree_writes_managed_gitignore(monkeypatch, tmp_path):
    """build_tree() always writes a self-ignoring .release/.gitignore (`*`) so the
    whole ephemeral build dir is invisible to git — out-of-sync impossible by
    construction (WS4, release#521; supersedes the bytecode-only ignore of #450)."""
    plan = sync.Plan()
    plan.order = ["bin/real"]
    plan.mode = {"bin/real": "100644"}
    plan.source = {"bin/real": "templates/commons/bin/real"}

    monkeypatch.setattr(sync.gh, "git_show_bytes", lambda spec, *, cwd: b"#!/bin/sh\n")
    sync.build_tree(_git_source(ref_sha="deadbeef" * 5), "deadbeef" * 5, plan, str(tmp_path))

    gi = tmp_path / ".gitignore"
    assert gi.is_file()
    body = gi.read_text()
    # `*` on its own line ignores everything in the dir (including .gitignore itself).
    assert any(line.strip() == "*" for line in body.splitlines())


# ── find-style traversal order (the report-ordering contract) ─────────────────


def test_find_files_interleaves_like_find(tmp_path):
    """find recurses into a subdir as soon as it meets it in readdir order; the
    order must match (NOT os.walk's files-first-then-subdirs)."""
    (tmp_path / "a").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x").write_text("")
    (tmp_path / "z").write_text("")
    files = sync._find_files(str(tmp_path))
    # All three present; sub/x appears (the exact interleave depends on readdir,
    # but the function must descend sub and never miss it).
    assert set(files) == {"a", "sub/x", "z"}


# ── broken-symlink detection ──────────────────────────────────────────────────


def test_broken_link_swept_when_target_absent_everywhere(tmp_path):
    binp = tmp_path / "bin"
    binp.mkdir()
    os.symlink("../.release/bin/gone", str(binp / "stale"))
    # bin/gone is not a mirrored dest → swept.
    out = sync._find_broken_release_links(str(tmp_path), set())
    assert out == ["./bin/stale"]


def test_broken_link_kept_when_still_a_mirrored_dest(tmp_path):
    binp = tmp_path / "bin"
    binp.mkdir()
    os.symlink("../.release/bin/check-shell", str(binp / "check-shell"))
    # bin/check-shell is still mirrored this sync → kept.
    out = sync._find_broken_release_links(str(tmp_path), {"bin/check-shell"})
    assert out == []


def test_link_swept_when_target_removed_this_sync(tmp_path):
    """Regression for #476 (the lex dogfood): a symlink whose target STILL EXISTS
    in the live .release/ (so it is NOT broken-live) but is no longer a mirrored
    dest must be swept. The old condition required broken-live AND absent-new, so
    a removed-this-sync target dangled after the .release/ swap."""
    binp = tmp_path / "bin"
    binp.mkdir()
    # Live target present (link resolves fine right now) — the OLD .release/.
    live_release = tmp_path / ".release" / "bin"
    live_release.mkdir(parents=True)
    (live_release / "changelog").write_text("#!/bin/sh\n")
    os.symlink("../.release/bin/changelog", str(binp / "changelog"))
    assert os.path.exists(str(binp / "changelog"))  # NOT broken-live
    # The shim was retired this sync → bin/changelog is not a mirrored dest.
    out = sync._find_broken_release_links(str(tmp_path), set())
    assert out == ["./bin/changelog"]


def test_demirrored_link_swept_even_when_target_present(tmp_path):
    """WS3 (release#524): the root lefthook.yml + lint/format configs became
    release-internal — still materialized into .release/ (target RESOLVES) but no
    longer mirrored out. A filesystem-presence test would leave these stale root
    symlinks behind; the mirrored-dest rule sweeps them."""
    # Seed a pre-WS3 consumer: root lefthook.yml symlink whose .release/ target
    # still exists, and a couple config symlinks.
    live = tmp_path / ".release"
    live.mkdir()
    for name in ("lefthook.yml", ".markdownlint.json", ".yamllint"):
        (live / name).write_text("x\n")
        os.symlink(f".release/{name}", str(tmp_path / name))
    # None of these dests is mirrored anymore (they are release-internal now).
    out = sync._find_broken_release_links(str(tmp_path), set())
    assert sorted(out) == ["./.markdownlint.json", "./.yamllint", "./lefthook.yml"]


def test_broken_link_tampered_escape_is_swept(tmp_path):
    """A tampered target whose post-marker path escapes via `..` is not a clean
    mirrored dest, so it is swept (the old explicit containment guard is subsumed
    by the membership rule)."""
    binp = tmp_path / "bin"
    binp.mkdir()
    os.symlink("../.release/../outside", str(binp / "evil"))  # tgt_rel = ../outside
    out = sync._find_broken_release_links(str(tmp_path), {"bin/check-shell"})
    assert out == ["./bin/evil"]


def test_broken_link_ignores_non_release_targets(tmp_path):
    os.symlink("/nowhere/else", str(tmp_path / "other"))
    assert sync._find_broken_release_links(str(tmp_path), set()) == []


def test_broken_link_prunes_release_and_git(tmp_path):
    # A broken .release-pointing link INSIDE .release/ or .git/ must be ignored.
    rel = tmp_path / ".release" / "bin"
    rel.mkdir(parents=True)
    os.symlink("../.release/bin/gone", str(rel / "inside"))
    assert sync._find_broken_release_links(str(tmp_path), set()) == []


# ── stale managed-copy sweep ──────────────────────────────────────────────────


def test_stale_managed_copy_detected(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "old.yml").write_text(sync.MANAGED_MARKER + "\non: push\n")
    (wf / "hand.yml").write_text("on: push\n")  # no marker → left alone
    out = sync._find_stale_managed_copies(str(tmp_path), set())
    assert out == [".github/workflows/old.yml"]


def test_stale_managed_copy_detects_pre_ws4_release_sync_marker(tmp_path):
    # WS4 (release#521) changed MANAGED_MARKER from the "release-sync" wording to a
    # "release-core init" one. Detection keys off the stable MANAGED_MARKER_SIGNATURE
    # prefix, so a copy a pre-WS4 consumer committed with the OLD literal marker is
    # still recognized as managed (→ swept/rewritten, not orphaned).
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    old_marker = "# Managed by release-sync — do not edit. Regenerate via release-sync."
    assert sync.MANAGED_MARKER_SIGNATURE in old_marker  # the compat invariant
    (wf / "legacy.yml").write_text(old_marker + "\non: push\n")
    out = sync._find_stale_managed_copies(str(tmp_path), set())
    assert out == [".github/workflows/legacy.yml"]


def test_stale_managed_copy_skips_rewritten(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "keep.yml").write_text(sync.MANAGED_MARKER + "\non: push\n")
    # In copy_set → being (re)written this sync → not stale.
    out = sync._find_stale_managed_copies(str(tmp_path), {".github/workflows/keep.yml"})
    assert out == []


def test_stale_managed_copy_rel_uses_forward_slashes(tmp_path):
    # The membership test against copy_set (forward-slash keyed) and the emitted
    # rel must always use '/', never the OS separator — guards the cross-platform
    # path normalization at the relpath call.
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "old.yml").write_text(sync.MANAGED_MARKER + "\non: push\n")
    out = sync._find_stale_managed_copies(str(tmp_path), set())
    assert out == [".github/workflows/old.yml"]
    assert all("\\" not in p for p in out)


# ── distributed-skill dest replacement (the lex pr-review-respond regression) ──


@pytest.mark.parametrize(
    ("dest", "is_skill"),
    [
        (".claude/skills/pr-review-respond/SKILL.md", True),
        (".claude/skills/tdd/mocking.md", True),
        (".claude/settings.json", False),
        ("bin/check", False),
        ("lefthook.yml", False),
    ],
)
def test_is_distributed_skill_dest(dest, is_skill):
    assert sync.is_distributed_skill_dest(dest) is is_skill


def test_compute_mirror_replaces_stale_real_skill_copy(tmp_path):
    """A pre-existing REAL .claude/skills/<name>/SKILL.md (lex's stale hand-copy)
    is migrated→symlinked WITHOUT --migrate — never left as a conflict."""
    dest = ".claude/skills/pr-review-respond/SKILL.md"
    real = tmp_path / dest
    real.parent.mkdir(parents=True)
    real.write_text("# stale local copy (157 lines)\n")  # a real file, not a symlink

    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)

    target = sync.link_target(dest)
    assert dest in mp.migrated
    assert f"{dest} -> {target}" in mp.symlinks_to_create
    assert dest not in mp.conflicts


def test_compute_mirror_sweeps_link_whose_target_removed_this_sync(tmp_path):
    """#476: a consumer symlink into the LIVE .release/ whose target is gone from
    the new tree (a retired shim) lands in symlinks_to_remove — so the .release/
    swap doesn't leave it dangling. The new tree carries some OTHER managed file
    (the new_files list is non-empty), but not the retired one."""
    binp = tmp_path / "bin"
    binp.mkdir()
    # Live target present (resolves now); retired from the new tree this sync.
    live = tmp_path / ".release" / "bin"
    live.mkdir(parents=True)
    (live / "changelog").write_text("#!/bin/sh\n")
    os.symlink("../.release/bin/changelog", str(binp / "changelog"))

    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / "bin").mkdir(parents=True)
    (tmp_release / "bin" / "keep").write_text("#!/bin/sh\n")  # the surviving tool

    mp = sync.compute_mirror(["bin/keep"], str(tmp_path), str(tmp_release), migrate=False)

    assert "./bin/changelog" in mp.symlinks_to_remove


def test_compute_mirror_symlinked_skill_root_is_removed_first(tmp_path):
    """When the consumer's skill ROOT is itself a SYMLINK, compute_mirror schedules
    the root for removal and plans plain creates for files under it — so apply
    never mutates the symlink's target (e.g. inside .release/)."""
    # .claude/skills/lex-primer -> some external dir (the dangerous case).
    external = tmp_path / "external-target"
    (external).mkdir()
    (external / "SKILL.md").write_text("# do not touch this target\n")
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    os.symlink(str(external), str(skills / "lex-primer"))

    dest = ".claude/skills/lex-primer/SKILL.md"
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)

    # The symlinked root is removed first; the file is a plain create.
    assert ".claude/skills/lex-primer" in mp.migrated
    target = sync.link_target(dest)
    assert f"{dest} -> {target}" in mp.symlinks_to_create
    # The per-file dest is NOT separately migrated (that would read through the link).
    assert dest not in mp.migrated


def test_compute_mirror_real_copy_queued_when_dest_absent_or_drifted(tmp_path):
    """A managed real-file copy (.github/workflows/*.yml) is queued when the dest
    is missing or its bytes drift from what _apply would write."""
    dest = ".github/workflows/copilot-review.yml"
    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / ".github" / "workflows").mkdir(parents=True)
    (tmp_release / dest).write_text("on: pull_request\n")

    # absent dest → queued
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert dest in mp.copies_to_write

    # drifted dest (hand-edited) → still queued (drift repaired)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / dest).write_text("hand-edited junk\n")
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert dest in mp.copies_to_write


def test_compute_mirror_real_copy_skipped_when_byte_identical(tmp_path):
    """#476 idempotency: a managed real-file copy whose dest already matches what
    _apply would write (managed-marker header + body for YAML) is NOT re-queued —
    so a steady-state init reports zero changes and skips the auto-commit instead
    of failing it with 'nothing to commit'."""
    dest = ".github/workflows/copilot-review.yml"
    body = "on: pull_request\n"
    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / ".github" / "workflows").mkdir(parents=True)
    (tmp_release / dest).write_text(body)

    # Dest holds exactly what _apply writes for YAML: marker line + body.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / dest).write_text(sync.MANAGED_MARKER + "\n" + body)

    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert dest not in mp.copies_to_write
    assert mp.copies_to_write == []
    # And it must NOT be swept as stale just because it wasn't rewritten — that
    # would flip-flop delete/rewrite the file on alternating syncs.
    assert dest not in mp.copies_to_remove


def test_compute_mirror_sweeps_only_genuinely_retired_managed_copy(tmp_path):
    """A marker-bearing workflow that the sync no longer owns IS swept; a managed
    copy still owned (even byte-identical, not rewritten) is NOT."""
    owned = ".github/workflows/copilot-review.yml"
    retired = ".github/workflows/old-thing.yml"
    body = "on: pull_request\n"
    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / ".github" / "workflows").mkdir(parents=True)
    (tmp_release / owned).write_text(body)

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    # owned dest already matches _apply output → not rewritten this sync
    (tmp_path / owned).write_text(sync.MANAGED_MARKER + "\n" + body)
    # retired managed copy: marker-bearing, no longer in new_files
    (tmp_path / retired).write_text(sync.MANAGED_MARKER + "\nname: gone\n")

    mp = sync.compute_mirror([owned], str(tmp_path), str(tmp_release), migrate=False)
    assert retired in mp.copies_to_remove
    assert owned not in mp.copies_to_remove


def test_skill_root_of():
    assert sync._skill_root_of(".claude/skills/tdd/mocking.md") == ".claude/skills/tdd"
    assert sync._skill_root_of(".claude/skills/tdd/SKILL.md") == ".claude/skills/tdd"
    # a bare root with no file under it, and non-skill dests → None
    assert sync._skill_root_of(".claude/skills/tdd") is None
    assert sync._skill_root_of("bin/check") is None


def test_compute_mirror_non_skill_real_file_still_conflicts(tmp_path):
    """A real file at a NON-skill managed dest keeps the conflict guard (only
    --migrate replaces it) — the skill auto-replace is scoped to skills.
    (.editorconfig stays mirrored — unlike lefthook.yml/configs, which WS3 made
    release-internal.)"""
    dest = ".editorconfig"
    (tmp_path / dest).write_text("root = true\n")
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert dest in mp.conflicts
    assert not mp.symlinks_to_create


# ── CLAUDE.md orientation block ───────────────────────────────────────────────


def test_claude_desired_creates_block_only_when_no_file(tmp_path):
    desired = sync.claude_desired(str(tmp_path))
    # WS2 (#523): the block is the stub pointing at the binary, not an ORIENTATION import.
    assert desired == (f"{sync.CLAUDE_BEGIN}\n{sync.CLAUDE_STUB_BODY}\n{sync.CLAUDE_END}\n")
    assert "release-core how-to" in desired
    assert "@.release/ORIENTATION.md" not in desired


def test_claude_desired_preserves_existing_content(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Proj\n\nmine\n")
    desired = sync.claude_desired(str(tmp_path))
    assert desired.startswith(sync.CLAUDE_BEGIN)
    assert "# Proj" in desired
    assert "mine" in desired
    # Block, blank line, then content.
    assert f"{sync.CLAUDE_END}\n\n# Proj" in desired


def test_claude_desired_strips_prior_block_idempotent(tmp_path):
    p = tmp_path / "CLAUDE.md"
    first = sync.claude_desired(str(tmp_path))
    p.write_text(first)
    # Feeding its own output back yields a byte-identical file.
    assert sync.claude_desired(str(tmp_path)) == first


def test_claude_desired_strips_stale_block_and_refreshes(tmp_path):
    p = tmp_path / "CLAUDE.md"
    # An old consumer's block imported @.release/ORIENTATION.md — it must be
    # stripped and refreshed to the stub, not duplicated.
    old = f"{sync.CLAUDE_BEGIN}\n@.release/ORIENTATION.md\n{sync.CLAUDE_END}\n"
    p.write_text(f"{old}\n# Proj\n\nmine\n")
    desired = sync.claude_desired(str(tmp_path))
    assert "release-core how-to" in desired
    assert "@.release/ORIENTATION.md" not in desired
    assert "# Proj" in desired
    assert desired.count(sync.CLAUDE_BEGIN) == 1


def test_claude_refresh_converges_from_both_historical_forms(tmp_path):
    """release#563: every init must rewrite the managed block to the current
    how-to-pointing stub regardless of WHICH historical form it finds — the
    @.release/ORIENTATION.md import form (padz/phos-app seeds, including the
    blank-line variant) and any older inlined-prose body. (The legacy-marker
    form is covered by test_claude_legacy_marker_is_rewritten_not_duplicated.)"""
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    p = tmp_path / "CLAUDE.md"
    stub = sync.claude_desired(str(tmp_path))  # no CLAUDE.md yet → the bare stub

    historical_blocks = [
        # the import form (padz seed)
        f"{sync.CLAUDE_BEGIN}\n@.release/ORIENTATION.md\n{sync.CLAUDE_END}\n",
        # the import form with a blank line inside the block (phos-app seed)
        f"{sync.CLAUDE_BEGIN}\n\n@.release/ORIENTATION.md\n{sync.CLAUDE_END}\n",
        # an older inlined-prose body
        (
            f"{sync.CLAUDE_BEGIN}\n"
            "Open a live PR (never a draft — stale pre-#456 doctrine).\n"
            f"{sync.CLAUDE_END}\n"
        ),
    ]
    for block in historical_blocks:
        p.write_text(f"{block}\n# Proj\n\nmine\n")
        decision = sync.decide_claude(str(tmp_path), str(tmp_release))
        assert decision.action == "refresh", block
        assert decision.desired is not None
        assert decision.desired.startswith(stub)
        assert "@.release/ORIENTATION.md" not in decision.desired
        assert "never a draft" not in decision.desired
        assert "release-core how-to" in decision.desired
        assert "# Proj" in decision.desired
        assert decision.desired.count(sync.CLAUDE_BEGIN) == 1
        # Convergence is a fixpoint: applying the refresh ends the loop.
        p.write_text(decision.desired)
        assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "none"


def test_claude_legacy_marker_is_rewritten_not_duplicated(tmp_path):
    """De-jargon (#655): the BEGIN marker moved from 'managed by release-sync' to
    'managed by release-core'. An already-seeded consumer whose CLAUDE.md still
    opens with the legacy marker must be RECOGNIZED (so the block is rewritten to
    the current marker), never have a second block injected above it."""
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    p = tmp_path / "CLAUDE.md"
    legacy = f"{sync.CLAUDE_BEGIN_LEGACY}\n@.release/ORIENTATION.md\n{sync.CLAUDE_END}\n"
    p.write_text(f"{legacy}\n# Proj\n\nmine\n")

    decision = sync.decide_claude(str(tmp_path), str(tmp_release))
    assert decision.action == "refresh"  # recognized, not "inject"
    assert decision.desired is not None
    # Rewritten to the new marker; the legacy line is gone; no duplicate block.
    assert decision.desired.count(sync.CLAUDE_BEGIN) == 1
    assert sync.CLAUDE_BEGIN_LEGACY not in decision.desired
    assert "release-core how-to" in decision.desired
    assert "# Proj" in decision.desired
    # Applying the refresh is a fixpoint — the next init is a no-op.
    p.write_text(decision.desired)
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "none"


def test_no_orientation_file_anywhere_in_the_template_source():
    """Regression for release#563: ORIENTATION.md was retired in WS2 (#523) —
    no template/skill source file of that name may exist for the compose plan
    (and therefore the wheel bundle) to pick up again."""
    import pathlib

    import pytest

    repo_root = pathlib.Path(__file__).resolve().parents[5]
    templates = repo_root / "templates"
    skills = repo_root / "skills"
    if not templates.is_dir():
        pytest.skip("not running from the release source checkout")
    offenders = [
        p for root in (templates, skills) if root.is_dir() for p in root.rglob("ORIENTATION.md")
    ]
    assert offenders == []


def test_decide_claude_unconditional_no_orientation_gate(tmp_path):
    # WS2 (#523): the stub block is unconditional — no ORIENTATION.md needs to be
    # composed in the tree for the block to be created (the old gate is gone).
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()  # no ORIENTATION.md
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "create"


def test_decide_claude_create(tmp_path):
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "create"


def test_decide_claude_skip_symlink(tmp_path):
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    (tmp_path / "real.md").write_text("x\n")
    os.symlink("real.md", str(tmp_path / "CLAUDE.md"))
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "skip-symlink"


def test_decide_claude_inject_vs_refresh(tmp_path):
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    claude = tmp_path / "CLAUDE.md"
    # No managed marker → inject.
    claude.write_text("# Proj\n")
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "inject"
    # Has a (stale) managed marker → refresh.
    claude.write_text(f"{sync.CLAUDE_BEGIN}\n@.release/STALE.md\n{sync.CLAUDE_END}\n")
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "refresh"


def test_decide_claude_none_when_already_synced(tmp_path):
    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(sync.claude_desired(str(tmp_path)))
    assert sync.decide_claude(str(tmp_path), str(tmp_release)).action == "none"


# ── file diff ─────────────────────────────────────────────────────────────────


def test_diff_release_added_modified_removed(tmp_path):
    new = tmp_path / "new"
    old = tmp_path / "old"
    for d in (new, old):
        (d / "sub").mkdir(parents=True)
    (new / "added.txt").write_text("a")
    (new / "sub" / "same.txt").write_text("same")
    (new / "sub" / "changed.txt").write_text("NEW")
    (old / "sub" / "same.txt").write_text("same")
    (old / "sub" / "changed.txt").write_text("OLD")
    (old / "removed.txt").write_text("r")

    diff, new_files = sync.diff_release(str(new), str(old))
    assert set(diff.added) == {"added.txt"}
    assert set(diff.modified) == {"sub/changed.txt"}
    assert set(diff.removed) == {"removed.txt"}
    assert "sub/same.txt" not in diff.modified
    assert set(new_files) == {"added.txt", "sub/same.txt", "sub/changed.txt"}


def test_diff_release_no_existing(tmp_path):
    new = tmp_path / "new"
    new.mkdir()
    (new / "x").write_text("1")
    diff, _ = sync.diff_release(str(new), str(tmp_path / "nope"))
    assert diff.added == ["x"]
    assert diff.removed == []


# ── WS5 (release#526): the irreducible bootstrap set is REAL files ────────────


def test_bootstrap_files_are_classified_real_copies():
    """The SessionStart chain must be readable/executable on a FRESH CLONE —
    before the ephemeral .release/ exists — so it must never be a symlink into
    it. Lock the exact set: the hooks config + the boot resolver + the session
    provisioner + the PreToolUse guard."""
    assert (
        frozenset(
            {
                ".claude/settings.json",
                "bin/install-release-core",
                "bin/setup-dev-env.sh",
                "bin/pr-loop-guard",
            }
        )
        == sync.BOOTSTRAP_REAL_FILES
    )
    for dest in sync.BOOTSTRAP_REAL_FILES:
        assert sync.needs_real_file(dest), dest
    # The neighbors stay symlinks (ephemeral-targeted is fine post-boot).
    assert not sync.needs_real_file("bin/check-shell")
    assert not sync.needs_real_file(".claude/skills/tdd/SKILL.md")


def test_compute_mirror_migrates_bootstrap_symlink_to_real_copy(tmp_path):
    """A pre-WS5 consumer carries TRACKED SYMLINKS at the bootstrap paths
    (pointing into .release/). On re-init those dests are planned as real-copy
    writes — the symlink is replaced, never left dangling for a fresh clone."""
    dest = "bin/setup-dev-env.sh"
    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / "bin").mkdir(parents=True)
    (tmp_release / dest).write_text("#!/usr/bin/env bash\necho boot\n")

    (tmp_path / "bin").mkdir()
    os.symlink(os.path.join(".release", dest), tmp_path / dest)  # the old mirror
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert dest in mp.copies_to_write
    assert not any(dest in s for s in mp.symlinks_to_create)


# ── retired-file tombstones (WS6, release#527) ────────────────────────────────


def test_git_blob_sha1_matches_git_hash_object(tmp_path):
    # `echo hello | git hash-object --stdin` — the classic known vector.
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello\n")
    assert sync._git_blob_sha1(str(f)) == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_retired_blob_file_removed_only_on_exact_match(tmp_path, monkeypatch):
    """Blob provenance is byte-exact: the shipped copy is swept, a
    consumer-MODIFIED copy no longer matches and is left alone."""
    (tmp_path / "bin").mkdir()
    shipped = tmp_path / "bin" / "check-fmt"
    shipped.write_text("#!/usr/bin/env bash\necho fmt\n")
    monkeypatch.setitem(
        sync.RETIRED_BLOB_FILES,
        "bin/check-fmt",
        frozenset({sync._git_blob_sha1(str(shipped))}),
    )
    assert sync._find_retired_files(str(tmp_path)) == ["bin/check-fmt"]

    shipped.write_text("#!/usr/bin/env bash\necho fmt # consumer tweak\n")
    assert sync._find_retired_files(str(tmp_path)) == []


def test_retired_fingerprint_file_swept_and_plain_kept(tmp_path):
    """bin/release shims were per-repo tailored (no stable blob); the verbatim
    header line is the provenance. A consumer's own bin/release stays."""
    (tmp_path / "bin").mkdir()
    shim = tmp_path / "bin" / "release"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "# Thin shim around the canonical release-cut CLI (arthur-debert/release).\n"
    )
    assert "bin/release" in sync._find_retired_files(str(tmp_path))

    shim.write_text("#!/usr/bin/env bash\nmy-own-release-flow\n")
    assert "bin/release" not in sync._find_retired_files(str(tmp_path))


def test_retired_symlink_is_skipped(tmp_path):
    """Symlinks at tombstoned dests belong to the broken-symlink sweep, never
    the tombstone path (which would unlink based on the TARGET's content)."""
    (tmp_path / "bin").mkdir()
    os.symlink("../.release/bin/release", tmp_path / "bin" / "release")
    (tmp_path / ".release" / "bin").mkdir(parents=True)
    (tmp_path / ".release" / "bin" / "release").write_text(
        "# Thin shim around the canonical release-cut CLI\n"
    )
    assert "bin/release" not in sync._find_retired_files(str(tmp_path))


def test_compute_mirror_planned_dest_never_tombstoned(tmp_path, monkeypatch):
    """A dest this sync still distributes is LIVE — if a future kind re-ships a
    retired name, the plan wins and the tombstone is suppressed."""
    dest = "bin/check-fmt"
    tmp_release = tmp_path / "tmpbuild"
    (tmp_release / "bin").mkdir(parents=True)
    (tmp_release / dest).write_text("#!/usr/bin/env bash\necho fmt\n")

    (tmp_path / "bin").mkdir()
    consumer = tmp_path / dest
    consumer.write_text("#!/usr/bin/env bash\necho fmt\n")
    monkeypatch.setitem(
        sync.RETIRED_BLOB_FILES, dest, frozenset({sync._git_blob_sha1(str(consumer))})
    )
    mp = sync.compute_mirror([dest], str(tmp_path), str(tmp_release), migrate=False)
    assert mp.retired_to_remove == []

    # Absent from the plan, the same file IS tombstoned.
    mp = sync.compute_mirror([], str(tmp_path), str(tmp_release), migrate=False)
    assert mp.retired_to_remove == [dest]


def test_retired_smoke_hook_shipped_bytes_swept_modified_kept(tmp_path):
    """release#590: the REAL shipped smoke-hook bytes (pinned as fixtures —
    the templates themselves are deleted) match the live catalog entry and are
    swept; a consumer-OVERRIDDEN hook (the documented customization path) no
    longer matches a shipped blob and is left alone. No monkeypatch: this
    exercises the actual RETIRED_BLOB_FILES entry."""
    fixtures = os.path.join(os.path.dirname(__file__), "retired_fixtures")
    catalog = sync.RETIRED_BLOB_FILES["app-bin/smoke-hook.sh"]
    (tmp_path / "app-bin").mkdir()
    hook = tmp_path / "app-bin" / "smoke-hook.sh"
    for kind in ("electron-app", "tauri-app"):
        with open(os.path.join(fixtures, f"smoke-hook.{kind}"), "rb") as fh:
            shipped = fh.read()
        hook.write_bytes(shipped)
        assert sync._git_blob_sha1(str(hook)) in catalog, kind
        assert "app-bin/smoke-hook.sh" in sync._find_retired_files(str(tmp_path)), kind

        hook.write_bytes(shipped + b"# consumer dynamic smoke\n")
        assert "app-bin/smoke-hook.sh" not in sync._find_retired_files(str(tmp_path)), kind


def test_retired_tables_inventory_locked():
    """The fleet-audit inventory (release#527, completed by #563): paths +
    variant counts, re-derived from template git history. A new retirement
    extends the tables deliberately; this guards accidental edits."""
    expected_counts = {
        # pre-unified-gate entry points — one blob per kind-template variant
        "bin/check-fmt": 6,
        "bin/check-lint": 7,
        # earliest go/rust scripts/ layout (blob-only: common consumer names)
        "scripts/check": 2,
        "scripts/check-fmt": 2,
        "scripts/check-lint": 3,
        "scripts/check-tests": 2,
        # pre-console-script changelog shims (#476) — full blob histories
        "bin/changelog": 6,
        "bin/changelog-add": 6,
        "bin/changelog-cut": 10,
        "bin/changelog-render": 11,
        # #476 shims (bin/semver is BLOB-ONLY: collides with the kept
        # console-script — it must never gain a marker/fingerprint entry)
        "bin/semver": 2,
        "bin/gh-task-status": 3,
        "bin/gh-release-issue": 6,
        # vendored semver-tool (#414), both historical dests
        "vendor/semver-tool/semver": 1,
        "vendor/semver-tool/LICENSE": 1,
        "vendor/semver-tool/README.md": 1,
        "bin/share/semver-tool/semver": 1,
        "bin/share/semver-tool/LICENSE": 1,
        "bin/share/semver-tool/README.md": 1,
        # pre-path-mirror SessionStart script (also fingerprinted — tailored)
        "scripts/setup-dev-env.sh": 21,
        # root lefthook.yml REAL-FILE seeds ONLY (highest collision risk)
        "lefthook.yml": 2,
        # ORIENTATION.md (WS2 #523 / #563) — full template-history blob set
        ".release/ORIENTATION.md": 12,
        # packaged-binary smoke hooks (#590) — one blob per kind at the final
        # dest, plus the pre-#270 scripts/ dest (blob-only: common name)
        "app-bin/smoke-hook.sh": 2,
        "scripts/smoke.sh": 3,
    }
    assert {k: len(v) for k, v in sync.RETIRED_BLOB_FILES.items()} == expected_counts

    # The de-distributed infra-skill set is NOT in this table (#655): a dropped
    # skill reaches a consumer as an untracked symlink (WS7), which the
    # broken-symlink sweep removes — the explicit per-file blob entries were
    # redundant and were deleted. No .claude/skills/ entry survives here.
    assert not any(k.startswith(".claude/skills/") for k in sync.RETIRED_BLOB_FILES)

    for dest, blobs in sync.RETIRED_BLOB_FILES.items():
        assert blobs, dest
        for sha in blobs:
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (dest, sha)
    assert set(sync.RETIRED_FINGERPRINT_FILES) == {"bin/release", "scripts/setup-dev-env.sh"}


def test_retired_catalog_pins_the_live_fleet_misses():
    """The exact blobs the 2026-06 #563 audit found still TRACKED in consumers
    today must be in the catalog — these are the misses that motivated the
    completion (supage's go check-fmt/-lint; padz/phos-app's vendored
    semver-tool; the ORIENTATION.md every pre-WS4 seed still tracks)."""
    assert "2419933941d5607732a488669188d77269a7f49b" in sync.RETIRED_BLOB_FILES["bin/check-fmt"]
    assert "e09ccdbbefd8db5dee80127af0a52e534a4b8229" in sync.RETIRED_BLOB_FILES["bin/check-lint"]
    for dest in ("vendor/semver-tool/semver", "bin/share/semver-tool/semver"):
        assert "a16042505af81862afa6d028b72b355c1572d144" in sync.RETIRED_BLOB_FILES[dest]
    assert (
        "eb0cd6aef6322f24367f1d4f2475b88229ec7f89"
        in sync.RETIRED_BLOB_FILES[".release/ORIENTATION.md"]
    )


def test_retired_orientation_inside_release_dir_is_swept(tmp_path, monkeypatch):
    """A pre-WS4 seed's tracked .release/ORIENTATION.md is tombstoned: the
    dotted-dir dest resolves and lands in retired_to_remove so the managed
    commit pathspec records the deletion explicitly."""
    rel = tmp_path / ".release"
    rel.mkdir()
    orientation = rel / "ORIENTATION.md"
    orientation.write_text("# Orientation\n\nstale doctrine\n")
    monkeypatch.setitem(
        sync.RETIRED_BLOB_FILES,
        ".release/ORIENTATION.md",
        frozenset({sync._git_blob_sha1(str(orientation))}),
    )
    assert ".release/ORIENTATION.md" in sync._find_retired_files(str(tmp_path))

    tmp_release = tmp_path / "tmpbuild"
    tmp_release.mkdir()
    mp = sync.compute_mirror([], str(tmp_path), str(tmp_release), migrate=False)
    assert ".release/ORIENTATION.md" in mp.retired_to_remove


def test_retired_dest_matching_blob_and_fingerprint_listed_once(tmp_path, monkeypatch):
    """scripts/setup-dev-env.sh carries BOTH a blob set and a fingerprint; a
    verbatim copy matches both and must still be swept exactly once."""
    (tmp_path / "scripts").mkdir()
    f = tmp_path / "scripts" / "setup-dev-env.sh"
    f.write_text(
        "#!/usr/bin/env bash\n"
        "# scripts/setup-dev-env.sh — per-session dev-environment setup, invoked by\n"
        "# the SessionStart hook in .claude/settings.json.\n"
    )
    monkeypatch.setitem(
        sync.RETIRED_BLOB_FILES,
        "scripts/setup-dev-env.sh",
        frozenset({sync._git_blob_sha1(str(f))}),
    )
    found = sync._find_retired_files(str(tmp_path))
    assert found.count("scripts/setup-dev-env.sh") == 1


def test_retired_tailored_setup_dev_env_swept_by_fingerprint(tmp_path):
    """A repo-TAILORED scripts/setup-dev-env.sh (extras appended, so its blob
    is not in template history — the supage case) is still swept via the
    verbatim header fingerprint; a consumer-authored script of the same name
    without the header is left alone."""
    (tmp_path / "scripts").mkdir()
    f = tmp_path / "scripts" / "setup-dev-env.sh"
    f.write_text(
        "#!/usr/bin/env bash\n"
        "# scripts/setup-dev-env.sh — per-session dev-environment setup, invoked by\n"
        "# the SessionStart hook in .claude/settings.json.\n"
        "echo repo-specific extras below the marker\n"
    )
    assert "scripts/setup-dev-env.sh" in sync._find_retired_files(str(tmp_path))

    f.write_text("#!/usr/bin/env bash\nmy own session setup\n")
    assert "scripts/setup-dev-env.sh" not in sync._find_retired_files(str(tmp_path))


def test_retired_fingerprint_requires_header_comment_line(tmp_path):
    """The fingerprint is header-anchored: a consumer-owned bin/release that
    merely MENTIONS the phrase mid-body is not ours and must survive."""
    (tmp_path / "bin").mkdir()
    shim = tmp_path / "bin" / "release"
    body = "#!/usr/bin/env bash\n" + "echo step\n" * 12
    shim.write_text(body + "# replaced the Thin shim around the canonical release-cut CLI\n")
    assert "bin/release" not in sync._find_retired_files(str(tmp_path))

    shim.write_text(
        "#!/usr/bin/env bash\n# Thin shim around the canonical release-cut CLI (x/y).\n"
    )
    assert "bin/release" in sync._find_retired_files(str(tmp_path))
