"""init verb (verbs/init.py): installs the managed files into a consumer repo.

A bare `release-core init` runs the FULL install +
auto-commit-on-change — the ONLY mode since release#532 removed the
`--config-only` escape hatch (post-WS3 it wrote root configs whose gate
referenced a `.release/` it never created). The sync engine that
installs the source content is exercised by test_core_sync.py; here we pin
init's OWN contract: the full install + mirror apply, auto-commit scoping
(only managed paths, never a user's work), the push guards, idempotency,
--dry-run/--no-commit, the tolerated legacy --commit/--force flags, and the
resolution-failure surfaces (Kind / source / YAML) → clean exit 1.
"""

from __future__ import annotations

import os

from release_core import manifest, sync, yamlio
from release_core.verbs import init


def test_init_resolves_relative_release_home_before_chdir(tmp_path, monkeypatch):
    # A relative RELEASE_HOME must be resolved against the ORIGINAL cwd, not the
    # repo root init chdir's into. (Gemini review on #424.)
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_run_full_sync", lambda *a, **k: (0, [], "test-ref", []))
    monkeypatch.setenv("RELEASE_HOME", "rel/clone")

    assert init.main([]) == 0
    assert os.environ["RELEASE_HOME"] == str(tmp_path / "rel" / "clone")


# --------------------------------------------------------------------------
# resolution-failure surfaces (Kind / ref) → exit 1, clean message
# --------------------------------------------------------------------------


def test_init_kind_error_exits_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_kind(root, name):
        raise manifest.KindError("nope")

    monkeypatch.setattr(init, "_resolve_full_source", raise_kind)
    rc = init.main(["--no-commit"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "could not detect kind" in err


def test_init_sync_error_exits_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_sync(root, name):
        raise sync.SyncError("release-core init: $RELEASE_HOME=... is not a git clone")

    monkeypatch.setattr(init, "_resolve_full_source", raise_sync)
    rc = init.main(["--no-commit"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "is not a git clone" in err


def test_init_not_in_git_repo_exits_1(monkeypatch, capsys):
    def boom():
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(init.gh, "repo_root", boom)
    rc = init.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not inside a git repo" in err


def test_init_yaml_error_exits_1_not_traceback(tmp_path, monkeypatch, capsys):
    # A yamlio.YamlError out of the sync engine (missing yq, malformed manifest,
    # or a lefthook-fragment merge failure) must be caught at the CLI boundary →
    # clean exit 1, never a traceback escaping.
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_yaml(root, name):
        raise yamlio.YamlError("yq -o=json . failed (1): bad YAML")

    monkeypatch.setattr(init, "_resolve_full_source", raise_yaml)
    rc = init.main(["--no-commit"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "bad YAML" in err


# --------------------------------------------------------------------------
# --help
# --------------------------------------------------------------------------


def test_init_help_exits_0_and_prints_usage(capsys):
    rc = init.main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage:" in out
    assert "release-core init" in out
    # Post-#532: a single mode — help documents the full install, the
    # real flags, and the tolerated legacy --commit/--force no-ops.
    assert "--no-commit" in out
    assert "--push" in out
    assert "TOLERATED" in out


def test_init_unknown_flag_is_usage_error(capsys):
    rc = init.main(["--nope"])
    assert rc == 64


def test_removed_modes_are_unknown_flags(capsys):
    # release#532: --config-only (the escape hatch) and --full (its redundant
    # alias) were REMOVED, not deprecated — both are plain unknown flags now.
    for flag in ("--config-only", "--full"):
        rc = init.main([flag])
        err = capsys.readouterr().err
        assert rc == 64, flag
        assert "unknown" in err.lower() or "usage" in err.lower(), flag


# yq (mikefarah v4) is required for the lefthook fragment merge — the same hard
# dep release-sync has. Skip the merge-dependent tests cleanly if it's absent so
# a yq-less dev box doesn't see spurious failures (CI pins yq, so coverage holds).
import shutil as _shutil  # noqa: E402

import pytest  # noqa: E402

_HAVE_YQ = _shutil.which("yq") is not None
_needs_yq = pytest.mark.skipif(not _HAVE_YQ, reason="yq (mikefarah v4) not installed")


# --------------------------------------------------------------------------
# --commit / --push (the pull-model commit-hygiene seam)
#
# These drive init.main() against a REAL throwaway git repo (git is already a
# release dependency and present in CI) so the staging/commit scoping is
# exercised for real, not mocked. The source content is still the fixture tree
# via _patch, so no yq/templates are needed.
# --------------------------------------------------------------------------

import subprocess as _subprocess  # noqa: E402

_HAVE_GIT = _shutil.which("git") is not None
_needs_git = pytest.mark.skipif(not _HAVE_GIT, reason="git not installed")


def _git(repo, *args):
    return _subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_git_repo(repo, *, default_branch="main"):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", default_branch)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    # An initial commit so HEAD exists (commit --only needs a real HEAD).
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _patch_full(monkeypatch, repo, files):
    """Wire init to the fixture repo with a stubbed full install that "writes" the
    given managed files into the repo and reports them as changed — so these
    tests exercise the REAL _auto_commit staging/commit/push scoping against a
    real git repo, with the heavy install stubbed out."""
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def fake_sync(root, name, *, dry_run):
        if not dry_run:
            for f in files:
                fp = repo / f
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(f"# managed {f}\n")
        return (len(files), list(files), "test-ref", [])

    monkeypatch.setattr(init, "_run_full_sync", fake_sync)


_MANAGED = ["bin/check", "CLAUDE.md", ".claude/skills/tdd/SKILL.md"]


def _tracked_status(repo):
    return _git(repo, "status", "--porcelain")


@_needs_git
def test_full_mode_tolerates_commit_flag_from_stale_resolver(tmp_path, monkeypatch, capsys):
    """Bootstrap-forward: the deployed SessionStart resolver in a not-yet-migrated
    consumer calls `release-core init --commit`. The default (full) init must
    TOLERATE that (warn + proceed, exit 0), not fail with a usage error — else the
    first cutover pull stalls the whole fleet (the resolver can't install the managed
    files that would update the resolver). Caught by the dodot carrier run."""
    repo = _init_git_repo(tmp_path / "repo")
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    # Stub the heavy sync pipeline to a no-op so we exercise only arg handling.
    monkeypatch.setattr(init, "_run_full_sync", lambda *a, **k: (0, [], "test-ref", []))

    rc = init.main(["--commit"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "redundant" in err and "ignoring" in err

    # --force is tolerated the same way (some resolver variants pass it).
    assert init.main(["--force"]) == 0


@_needs_git
def test_commit_message_includes_source_ref_when_known(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    assert rc == 0
    capsys.readouterr()
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject == "chore(release): sync managed tree from test-ref"


@_needs_git
def test_commit_does_not_stage_unrelated_working_tree_changes(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    _patch_full(monkeypatch, repo, _MANAGED)

    # A user's in-progress work: one modified tracked file + one new untracked
    # file, neither managed. Must survive the managed commit untouched.
    (repo / "README.md").write_text("# repo — WIP edit\n")
    (repo / "feature.txt").write_text("user work\n")

    rc = init.main([])
    assert rc == 0
    capsys.readouterr()
    # The managed commit must NOT include the user's files.
    committed = set(_git(repo, "log", "-1", "--name-only", "--pretty=format:").splitlines())
    committed.discard("")
    assert committed == set(_MANAGED)
    # The user's changes are still present and uncommitted.
    status = _tracked_status(repo)
    # README is still modified and feature.txt still untracked — both left for the
    # user. The critical guarantee (README not in the managed commit) is asserted
    # above; here we confirm the user's work survived intact and uncommitted.
    assert "README.md" in status
    assert "?? feature.txt" in status
    # And README is NOT in the index as staged-for-commit (its worktree X column
    # is unmodified): no managed commit should have staged it.
    diff_cached = _git(repo, "diff", "--cached", "--name-only")
    assert "README.md" not in diff_cached.splitlines()
    assert (repo / "README.md").read_text() == "# repo — WIP edit\n"


@_needs_git
def test_commit_does_not_fold_in_pre_staged_unrelated_changes(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    _patch_full(monkeypatch, repo, _MANAGED)

    # A user already STAGED an unrelated change. The pathspec-scoped commit must
    # not absorb it.
    (repo / "staged.txt").write_text("already staged\n")
    _git(repo, "add", "staged.txt")

    rc = init.main([])
    assert rc == 0
    capsys.readouterr()
    committed = set(_git(repo, "log", "-1", "--name-only", "--pretty=format:").splitlines())
    committed.discard("")
    assert "staged.txt" not in committed
    assert committed == set(_MANAGED)
    # staged.txt is still staged, still uncommitted.
    assert "A  staged.txt" in _tracked_status(repo)


def test_commit_in_non_git_dir_is_safe_no_op(tmp_path, monkeypatch, capsys):
    # repo_root is monkeypatched to a plain dir (no .git). init must still
    # install the managed files and succeed; --commit is a quiet no-op.
    repo = tmp_path / "repo"
    repo.mkdir()
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    for dest in _MANAGED:
        assert (repo / dest).is_file()
    assert "committed" not in out


@_needs_git
def test_commit_on_unborn_branch_is_safe_no_op(tmp_path, monkeypatch, capsys):
    # A freshly `git init`'d repo with NO commits yet (unborn HEAD). A
    # pathspec-scoped commit cannot run there ("partial commit during
    # bootstrap"); init must skip silently and still succeed — consistently
    # across layouts (Gemini review on #443).
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    # No initial commit → HEAD is unborn.
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    captured = capsys.readouterr()
    assert rc == 0
    # Files were still installed.
    for dest in _MANAGED:
        assert (repo / dest).is_file()
    # No commit, and no noisy "cannot do partial commit" failure surfaced.
    assert "committed" not in captured.out
    assert "bootstrap" not in captured.err
    assert "--commit skipped" not in captured.err


@_needs_git
def test_push_only_on_default_branch(tmp_path, monkeypatch, capsys):
    # On a feature branch, --push must keep the commit local (no push attempt).
    repo = _init_git_repo(tmp_path / "repo", default_branch="main")
    # Give it an origin so git_default_branch resolves to 'main'.
    bare = tmp_path / "origin.git"
    _subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")
    # Now switch to a feature branch.
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _patch_full(monkeypatch, repo, _MANAGED)

    pushes = []
    monkeypatch.setattr(init.gh, "git_push_ff", lambda branch, **k: pushes.append(branch))

    rc = init.main(["--push"])
    err = capsys.readouterr().err
    assert rc == 0
    assert pushes == [], "must NOT push from a feature branch"
    assert "push skipped" in err
    # But the commit was still made locally on the feature branch.
    assert "chore(release)" in _git(repo, "log", "-1", "--pretty=format:%s")


@_needs_git
def test_push_skipped_when_tree_dirty_on_default_branch(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo", default_branch="main")
    bare = tmp_path / "origin.git"
    _subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")
    _patch_full(monkeypatch, repo, _MANAGED)

    # A non-managed dirty file on the default branch → push must be skipped.
    (repo / "dirty.txt").write_text("uncommitted\n")

    pushes = []
    monkeypatch.setattr(init.gh, "git_push_ff", lambda branch, **k: pushes.append(branch))

    rc = init.main(["--push"])
    err = capsys.readouterr().err
    assert rc == 0
    assert pushes == [], "must NOT push with an otherwise-dirty tree"
    assert "push skipped" in err


@_needs_git
def test_push_happens_on_clean_default_branch(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo", default_branch="main")
    bare = tmp_path / "origin.git"
    _subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main(["--push"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "pushed to main." in out
    # The bare origin now has the managed commit. Read the explicit `main` ref,
    # not the bare's default HEAD: `git init --bare` points HEAD at
    # init.defaultBranch (often `master` on CI), so `git log` with no ref hits an
    # empty branch and exits 128 even though the push to `main` succeeded.
    assert "chore(release)" in _git(bare, "log", "-1", "--pretty=format:%s", "main")


# --------------------------------------------------------------------------
# the branch-from-origin hint (release#566)
#
# When the auto-commit lands on the checked-out DEFAULT branch and stays local,
# an agent that branches from local <default> carries the alien sync commit
# into its feature PR diff. init must say so loudly — and ONLY when a commit
# actually happened on the default branch and was not pushed.
# --------------------------------------------------------------------------

_HINT = "managed sync committed on"


def _repo_with_origin(tmp_path, *, default_branch="main"):
    """A real repo whose `origin` is a bare clone with origin/HEAD set, so
    gh.git_default_branch resolves (the same dance the push tests do)."""
    repo = _init_git_repo(tmp_path / "repo", default_branch=default_branch)
    bare = tmp_path / "origin.git"
    _subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", default_branch)
    _git(repo, "remote", "set-head", "origin", default_branch)
    return repo


@_needs_git
def test_branch_hint_fires_when_auto_commit_lands_on_default_branch(tmp_path, monkeypatch, capsys):
    repo = _repo_with_origin(tmp_path)
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out  # the auto-commit happened…
    assert f"{_HINT} 'main'" in out  # …and the hint names the branch
    assert "branch from origin/main" in out  # …with the remedy


@_needs_git
def test_branch_hint_absent_on_noop_init(tmp_path, monkeypatch, capsys):
    # No managed change → no auto-commit → no hint (the common steady-state
    # SessionStart must stay quiet).
    repo = _repo_with_origin(tmp_path)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_run_full_sync", lambda *a, **k: (0, [], "test-ref", []))

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already current" in out
    assert _HINT not in out


@_needs_git
def test_branch_hint_absent_on_feature_branch(tmp_path, monkeypatch, capsys):
    # The commit landing on a feature branch is the rider's own problem space —
    # the hint is specifically about polluting the DEFAULT branch.
    repo = _repo_with_origin(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    assert _HINT not in out


@_needs_git
def test_branch_hint_absent_when_commit_was_pushed(tmp_path, monkeypatch, capsys):
    # --push succeeded on the default branch: the commit is on origin, so
    # branching from local <default> is fine — no hint.
    repo = _repo_with_origin(tmp_path)
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main(["--push"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "pushed to main." in out
    assert _HINT not in out


@_needs_git
def test_branch_hint_absent_without_an_origin(tmp_path, monkeypatch, capsys):
    # No origin remote → no origin/<default> to branch from (and no PR to
    # pollute) — the hint must not fire with an unresolvable remedy.
    repo = _init_git_repo(tmp_path / "repo")
    _patch_full(monkeypatch, repo, _MANAGED)

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    assert _HINT not in out


@_needs_git
def test_commit_failure_does_not_fail_init(tmp_path, monkeypatch, capsys):
    # If the commit itself errors, init must still exit 0 (commit is best-effort).
    repo = _init_git_repo(tmp_path / "repo")
    _patch_full(monkeypatch, repo, _MANAGED)

    def boom(*a, **k):
        raise init.gh.proc.ProcError(["git", "commit"], 1, "nope")

    monkeypatch.setattr(init.gh, "git_commit_paths", boom)

    rc = init.main([])
    err = capsys.readouterr().err
    assert rc == 0
    assert "--commit skipped" in err


# --------------------------------------------------------------------------
# the full install + auto-commit-on-change (the ONLY mode)
# --------------------------------------------------------------------------
# A synthetic full source (a tiny commons/ + a manifest-less kind + one
# distributed skill) is installed once, then used BOTH as a wheel bundle (the
# BundleSource path) AND as a git release clone (the GitSource path). The two
# installed trees must be byte-identical modulo the provenance-marker line —
# this is the load-bearing proof that BundleSource == GitSource output.


def _full_source_tree(root) -> str:
    """Build a synthetic release-source tree under ``root``: templates/commons
    (a lint config + a real bin tool + a lefthook fragment), a manifest-less kind
    (tree-sitter) with its own fragment, and one PUSH_ALL skill (gh-pr-review-loop).
    Returns the abs path to ``root`` (the layout root, mirroring the repo:
    <root>/templates/… and <root>/skills/…). No ORIENTATION.md — RETIRED in WS2
    (#523); the CLAUDE.md block is the stub pointing at `release-core how-to`."""
    tpl = root / "templates"
    (tpl / "commons" / "bin").mkdir(parents=True)
    (tpl / "components").mkdir(parents=True)
    (tpl / "tree-sitter").mkdir(parents=True)
    (root / "skills" / "gh-pr-review-loop").mkdir(parents=True)

    (tpl / "commons" / ".editorconfig").write_text("root = true\n")
    tool = tpl / "commons" / "bin" / "check"
    tool.write_text("#!/bin/sh\necho check\n")
    os.chmod(tool, 0o755)
    (tpl / "commons" / "lefthook.fragment.yaml").write_text(
        "pre-commit:\n  commands:\n    md:\n      run: echo md\n"
    )
    (tpl / "components" / "_lefthook-base.yaml").write_text(
        "pre-commit:\n  parallel: true\n  commands: {}\n"
    )
    (tpl / "tree-sitter" / "lefthook.fragment.yaml").write_text(
        "pre-commit:\n  commands:\n    ts:\n      run: echo ts\n"
    )
    (root / "skills" / "gh-pr-review-loop" / "SKILL.md").write_text("# skill\n")
    return str(root)


def _git_clone_from(src_root, dest) -> str:
    """Make ``dest`` a git work tree whose committed tree IS ``src_root`` (so a
    GitSource at HEAD reads exactly the synthetic content). Returns dest path."""
    _shutil.copytree(src_root, dest)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.email", "t@example.com")
    _git(dest, "config", "user.name", "Test")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "source")
    return str(dest)


def _build_via_bundle(tmp_path, src_root, kind):
    out = tmp_path / "out-bundle"
    out.mkdir()
    source = sync.BundleSource(str(src_root), ref_sha="release-core test")
    plan = sync.install_plan(source, kind, [])
    sync.install_tree(source, source.ref_sha, plan, str(out))
    return out


def _build_via_git(tmp_path, clone, kind):
    out = tmp_path / "out-git"
    out.mkdir()
    source = sync.GitSource(str(clone), "HEAD", "abc123sha")
    plan = sync.install_plan(source, kind, [])
    sync.install_tree(source, source.ref_sha, plan, str(out))
    return out


def _tree_files(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                out[rel] = fh.read()
    return out


@_needs_yq
@_needs_git
def test_full_bundle_build_matches_git_sync(tmp_path):
    # THE core proof: the BundleSource (init --full) tree is byte-identical to the
    # GitSource (release-sync) tree for the same Kind, modulo only the provenance
    # marker line (.release-sync-source) and the lefthook header sha line.
    src = _full_source_tree(tmp_path / "src")
    clone = _git_clone_from(src, tmp_path / "clone")

    b = _build_via_bundle(tmp_path, src, "tree-sitter")
    g = _build_via_git(tmp_path, clone, "tree-sitter")

    bf = _tree_files(b)
    gf = _tree_files(g)
    assert set(bf) == set(gf), "the two built trees must list the same files"

    for rel in sorted(gf):
        if rel == ".release-sync-source":
            continue  # provenance differs by design (wheel ver vs git sha)
        if rel == "lefthook.yml":
            # Only the header carries the sha; the merged body must be identical.
            assert bf[rel].split(b"\n\n", 1)[1] == gf[rel].split(b"\n\n", 1)[1]
            continue
        assert bf[rel] == gf[rel], f"{rel} must be byte-identical across sources"

    # Sanity: the real bin tool kept its exec bit through both paths.
    assert os.access(os.path.join(b, "bin", "check"), os.X_OK)
    assert os.access(os.path.join(g, "bin", "check"), os.X_OK)


def _setup_full_repo(tmp_path, monkeypatch, src_root):
    """A throwaway git consumer wired so init takes the BundleSource path
    (no $RELEASE_HOME) against ``src_root`` as the bundle, kind tree-sitter."""
    repo = _init_git_repo(tmp_path / "consumer")
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.delenv("RELEASE_REF", raising=False)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_bundle_root", lambda: str(src_root))
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "tree-sitter")
    return repo


@_needs_yq
@_needs_git
def test_bare_init_does_full_install_and_auto_commits(tmp_path, monkeypatch, capsys):
    # THE cutover contract: a BARE `release-core init` (exactly what SessionStart
    # runs, no flags) installs ALL managed files from the bundle AND
    # auto-commits the managed change. This is the #476 default.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # An unrelated dirty file must NOT be folded into the managed commit.
    (repo / "my-feature.txt").write_text("wip\n")

    rc = init.main([])  # no flags — the SessionStart invocation
    out = capsys.readouterr().out
    assert rc == 0
    assert "managed-file change(s) applied" in out
    # `.release/` temp dir (on disk) + working-tree mirrors + CLAUDE.md header block.
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / "bin" / "check").is_symlink()
    # WS4 (#761): CLAUDE.md is a one-line @import of the managed target file.
    claude = (repo / "CLAUDE.md").read_text()
    assert claude.strip() == sync.CLAUDE_IMPORT_LINE
    target = (repo / sync.CLAUDE_IMPORT_TARGET).read_text()
    assert "release-core how-to" in target
    assert "@.release/ORIENTATION.md" not in target
    # Auto-committed (the default) — deterministic message.
    assert "committed" in out
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject.startswith("chore(release): sync managed tree from")
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # WS4 (release#521): the ephemeral `.release/` temp dir is gitignored and never
    # committed. WS7 (release#528): the symlink mirrors are EPHEMERAL too —
    # installed + excluded, never tracked — so only real-file copies and the
    # managed CLAUDE.md @import TARGET commit.
    assert "bin/check" not in committed
    assert _git(repo, "ls-files", "bin/check") == ""
    assert _git(repo, "status", "--porcelain", "bin/check") == ""  # excluded
    assert not any(p.startswith(".release/") for p in committed)
    # WS4 (#761): the managed @import target is committed. CLAUDE.md did not exist
    # → init CREATED it as the pure one-line pointer, which is safe to commit (no
    # consumer content to fold in), so the fresh-seed tree stays clean.
    assert sync.CLAUDE_IMPORT_TARGET in committed
    assert "CLAUDE.md" in committed
    assert (repo / "CLAUDE.md").read_text().strip() == sync.CLAUDE_IMPORT_LINE
    assert "my-feature.txt" not in committed


@_needs_yq
@_needs_git
def test_bare_init_idempotent_no_commit_second_run(tmp_path, monkeypatch, capsys):
    # A bare second init with no upstream change → zero changes → no commit (the
    # no-op-when-current property the pull model relies on so churn = cadence).
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main([]) == 0
    capsys.readouterr()
    head1 = _git(repo, "rev-parse", "HEAD")
    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "already current" in out
    assert _git(repo, "rev-parse", "HEAD") == head1


@_needs_yq
@_needs_git
def test_bare_init_commits_removals(tmp_path, monkeypatch, capsys):
    # The removed-target sweep works through the DEFAULT (bare) path: a managed
    # symlink whose .release target is gone in a later install is removed — no
    # dangling link left behind (#476 / #481). Post-WS7 (release#528) the mirror
    # was never TRACKED, so the removal is a pure filesystem op: no commit needed,
    # and the tree stays clean.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main([]) == 0
    capsys.readouterr()
    assert (repo / "bin" / "check").is_symlink()
    head1 = _git(repo, "rev-parse", "HEAD")

    os.remove(os.path.join(src, "templates", "commons", "bin", "check"))
    rc = init.main([])
    capsys.readouterr()
    assert rc == 0
    assert not os.path.lexists(repo / "bin" / "check"), "left a dangling symlink"
    assert not (repo / ".release" / "bin" / "check").exists()
    assert _git(repo, "status", "--porcelain") == ""
    # The mirror was ephemeral (untracked): its removal produces no commit.
    assert _git(repo, "rev-parse", "HEAD") == head1


@_needs_yq
@_needs_git
def test_full_installs_tree_and_symlinks(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    rc = init.main(["--no-commit"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "managed-file change(s) applied" in out
    # `.release/` temp dir populated.
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / ".release" / ".claude" / "skills" / "gh-pr-review-loop" / "SKILL.md").is_file()
    # Working-tree symlinks mirrored.
    assert (repo / "bin" / "check").is_symlink()
    assert (repo / ".claude" / "skills" / "gh-pr-review-loop" / "SKILL.md").is_symlink()
    # CLAUDE.md @import created (WS4, #761): a one-line pointer; the managed
    # target file carries the orientation, points at the binary, no ORIENTATION.
    claude = (repo / "CLAUDE.md").read_text()
    assert claude.strip() == sync.CLAUDE_IMPORT_LINE
    target = (repo / sync.CLAUDE_IMPORT_TARGET).read_text()
    assert "release-core how-to" in target
    assert "@.release/ORIENTATION.md" not in target
    assert not (repo / ".release" / "ORIENTATION.md").exists()
    assert not (repo / "ORIENTATION.md").exists()


@_needs_yq
@_needs_git
def test_full_auto_commits_only_managed_paths_when_changed(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # An unrelated dirty file must NOT be folded into the managed commit.
    (repo / "my-feature.txt").write_text("wip\n")

    rc = init.main([])  # auto-commit is the default
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject.startswith("chore(release): sync managed tree from")
    # NO [skip ci]: it would make a managed-files-only migration PR un-mergeable
    # under a required-status-checks ruleset (CI skipped → required checks never
    # satisfied).
    assert "[skip ci]" not in subject
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # Managed real-file paths committed; the unrelated file is NOT. WS4
    # (release#521): the ephemeral `.release/` temp dir is gitignored and never
    # committed. WS7 (release#528): the symlink mirrors are ephemeral too.
    assert not any(p.startswith(".release/") for p in committed)
    assert "bin/check" not in committed
    assert _git(repo, "ls-files", "bin/check") == ""
    # WS4 (#761): the managed @import target is committed; CLAUDE.md was created
    # fresh as the pure pointer (safe to commit), the unrelated file is NOT.
    assert sync.CLAUDE_IMPORT_TARGET in committed
    assert "CLAUDE.md" in committed
    assert "my-feature.txt" not in committed
    assert _git(repo, "status", "--porcelain", "my-feature.txt") == "?? my-feature.txt"


@_needs_yq
@_needs_git
def test_full_force_adds_managed_paths_under_a_consumer_gitignore(tmp_path, monkeypatch, capsys):
    """A consumer .gitignore that covers a managed REAL-FILE path (e.g. `.claude/`
    shadowing the managed `.claude/settings.json`, here modeled with `bin/` over
    the bootstrap copy) must NOT silently drop it from the migration commit —
    managed real files are release-owned and force-added. Regression: 6 fleet
    consumers gitignored `.claude/`, so their migration staged but never committed
    (`git add` errors on an ignored path). Post-WS7 the SYMLINK mirrors are
    ephemeral (never tracked), so the force-add applies only to real-file managed
    copies — the bootstrap quartet and workflow copies."""
    src = _full_source_tree(tmp_path / "src")
    # Give the synthetic source a bootstrap real-file dest (quartet member).
    boot = os.path.join(src, "templates", "commons", "bin", "install-release-core")
    with open(boot, "w") as fh:
        fh.write("#!/usr/bin/env bash\necho install-release-core\n")
    os.chmod(boot, 0o755)
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # Consumer ignores a directory the managed files are written into.
    (repo / ".gitignore").write_text("/bin/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore bin")

    rc = init.main([])
    assert rc == 0
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # The managed real-file copy is committed despite the .gitignore covering bin/;
    # the ephemeral symlink mirror is not tracked at all.
    assert "bin/install-release-core" in committed
    assert "bin/check" not in committed
    assert _git(repo, "ls-files", "bin/check") == ""
    assert _git(repo, "status", "--porcelain") == ""  # clean tree, nothing stranded


@_needs_yq
@_needs_git
def test_full_is_idempotent_no_commit_second_run(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    assert init.main([]) == 0
    capsys.readouterr()
    head1 = _git(repo, "rev-parse", "HEAD")

    # Second run: byte-identical tree → zero changes → no commit.
    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "already current" in out
    head2 = _git(repo, "rev-parse", "HEAD")
    assert head1 == head2, "a no-change second run must not create a commit"


@_needs_yq
@_needs_git
def test_full_no_commit_skips_commit(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--no-commit"])
    assert rc == 0
    capsys.readouterr()
    # Files installed but no commit made; managed changes left in the worktree.
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain") != ""


@_needs_yq
@_needs_git
def test_full_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run, no writes" in out
    assert not (repo / ".release").exists()
    assert not (repo / "bin" / "check").exists()
    assert _git(repo, "rev-parse", "HEAD") == head_before


@_needs_yq
@_needs_git
def test_full_errors_when_source_lacks_kind_tree(tmp_path, monkeypatch, capsys):
    # A source missing templates/<kind>/ must hard-fail (exit 1) rather than
    # silently build an incomplete tree (commons/skills only).
    src = _full_source_tree(tmp_path / "src")
    repo = _init_git_repo(tmp_path / "consumer")
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_bundle_root", lambda: str(src))
    # detect a Kind the bundle has no templates/<kind>/ dir for.
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "no-such-kind")

    rc = init.main(["--no-commit"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no" in err and "templates/no-such-kind/ tree" in err
    assert not (repo / ".release").exists()


@_needs_yq
@_needs_git
def test_full_commits_removals(tmp_path, monkeypatch, capsys):
    # A "removals-only" managed update (a symlink whose .release target is gone in
    # a later install) must not be left dangling. Post-WS7 (release#528) the mirror
    # is normally never tracked, so the sweep is a pure filesystem op — but a
    # TRACKED swept symlink must still commit its deletion via the managed
    # pathspec (the swept link rides `written`).
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main([]) == 0
    capsys.readouterr()
    assert (repo / "bin" / "check").is_symlink()
    # A tracked mirror symlink (force-added).
    _git(repo, "add", "-f", "bin/check")
    _git(repo, "commit", "-q", "-m", "tracked mirror")

    # Drop the bin/check tool from the source, re-run: the managed symlink +
    # its .release/ target must be removed AND the tracked deletion committed.
    os.remove(os.path.join(src, "templates", "commons", "bin", "check"))
    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    # #476: the working-tree symlink itself must be GONE — not merely a dangling
    # link (os.path.exists follows the link, so it returns False for a broken
    # link too; lexists/islink is what actually catches the dangle the bug left).
    assert not (repo / "bin" / "check").exists()
    assert not os.path.lexists(repo / "bin" / "check"), "left a dangling symlink"
    assert not (repo / "bin" / "check").is_symlink()
    assert not (repo / ".release" / "bin" / "check").exists()
    # The deletion is in the commit and the tree is clean (nothing left staged).
    last = _git(repo, "show", "--name-status", "--pretty=format:", "HEAD")
    assert "bin/check" in last
    assert _git(repo, "status", "--porcelain") == ""


# --------------------------------------------------------------------------
# Flag-combo guards (full install is the ONLY mode since release#532).
# --------------------------------------------------------------------------


def test_push_and_no_commit_is_bad_usage(capsys):
    # --push implies a commit; --no-commit suppresses it — contradictory. In the
    # default (full) mode, with no other flag.
    rc = init.main(["--push", "--no-commit"])
    err = capsys.readouterr().err
    assert rc == 64
    assert "mutually exclusive" in err


@_needs_yq
@_needs_git
def test_no_commit_in_default_full_mode_is_valid(tmp_path, monkeypatch, capsys):
    # The flip: a BARE `--no-commit` (default full mode) is now VALID — it skips
    # the auto-commit (was a usage error pre-#476). Run it for real and confirm
    # rc 0, the files installed, and NO commit was made.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--no-commit"])
    assert rc == 0
    capsys.readouterr()
    assert (repo / ".release" / "bin" / "check").is_file()  # files installed
    assert _git(repo, "rev-parse", "HEAD") == head_before  # but no commit


# NOTE: --commit/--force in default full mode are now TOLERATED (warn + proceed),
# not rejected — the deployed stale resolver passes --commit on the first cutover
# pull. See test_full_mode_tolerates_commit_flag_from_stale_resolver above.


@_needs_yq
@_needs_git
def test_full_surfaces_conflicts(tmp_path, monkeypatch, capsys):
    # A real file at a managed symlink location blocks the link (a conflict). It
    # is reported on stderr and the run is NOT called "already current".
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # Pre-place a real bin/check (non-skill dest → conflict, not auto-migrated).
    (repo / "bin").mkdir()
    (repo / "bin" / "check").write_text("hand-written\n")

    rc = init.main(["--no-commit"])
    out = capsys.readouterr()
    assert rc == 0
    assert "conflict" in out.err
    assert "bin/check" in out.err
    # bin/check stays the real file (not replaced by a symlink).
    assert not (repo / "bin" / "check").is_symlink()


def test_full_no_bundle_and_no_clone_errors(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "consumer"
    repo.mkdir()
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_bundle_root", lambda: None)
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "tree-sitter")

    rc = init.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no bundled templates" in err


@_needs_yq
@_needs_git
def test_full_uses_release_home_clone_when_present(tmp_path, monkeypatch, capsys):
    # An explicit $RELEASE_HOME git clone OVERRIDES the bundle (release-dev path).
    src = _full_source_tree(tmp_path / "src")
    clone = _git_clone_from(src, tmp_path / "clone")
    repo = _init_git_repo(tmp_path / "consumer")
    monkeypatch.setenv("RELEASE_HOME", str(clone))
    monkeypatch.setenv("RELEASE_REF", "HEAD")
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "tree-sitter")
    # The bundle path must NOT run when a clone is present.
    monkeypatch.setattr(
        init, "_bundle_root", lambda: (_ for _ in ()).throw(AssertionError("bundle must not run"))
    )

    rc = init.main(["--no-commit"])
    assert rc == 0
    capsys.readouterr()
    assert (repo / ".release" / "bin" / "check").is_file()
    # Provenance marker carries the git sha (not the wheel version).
    marker = (repo / ".release" / ".release-sync-source").read_text()
    assert "release-core" not in marker


# ── The managed auto-commit + marker carry the source provenance ──────────────
# The source's ref_sha IS the provenance: the tag-stamped wheel version for a
# BundleSource ("release-core <version>", release#758) or the resolved git SHA for
# a GitSource. (WS8 #765 removed the separate release-source.tag stamp the boot
# resolver used to write + read_source_tag, so the RELEASE_CORE_SOURCE_TAG channel
# is gone — the wheel version is the only provenance label now.)


@_needs_yq
@_needs_git
def test_bundle_init_labels_commit_and_marker_with_wheel_version(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    assert init.main([]) == 0
    capsys.readouterr()
    # The commit subject carries the wheel-version provenance and keeps the exact
    # "chore(release): sync managed tree" prefix other tooling greps on.
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject.startswith("chore(release): sync managed tree from release-core ")
    # The marker carries the ref_sha (wheel-version) line.
    lines = (repo / ".release" / ".release-sync-source").read_text().splitlines()
    assert any(ln.startswith("release-core ") for ln in lines), "ref_sha line must remain"


def test_source_label_is_the_ref_sha():
    # WS8 #765: _source_label is just the source's ref_sha (the tag-stamped wheel
    # version for a bundle, the resolved SHA for a clone) — no separate tag channel.
    src = sync.BundleSource("/x", ref_sha="release-core 3.1.0")
    assert init._source_label(src) == "release-core 3.1.0"
    gsrc = sync.GitSource("/x", "HEAD", ref_sha="abc1234")
    assert init._source_label(gsrc) == "abc1234"


@_needs_yq
@_needs_git
def test_git_source_labels_with_the_resolved_sha(tmp_path, monkeypatch, capsys):
    # The $RELEASE_HOME override installs from a live clone: its resolved SHA is
    # the provenance for the commit subject + marker.
    src = _full_source_tree(tmp_path / "src")
    clone = _git_clone_from(src, tmp_path / "clone")
    repo = _init_git_repo(tmp_path / "consumer")
    monkeypatch.setenv("RELEASE_HOME", str(clone))
    monkeypatch.setenv("RELEASE_REF", "HEAD")
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "tree-sitter")

    assert init.main([]) == 0
    capsys.readouterr()
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    sha = _git(clone, "rev-parse", "HEAD")
    assert subject == f"chore(release): sync managed tree from {sha}"


# ── WS5 (release#526): the bootstrap quartet is written as REAL files ────────


def _ws5_source_tree(root) -> str:
    """_full_source_tree + the bootstrap files, so a full install exercises the
    real-copy path for the SessionStart chain. (WS8 #765: setup-dev-env.sh removed
    — the set is .claude/settings.json + install-release-core + pr-loop-guard +
    delegate-guard.)"""
    src = _full_source_tree(root)
    tpl = root / "templates" / "commons"
    (tpl / ".claude").mkdir(parents=True)
    (tpl / ".claude" / "settings.json").write_text('{"hooks": {}}\n')
    for name in ("install-release-core", "pr-loop-guard", "delegate-guard"):
        f = tpl / "bin" / name
        f.write_text(f"#!/usr/bin/env bash\necho {name}\n")
        os.chmod(f, 0o755)
    return src


@_needs_yq
@_needs_git
def test_full_init_writes_bootstrap_quartet_as_real_executable_files(tmp_path, monkeypatch, capsys):
    src = _ws5_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    assert init.main([]) == 0
    capsys.readouterr()
    for dest in (
        ".claude/settings.json",
        "bin/install-release-core",
        "bin/pr-loop-guard",
        "bin/delegate-guard",
    ):
        p = repo / dest
        assert p.is_file(), dest
        assert not p.is_symlink(), f"{dest} must be a REAL file (fresh-clone boot), not a symlink"
        assert dest in _git(repo, "ls-files"), f"{dest} must be tracked"
    # The executables carry their bit; the JSON does not.
    for dest in ("bin/install-release-core", "bin/pr-loop-guard", "bin/delegate-guard"):
        assert os.access(repo / dest, os.X_OK), dest
    # No managed-marker header on a shebang script (would break the shebang) or
    # on JSON (no comment syntax).
    first = (repo / "bin" / "install-release-core").read_text().splitlines()[0]
    assert first.startswith("#!"), "shebang must stay line 1"
    assert (repo / ".claude" / "settings.json").read_text().lstrip().startswith("{")


@_needs_yq
@_needs_git
def test_full_init_migrates_bootstrap_symlinks_and_replaces_atomically(
    tmp_path, monkeypatch, capsys
):
    """A pre-WS5 consumer has tracked SYMLINKS at the bootstrap paths; init must
    replace them with real files. The replace is by RENAME (new inode), never a
    truncate-in-place — install-release-core rewrites ITSELF while running, and
    an in-place truncation would yank the running script out from under it."""
    src = _ws5_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    # Seed the pre-WS5 state: a dangling symlink into the not-yet-built .release/.
    (repo / "bin").mkdir(exist_ok=True)
    os.symlink(
        os.path.join("..", ".release", "bin", "install-release-core"),
        repo / "bin" / "install-release-core",
    )
    assert init.main([]) == 0
    capsys.readouterr()
    p = repo / "bin" / "install-release-core"
    assert p.is_file() and not p.is_symlink()
    ino_before = os.stat(p).st_ino

    # Drift the file, re-init: repaired via a NEW inode (rename, not truncate).
    p.write_text("#!/usr/bin/env bash\nhand-edited\n")
    assert init.main([]) == 0
    capsys.readouterr()
    assert "echo install-release-core" in p.read_text()
    assert os.stat(p).st_ino != ino_before, "repair must be an atomic rename, not in-place"


@_needs_yq
@_needs_git
def test_full_removes_retired_files_in_managed_commit(tmp_path, monkeypatch, capsys):
    """WS6 (release#527): a pre-pull consumer carries retired release-distributed
    real files (e.g. the release-cut shim). A bare init removes them —
    provenance-gated — and the deletions ride the managed auto-commit;
    consumer-owned files are untouched."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    (repo / "bin").mkdir(exist_ok=True)
    (repo / "bin" / "release").write_text(
        "#!/usr/bin/env bash\n"
        "# Thin shim around the canonical release-cut CLI (arthur-debert/release).\n"
    )
    (repo / "bin" / "deploy").write_text("#!/usr/bin/env bash\nmy own tool\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pre-pull seed")

    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "committed" in out

    assert not (repo / "bin" / "release").exists()
    assert (repo / "bin" / "deploy").exists()

    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    assert "bin/release" in committed
    assert "bin/deploy" not in committed
    # The removals are real deletions in the index, not stray edits.
    assert _git(repo, "status", "--porcelain", "bin/release") == ""


@_needs_yq
@_needs_git
def test_full_converges_pre_pull_seed_orientation_and_stub(tmp_path, monkeypatch, capsys):
    """release#563 + WS4/WS8 (#761/#765): a pre-pull seed TRACKS
    .release/ORIENTATION.md (stale, rule-contradicting) and carries a consumer
    CLAUDE.md with no managed @import. One bare init converges: the install removes
    the on-disk orientation copy and never re-builds it, the retired-file removal +
    WS4 untracking record the deletion, the managed @import TARGET is written +
    committed, and the one-line @import is prepended to CLAUDE.md AND committed (the
    one-time insertion MUST land — otherwise the @import sits uncommitted and the
    next init re-stages it). After it, CLAUDE.md is consumer-owned and never
    re-staged, so a 2nd init is a clean no-op."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    (repo / ".release").mkdir()
    orientation = repo / ".release" / "ORIENTATION.md"
    orientation.write_text("# Orientation\n\nOpen a live PR (never a draft).\n")
    monkeypatch.setitem(
        init.sync.RETIRED_BLOB_FILES,
        ".release/ORIENTATION.md",
        frozenset({init.sync._git_blob_sha1(str(orientation))}),
    )
    (repo / "CLAUDE.md").write_text("# Consumer\n\nmine\n")
    _git(repo, "add", "-f", ".release/ORIENTATION.md", "CLAUDE.md")
    _git(repo, "commit", "-q", "-m", "pre-pull seed: tracked .release + consumer CLAUDE.md")

    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "committed" in out
    # Gone from disk, NOT rebuilt by the install.
    assert not orientation.exists()
    # Untracked — the deletion is recorded, not resurrected by the pathspec commit.
    assert _git(repo, "ls-files", ".release/ORIENTATION.md") == ""
    # CLAUDE.md gained the one-line @import; consumer prose survives below it.
    claude = (repo / "CLAUDE.md").read_text()
    assert claude.startswith(sync.CLAUDE_IMPORT_LINE)
    assert "# Consumer" in claude
    # The one-time insertion committed BOTH the managed @import TARGET and CLAUDE.md.
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    assert sync.CLAUDE_IMPORT_TARGET in committed
    assert "CLAUDE.md" in committed
    head_claude = _git(repo, "show", "HEAD:CLAUDE.md")
    assert head_claude.startswith(sync.CLAUDE_IMPORT_LINE)
    assert "# Consumer" in head_claude  # consumer prose preserved in the commit
    # Tree is CLEAN — CLAUDE.md is committed, not left dangling; the orientation
    # removal is fully recorded too.
    assert _git(repo, "status", "--porcelain", "CLAUDE.md") == ""
    assert _git(repo, "status", "--porcelain", ".release/ORIENTATION.md") == ""

    # Idempotent: nothing left to converge.
    head = _git(repo, "rev-parse", "HEAD")
    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "already current" in out
    assert _git(repo, "rev-parse", "HEAD") == head
    assert not orientation.exists()


@_needs_yq
@_needs_git
def test_full_sweeps_retired_vendor_dir_and_prunes_husk(tmp_path, monkeypatch, capsys):
    """release#563: the vendored semver-tool (retired #414) is swept per-file
    under blob provenance and its emptied directory husk is pruned — but a
    consumer-owned file inside the dir keeps the dir (and itself) alive."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    vendor = repo / "vendor" / "semver-tool"
    vendor.mkdir(parents=True)
    contents = {
        "vendor/semver-tool/semver": "#!/usr/bin/env bash\nsemver tool\n",
        "vendor/semver-tool/LICENSE": "Apache-2.0\n",
        "vendor/semver-tool/README.md": "# semver-tool\n",
    }
    for rel, body in contents.items():
        (repo / rel).write_text(body)
        monkeypatch.setitem(
            init.sync.RETIRED_BLOB_FILES,
            rel,
            frozenset({init.sync._git_blob_sha1(str(repo / rel))}),
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pre-pull seed: vendored semver-tool")

    assert init.main([]) == 0
    out = capsys.readouterr().out
    assert "committed" in out
    assert not (repo / "vendor").exists(), "left a vendor/ husk behind"
    last = _git(repo, "show", "--name-status", "--pretty=format:", "HEAD")
    assert "D\tvendor/semver-tool/semver" in last
    assert _git(repo, "status", "--porcelain") == ""

    # Re-seed WITH a consumer file alongside: the files sweep, the dir stays.
    vendor.mkdir(parents=True)
    for rel, body in contents.items():
        (repo / rel).write_text(body)
    (vendor / "NOTES.md").write_text("consumer-owned\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "re-seed with consumer file")
    capsys.readouterr()
    assert init.main([]) == 0
    capsys.readouterr()
    assert not (vendor / "semver").exists()
    assert (vendor / "NOTES.md").exists(), "consumer file must survive the prune"


@_needs_yq
@_needs_git
def test_full_sweeps_retired_smoke_hook_shipped_bytes(tmp_path, monkeypatch, capsys):
    """release#590: a seeded consumer still TRACKING the shipped
    app-bin/smoke-hook.sh has it swept (and the deletion committed) by a bare
    init. REAL template bytes, NO catalog monkeypatch — this drives the live
    RETIRED_BLOB_FILES entry. A consumer-OVERRIDDEN hook (the documented
    customization path) no longer matches a shipped blob and is left alone."""
    fixture = os.path.join(os.path.dirname(__file__), "retired_fixtures", "smoke-hook.electron-app")
    with open(fixture, "rb") as fh:
        shipped = fh.read()
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    (repo / "app-bin").mkdir()
    (repo / "app-bin" / "smoke-hook.sh").write_bytes(shipped)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed: shipped smoke hook")

    assert init.main([]) == 0
    assert "committed" in capsys.readouterr().out
    assert not (repo / "app-bin").exists(), "left an app-bin/ husk behind"
    last = _git(repo, "show", "--name-status", "--pretty=format:", "HEAD")
    assert "D\tapp-bin/smoke-hook.sh" in last
    assert _git(repo, "status", "--porcelain") == ""

    # Consumer-edited bytes: not ours to delete — survives further inits.
    (repo / "app-bin").mkdir()
    (repo / "app-bin" / "smoke-hook.sh").write_bytes(shipped + b"# dynamic packaged e2e\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "consumer-owned smoke hook")
    capsys.readouterr()
    assert init.main([]) == 0
    capsys.readouterr()
    assert (repo / "app-bin" / "smoke-hook.sh").exists(), "consumer-modified hook must survive"


@_needs_yq
@_needs_git
def test_full_writes_mirror_excludes_idempotently(tmp_path, monkeypatch, capsys):
    """The ephemeral mirrors are listed in .git/info/exclude (NOT the consumer's
    .gitignore — zero tracked footprint), in a managed block rewritten wholesale
    by every init; consumer-authored exclude lines survive."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    ex = repo / ".git" / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    ex.write_text("# consumer line\nmy-scratch/\n")

    assert init.main([]) == 0
    capsys.readouterr()
    text = ex.read_text()
    assert "my-scratch/" in text  # consumer content survives
    assert "/bin/check" in text
    assert "/.editorconfig" in text
    assert text.count(init._EXCLUDE_BEGIN) == 1

    assert init.main([]) == 0
    capsys.readouterr()
    assert ex.read_text().count(init._EXCLUDE_BEGIN) == 1  # rewritten, not appended


@_needs_yq
@_needs_git
def test_full_retired_tracked_skill_swept_not_resurrected(tmp_path, monkeypatch, capsys):
    """A seed tracks a symlink for a skill release has since RETIRED
    (release-issue-relay class). The sweep removes it from disk during apply; it
    must not be resurrected as a dangling link; its empty dir is pruned; the
    deletion commits; the tree ends clean."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main([]) == 0
    capsys.readouterr()

    # Seed a tracked symlink for a skill the source does NOT distribute.
    retired = repo / ".claude" / "skills" / "release-issue-relay"
    retired.mkdir(parents=True)
    os.symlink(
        "../../../.release/.claude/skills/release-issue-relay/SKILL.md",
        retired / "SKILL.md",
    )
    _git(repo, "add", "-f", ".claude/skills/release-issue-relay/SKILL.md")
    _git(repo, "commit", "-q", "-m", "pre-WS7 seed: tracked retired skill")

    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    assert not os.path.lexists(retired / "SKILL.md"), "resurrected a retired skill link"
    assert not retired.exists(), "left an empty skill-dir husk"
    last = _git(repo, "show", "--name-status", "--pretty=format:", "HEAD")
    assert "D\t.claude/skills/release-issue-relay/SKILL.md" in last
    assert _git(repo, "status", "--porcelain") == ""


@_needs_yq
@_needs_git
def test_full_conflict_dest_not_excluded(tmp_path, monkeypatch, capsys):
    """A conflicted mirror dest (real file blocks the managed path — no symlink
    applied) must NOT enter the .git/info/exclude block: excluding it would hide
    the untracked conflicting file from `git status`, masking the conflict the
    user is told to resolve."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    (repo / "bin").mkdir()
    (repo / "bin" / "check").write_text("#!/bin/sh\nconsumer's own check\n")

    rc = init.main([])
    err = capsys.readouterr().err
    assert rc == 0
    assert "bin/check" in err  # surfaced as a conflict
    text = (repo / ".git" / "info" / "exclude").read_text()
    assert "/bin/check\n" not in text  # conflicted dest NOT excluded
    assert "/.editorconfig" in text  # applied mirrors still are
    # The conflicting file stays visible to git status (untracked; -uall so
    # git doesn't collapse it into "?? bin/").
    assert "?? bin/check" in _git(repo, "status", "--porcelain", "-uall")


# --------------------------------------------------------------------------
# the seed-time workflow-reference warning (#581: the consumer-side tripwire)
# --------------------------------------------------------------------------

_BAD_WF = """\
name: e2e
on: [push]
jobs:
  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Run E2E tests
        run: bin/check-e2e
"""

_MATERIALIZED_WF = """\
name: e2e
on: [push]
jobs:
  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Arm the gate (materialize-only)
        uses: arthur-debert/release/.github/actions/arm-gate@v2
        with:
          toolset: 'false'
      - name: Run E2E tests
        run: bin/check-e2e
"""


def _write_consumer_wf(repo, text, name="e2e.yml"):
    wf_dir = repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / name).write_text(text)


@_needs_yq
def test_warn_names_file_job_step_and_the_next_action(tmp_path, capsys):
    """The supage#163 shape: a consumer-authored job invokes a managed bin tool
    without installing the `.release/` temp dir first → LOUD stderr warning with
    exact coordinates + remedy."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _write_consumer_wf(repo, _BAD_WF)
    init._warn_unbuilt_workflow_refs(str(repo), {"bin/check", "bin/check-e2e"})
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert ".github/workflows/e2e.yml -> integration -> Run E2E tests" in err
    assert "bin/check-e2e" in err
    # The one next action: install the `.release/` temp dir via arm-gate, toolset:'false'.
    assert "arm-gate" in err
    assert "toolset: 'false'" in err
    assert "release-core how-to" in err


@_needs_yq
def test_warn_silent_when_job_installs_first(tmp_path, capsys):
    repo = tmp_path / "consumer"
    repo.mkdir()
    _write_consumer_wf(repo, _MATERIALIZED_WF)
    init._warn_unbuilt_workflow_refs(str(repo), {"bin/check", "bin/check-e2e"})
    assert capsys.readouterr().err == ""


def test_warn_silent_when_repo_has_no_workflows(tmp_path, capsys):
    repo = tmp_path / "consumer"
    repo.mkdir()
    init._warn_unbuilt_workflow_refs(str(repo), {"bin/check"})
    assert capsys.readouterr().err == ""


def test_warn_never_raises_on_scan_failure(tmp_path, monkeypatch, capsys):
    """A WARNING, never a failure: any scanner error (missing yq, odd tree) is
    swallowed — init's boot is never the casualty."""

    def boom(*a, **k):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(init.contract, "lint_workflow_dir", boom)
    repo = tmp_path / "consumer"
    repo.mkdir()
    _write_consumer_wf(repo, _BAD_WF)
    init._warn_unbuilt_workflow_refs(str(repo), {"bin/check"})
    assert capsys.readouterr().err == ""


@_needs_yq
@_needs_git
def test_full_init_fires_the_workflow_warning(tmp_path, monkeypatch, capsys):
    """End-to-end: a bare `release-core init` over a consumer carrying a bad job
    warns on EVERY init (the tripwire is not a one-time migration message) —
    and still exits 0 with the managed files installed."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # References bin/check — a mirror dest of the synthetic source tree.
    _write_consumer_wf(
        repo,
        _BAD_WF.replace("run: bin/check-e2e", "run: bin/check"),
    )

    assert init.main([]) == 0
    err = capsys.readouterr().err
    assert ".github/workflows/e2e.yml -> integration -> Run E2E tests" in err
    assert "toolset: 'false'" in err
    assert (repo / "bin" / "check").is_symlink()  # init still applied the managed files

    # Steady-state re-run: managed files already current, the warning still fires.
    assert init.main([]) == 0
    err = capsys.readouterr().err
    assert ".github/workflows/e2e.yml -> integration -> Run E2E tests" in err
