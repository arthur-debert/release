"""init verb (verbs/init.py): the managed-tree materializer.

After the #476 cutover, a BARE `release-core init` runs the FULL managed-tree
materialize + auto-commit-on-change (covered by the `init --full / default`
section at the bottom). The CONFIG-SUBSET behavior (the old default, create-if-
absent) is now the `--config-only` escape hatch — every config-subset test below
drives `init.main(["--config-only", ...])`.

The release-sync engine that composes the source content is exercised by
test_core_sync.py / test_core_release_sync_verb.py; here we monkeypatch
init._materialize_config_sources to hand back a temp tree of fixture config
files, then pin init's OWN config-only contract: create-if-absent, idempotency,
--force overwrite, --dry-run, and the hard non-zero exit when a write fails. The
source resolution + Kind/ref failure surfaces are covered by their own tests
below.
"""

from __future__ import annotations

import os
import stat

from release_core import manifest, sync, yamlio
from release_core.verbs import init


def _fixture_sources(tmp_path) -> dict[str, str]:
    """A temp tree with one fixture file per CONFIG_FILES dest; return the
    {dest -> abs path} map init._materialize_config_sources would return."""
    src_root = tmp_path / "src"
    src_root.mkdir()
    sources: dict[str, str] = {}
    for dest in init.CONFIG_FILES:
        p = src_root / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# managed source for {dest}\n")
        sources[dest] = str(p)
    return sources


def _patch(monkeypatch, repo, sources):
    """Wire init's repo-root + source resolution to the fixture repo/sources."""
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(
        init, "_materialize_config_sources", lambda root, name, *, source_ref=None: sources
    )


# --------------------------------------------------------------------------
# create-if-absent
# --------------------------------------------------------------------------


def test_init_creates_all_config_files_when_absent(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only"])
    out = capsys.readouterr().out
    assert rc == 0
    for dest in init.CONFIG_FILES:
        target = repo / dest
        assert target.is_file(), f"{dest} should have been created"
        assert target.read_text() == f"# managed source for {dest}\n"
        assert f"create  {dest}" in out
    assert "7 created, 0 overwritten, 0 unchanged" in out
    assert "done." in out


def test_init_leaves_existing_files_untouched(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    # A pre-existing consumer edit must survive (create-if-absent, no --force).
    (repo / "lefthook.yml").write_text("# CONSUMER EDIT — keep me\n")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (repo / "lefthook.yml").read_text() == "# CONSUMER EDIT — keep me\n"
    assert "skip    lefthook.yml" in out
    # The other six were absent → created.
    assert "6 created, 0 overwritten, 1 unchanged" in out


# --------------------------------------------------------------------------
# idempotency — second run is a clean no-op
# --------------------------------------------------------------------------


def test_init_is_idempotent_second_run_is_clean_no_op(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    assert init.main(["--config-only"]) == 0
    capsys.readouterr()
    # Snapshot the tree after the first run.
    before = {
        dest: (repo / dest).read_text() for dest in init.CONFIG_FILES if (repo / dest).is_file()
    }

    rc = init.main(["--config-only"])
    out = capsys.readouterr().out
    assert rc == 0
    after = {
        dest: (repo / dest).read_text() for dest in init.CONFIG_FILES if (repo / dest).is_file()
    }
    assert after == before, "second run must not change any file"
    assert "0 created, 0 overwritten, 7 unchanged" in out
    assert "no changes — already initialized" in out


# --------------------------------------------------------------------------
# --force overwrite
# --------------------------------------------------------------------------


def test_init_force_overwrites_existing(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lefthook.yml").write_text("# stale\n")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--force"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (repo / "lefthook.yml").read_text() == "# managed source for lefthook.yml\n"
    assert "force   lefthook.yml (overwritten)" in out
    # 6 absent → created, 1 present → overwritten.
    assert "6 created, 1 overwritten, 0 unchanged" in out


def test_init_force_preserves_existing_file_mode(tmp_path, monkeypatch, capsys):
    # The atomic overwrite goes through mkstemp (0600) + os.replace; it must NOT
    # silently tighten the managed file's permissions. (Gemini review on #424.)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "lefthook.yml"
    target.write_text("# stale\n")
    os.chmod(target, 0o644)
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    assert init.main(["--config-only", "--force"]) == 0
    capsys.readouterr()
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o644, f"force overwrite changed mode to {oct(mode)} (expected 0o644)"


def test_init_repairs_a_broken_symlink(tmp_path, monkeypatch, capsys):
    # A dangling .release/-style symlink at a config path reports as present via
    # lexists(); init must repair it (materialize the real file over it) even
    # WITHOUT --force, not silently skip and leave the repo uninitialized.
    # (Gemini review on #424.)
    repo = tmp_path / "repo"
    repo.mkdir()
    link = repo / "lefthook.yml"
    os.symlink(repo / ".release" / "build" / "lefthook.yml", link)  # target missing
    assert os.path.islink(link) and not os.path.exists(link)
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only"])
    out = capsys.readouterr().out
    assert rc == 0
    assert not os.path.islink(link), "broken symlink should be replaced by a real file"
    assert link.read_text() == "# managed source for lefthook.yml\n"
    assert "repair  lefthook.yml (was a broken symlink)" in out
    assert "1 repaired" in out


def test_init_resolves_relative_release_home_before_chdir(tmp_path, monkeypatch):
    # A relative RELEASE_HOME must be resolved against the ORIGINAL cwd, not the
    # repo root init chdir's into. (Gemini review on #424.)
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)
    monkeypatch.setenv("RELEASE_HOME", "rel/clone")

    assert init.main(["--config-only"]) == 0
    assert os.environ["RELEASE_HOME"] == str(tmp_path / "rel" / "clone")


# --------------------------------------------------------------------------
# --dry-run writes nothing
# --------------------------------------------------------------------------


def test_init_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    for dest in init.CONFIG_FILES:
        assert not (repo / dest).exists(), f"{dest} must NOT be written in dry-run"
        assert f"would create  {dest}" in out
    assert "dry-run, no writes" in out


# --------------------------------------------------------------------------
# hard non-zero exit when a write fails
# --------------------------------------------------------------------------


def test_init_returns_1_when_a_write_fails(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    # Make the very first managed write raise OSError — init must hard-fail
    # (exit 1, clean stderr), never silently best-effort past it.
    def boom(dest, src, *, exists):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(init, "_write_file", boom)

    rc = init.main(["--config-only"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed to write" in err


# --------------------------------------------------------------------------
# config-file list provenance — the documented seam scope
# --------------------------------------------------------------------------


def test_config_files_is_the_documented_config_subset():
    # The exact list PR-C chose from sync.py (lefthook.yml + the managed lint/
    # format configs). Pinned so a future drift is a conscious edit, and so the
    # PR-body provenance claim stays honest.
    assert init.CONFIG_FILES == (
        "lefthook.yml",
        ".markdownlint.json",
        ".markdownlintignore",
        ".yamllint",
        ".shellcheckrc",
        ".editorconfig",
        ".prettierignore",
    )
    # None of the release-internal / package-code paths leak in.
    for dest in init.CONFIG_FILES:
        assert not dest.startswith("lib/"), "package code is NOT init's scope"
        assert dest != sync.SOURCE_MARKER
        assert dest != "ORIENTATION.md"


# --------------------------------------------------------------------------
# resolution-failure surfaces (Kind / ref) → exit 1, clean message
# --------------------------------------------------------------------------


def test_init_kind_error_exits_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_kind(root, name, *, source_ref=None):
        raise manifest.KindError("nope")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_kind)
    rc = init.main(["--config-only"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "could not detect kind" in err


def test_init_sync_error_exits_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_sync(root, name, *, source_ref=None):
        raise sync.SyncError("release-core init: $RELEASE_HOME=... is not a git clone")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_sync)
    rc = init.main(["--config-only"])
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


def test_init_missing_source_for_kind_is_reported_not_fatal(tmp_path, monkeypatch, capsys):
    # If the engine produced no lefthook.yml (a Kind whose gate composes none),
    # init reports it on stderr and still materializes the rest, exit 0 — but the
    # final line must NOT claim the repo is fully initialized.
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    del sources["lefthook.yml"]
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "absent  lefthook.yml" in captured.err
    assert not (repo / "lefthook.yml").exists()
    assert (repo / ".yamllint").is_file()
    # Don't mislead: with a missing source, the repo is not "already initialized".
    assert "already initialized" not in captured.out
    assert "no source" in captured.out


def test_init_yaml_error_exits_1_not_traceback(tmp_path, monkeypatch, capsys):
    # A yamlio.YamlError out of the sync engine (missing yq, malformed manifest,
    # or a lefthook-fragment merge failure) must be caught at the CLI boundary →
    # clean exit 1, never a traceback escaping.
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_yaml(root, name, *, source_ref=None):
        raise yamlio.YamlError("yq -o=json . failed (1): bad YAML")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_yaml)
    rc = init.main(["--config-only"])
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
    # Post-#476: help must document the default full materialize, the
    # --config-only escape hatch, and that --full is a redundant alias.
    assert "--config-only" in out
    assert "DEFAULT" in out
    assert "redundant" in out


def test_init_unknown_flag_is_usage_error(capsys):
    rc = init.main(["--nope"])
    assert rc == 64


# --------------------------------------------------------------------------
# self-contained bundle path — compose config from the wheel-bundled templates
# (release_core/_bundled_templates/) with NO release clone. This is the DEFAULT
# path a pip-installed consumer takes; the tests above monkeypatch
# _materialize_config_sources wholesale, so the bundle composition below is the
# coverage for the actual offline machinery (the feat/wheel-self-contained work).
# --------------------------------------------------------------------------

# yq (mikefarah v4) is required for the lefthook fragment merge — the same hard
# dep release-sync has. Skip the merge-dependent tests cleanly if it's absent so
# a yq-less dev box doesn't see spurious failures (CI pins yq, so coverage holds).
import shutil as _shutil  # noqa: E402

import pytest  # noqa: E402

_HAVE_YQ = _shutil.which("yq") is not None
_needs_yq = pytest.mark.skipif(not _HAVE_YQ, reason="yq (mikefarah v4) not installed")

# The repo's real templates/ tree (tests/ -> release_core -> lib -> commons ->
# templates). Used as a faithful bundle so the composition tests exercise the
# ACTUAL fragments the wheel ships, not a hand-rolled stand-in.
_REAL_TEMPLATES = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _fake_bundle(tmp_path) -> str:
    """A minimal but valid bundle tree (templates/...) for the go-cli kind:
    commons lint configs + fragment, the base fragment, a go-quality capability
    fragment, and a go-cli manifest. Returns the tpl_root (…/templates)."""
    root = tmp_path / "_bundled_templates" / "templates"
    (root / "commons").mkdir(parents=True)
    (root / "components" / "go-quality").mkdir(parents=True)
    (root / "go-cli").mkdir(parents=True)

    # Static commons lint configs (copied verbatim by the bundle path).
    for name in (
        ".markdownlint.json",
        ".markdownlintignore",
        ".yamllint",
        ".shellcheckrc",
        ".editorconfig",
        ".prettierignore",
    ):
        (root / "commons" / name).write_text(f"# fake {name}\n")

    (root / "components" / "_lefthook-base.yaml").write_text(
        "pre-commit:\n  parallel: true\n  commands: {}\n"
    )
    (root / "commons" / "lefthook.fragment.yaml").write_text(
        "pre-commit:\n  commands:\n    markdownlint:\n      run: markdownlint .\n"
    )
    (root / "components" / "go-quality" / "lefthook.fragment.yaml").write_text(
        "pre-commit:\n  commands:\n    go-vet:\n      run: go vet ./...\n"
    )
    (root / "go-cli" / "manifest.yaml").write_text("kind: go-cli\ncapabilities:\n  - go-quality\n")
    return str(root)


# ---- _bundle_templates_root --------------------------------------------------


def test_bundle_templates_root_none_when_unstaged(monkeypatch, tmp_path):
    # No staged _bundled_templates/ next to the module → None, so init falls back
    # to the $RELEASE_HOME git path. (Not asserted against the live source tree:
    # a local `python -m build` stages the gitignored bundle on disk, which would
    # make a bare assertion flaky — so we drive the resolver at a clean fake dir.)
    fake_pkg = tmp_path / "release_core" / "verbs"
    fake_pkg.mkdir(parents=True)
    fake_file = fake_pkg / "init.py"
    fake_file.write_text("")
    monkeypatch.setattr(init.os.path, "realpath", lambda _p: str(fake_file))
    assert init._bundle_templates_root() is None


def test_bundle_templates_root_found_when_staged(monkeypatch, tmp_path):
    # Point the resolver at a fake module dir whose _bundled_templates/templates
    # exists; it must return that path.
    fake_pkg = tmp_path / "release_core" / "verbs"
    fake_pkg.mkdir(parents=True)
    bundle = tmp_path / "release_core" / "_bundled_templates" / "templates"
    bundle.mkdir(parents=True)
    fake_file = fake_pkg / "init.py"
    fake_file.write_text("")
    monkeypatch.setattr(init.os.path, "realpath", lambda _p: str(fake_file))
    assert init._bundle_templates_root() == str(bundle)


# ---- _capabilities_from_bundle ----------------------------------------------


@_needs_yq  # _capabilities_from_bundle parses the manifest via yamlio (yq)
def test_capabilities_from_bundle_reads_manifest(tmp_path):
    tpl_root = _fake_bundle(tmp_path)
    assert init._capabilities_from_bundle(tpl_root, "go-cli") == ["go-quality"]


def test_capabilities_from_bundle_missing_manifest_is_empty(tmp_path):
    tpl_root = _fake_bundle(tmp_path)
    assert init._capabilities_from_bundle(tpl_root, "no-such-kind") == []


# ---- _materialize_config_from_bundle (fake bundle) --------------------------


@_needs_yq
def test_materialize_from_bundle_composes_config_and_lefthook(tmp_path):
    tpl_root = _fake_bundle(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = init._materialize_config_from_bundle(tpl_root, str(repo), "go-cli", ["go-quality"])

    # Every static commons config is copied verbatim.
    for name in (
        ".markdownlint.json",
        ".yamllint",
        ".shellcheckrc",
        ".editorconfig",
        ".prettierignore",
        ".markdownlintignore",
    ):
        assert name in sources, f"{name} should be composed from the bundle"
        assert os.path.isfile(sources[name])

    # lefthook.yml is fragment-merged (base < commons < go-quality), carries the
    # "do not edit" provenance header, and contains commands from BOTH fragments.
    assert "lefthook.yml" in sources
    text = _read(sources["lefthook.yml"])
    assert "Generated by release-core init from the bundled templates" in text
    assert "markdownlint" in text  # from commons fragment
    assert "go-vet" in text  # from the go-quality capability fragment


@_needs_yq
def test_materialize_from_bundle_uses_real_templates(tmp_path):
    # The strongest proof: compose from the repo's ACTUAL templates tree exactly
    # as the staged bundle would, for the go-cli kind. Exercises the real
    # fragments the wheel ships, so a fragment-merge regression is caught here.
    repo = tmp_path / "repo"
    repo.mkdir()
    caps = init._capabilities_from_bundle(_REAL_TEMPLATES, "go-cli")
    assert "go-quality" in caps
    sources = init._materialize_config_from_bundle(_REAL_TEMPLATES, str(repo), "go-cli", caps)
    assert "lefthook.yml" in sources
    text = _read(sources["lefthook.yml"])
    # commons gate (markdownlint) + the go-quality capability (gofmt/go-vet).
    assert "markdownlint" in text
    assert "go-vet" in text
    # The full documented config subset is present (commons ships all of them).
    for name in init.CONFIG_FILES:
        assert name in sources, f"{name} missing from real-templates composition"


# ---- _materialize_config_sources routing ------------------------------------


@_needs_yq  # the spy calls the REAL composer, which fragment-merges via yq
def test_materialize_sources_defaults_to_bundle_when_no_release_home(tmp_path, monkeypatch):
    # No $RELEASE_HOME → the bundle path is taken (NOT the git engine).
    tpl_root = _fake_bundle(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init, "_bundle_templates_root", lambda: tpl_root)
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "go-cli")

    # Spy: the git engine must NOT be touched on the bundle path.
    def _boom(*a, **k):  # pragma: no cover - asserts non-invocation
        raise AssertionError("git sync engine must not run on the bundle path")

    monkeypatch.setattr(init.sync, "select_ref", _boom)

    called = {}
    real = init._materialize_config_from_bundle

    def _spy(tpl, root, kind, caps):
        called["caps"] = caps
        called["kind"] = kind
        return real(tpl, root, kind, caps)

    monkeypatch.setattr(init, "_materialize_config_from_bundle", _spy)

    sources = init._materialize_config_sources(str(repo), "repo")
    assert called["kind"] == "go-cli"
    assert called["caps"] == ["go-quality"]  # from the bundled manifest
    assert ".yamllint" in sources


@_needs_yq  # capability resolution parses .release-sync.yaml via yamlio (yq)
def test_materialize_sources_bundle_honors_consumer_sync_yaml(tmp_path, monkeypatch):
    # A consumer .release-sync.yaml capability override wins over the bundled
    # manifest default, on the offline path too.
    tpl_root = _fake_bundle(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".release-sync.yaml").write_text("capabilities:\n  - go-quality\n  - extra\n")
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init, "_bundle_templates_root", lambda: tpl_root)
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "go-cli")

    captured = {}

    def _spy(tpl, root, kind, caps):
        captured["caps"] = caps
        return {}

    monkeypatch.setattr(init, "_materialize_config_from_bundle", _spy)
    init._materialize_config_sources(str(repo), "repo")
    assert captured["caps"] == ["go-quality", "extra"]


def test_materialize_sources_release_home_overrides_bundle(tmp_path, monkeypatch):
    # An explicit $RELEASE_HOME git clone OVERRIDES the bundle: the full sync
    # engine runs (release-dev's live-templates path), bundle untouched.
    tpl_root = _fake_bundle(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    # The guard now probes git (is_git_worktree) instead of os.path.isdir(.git),
    # so this fake clone is reported as a work tree without a real `git init`.
    monkeypatch.setattr(init.gh, "is_git_worktree", lambda path: True)
    monkeypatch.setenv("RELEASE_HOME", str(clone))
    monkeypatch.setattr(init, "_bundle_templates_root", lambda: tpl_root)
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "go-cli")

    # The bundle composer must NOT run when a clone is present.
    def _boom(*a, **k):  # pragma: no cover - asserts non-invocation
        raise AssertionError("bundle path must not run when $RELEASE_HOME is a clone")

    monkeypatch.setattr(init, "_materialize_config_from_bundle", _boom)

    # Stub the git engine so the override path is exercised without real git.
    monkeypatch.setattr(init.sync, "select_ref", lambda *a, **k: "abcdef")
    monkeypatch.setattr(init.gh, "git_rev_parse", lambda *a, **k: "abcdef0")
    monkeypatch.setattr(
        init.sync,
        "resolve_capabilities",
        lambda *a, **k: sync.Capabilities(names=["go-quality"], manifest_source="x"),
    )
    monkeypatch.setattr(init.sync, "build_plan", lambda *a, **k: object())

    def _fake_materialize(source, sha, plan, dest):
        for name in init.CONFIG_FILES:
            with open(os.path.join(dest, name), "w", encoding="utf-8") as fh:
                fh.write(f"# git-engine {name}\n")

    monkeypatch.setattr(init.sync, "materialize", _fake_materialize)

    sources = init._materialize_config_sources(str(repo), "repo")
    assert set(sources) == set(init.CONFIG_FILES)
    assert _read(sources["lefthook.yml"]) == "# git-engine lefthook.yml\n"


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


def _tracked_status(repo):
    return _git(repo, "status", "--porcelain")


@_needs_git
def test_full_mode_tolerates_commit_flag_from_stale_resolver(tmp_path, monkeypatch, capsys):
    """Bootstrap-forward: the deployed SessionStart resolver in a not-yet-migrated
    consumer calls `release-core init --commit`. The default (full) init must
    TOLERATE that (warn + proceed, exit 0), not fail with a usage error — else the
    first cutover pull stalls the whole fleet (the resolver can't update the tree
    that would update the resolver). Caught by the dodot carrier run."""
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
def test_commit_commits_only_managed_files_when_changed(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--commit"])
    out = capsys.readouterr().out
    assert rc == 0
    # A new commit was made; its tree contains exactly the managed config files
    # (+ the pre-existing README from the initial commit).
    assert "committed 7 managed file(s)" in out
    assert "chore(release): sync managed config" in out
    last = _git(repo, "log", "-1", "--name-only", "--pretty=format:%s")
    lines = last.splitlines()
    assert lines[0].startswith("chore(release): sync managed config")
    committed = set(lines[1:])
    assert committed == set(init.CONFIG_FILES)
    # Working tree is clean after the commit (everything managed is committed).
    assert _tracked_status(repo) == ""


@_needs_git
def test_commit_message_includes_source_ref_when_known(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    # Hand back a source_ref the way the bundle/git-engine path would.
    def _materialize(root, name, *, source_ref=None):
        if source_ref is not None:
            source_ref["ref"] = "deadbeef1234"
        return sources

    monkeypatch.setattr(init, "_materialize_config_sources", _materialize)

    rc = init.main(["--config-only", "--commit"])
    assert rc == 0
    capsys.readouterr()
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject == "chore(release): sync managed config to deadbeef1234"


@_needs_git
def test_commit_no_commit_when_unchanged(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    assert init.main(["--config-only", "--commit"]) == 0
    capsys.readouterr()
    head_before = _git(repo, "rev-parse", "HEAD")

    # Second run: everything present, changed == 0 → no commit.
    rc = init.main(["--config-only", "--commit"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" not in out
    assert _git(repo, "rev-parse", "HEAD") == head_before


@_needs_git
def test_commit_does_not_stage_unrelated_working_tree_changes(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    # A user's in-progress work: one modified tracked file + one new untracked
    # file, neither managed. Must survive the managed commit untouched.
    (repo / "README.md").write_text("# repo — WIP edit\n")
    (repo / "feature.txt").write_text("user work\n")

    rc = init.main(["--config-only", "--commit"])
    assert rc == 0
    capsys.readouterr()
    # The managed commit must NOT include the user's files.
    committed = set(_git(repo, "log", "-1", "--name-only", "--pretty=format:").splitlines())
    committed.discard("")
    assert committed == set(init.CONFIG_FILES)
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
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    # A user already STAGED an unrelated change. The pathspec-scoped commit must
    # not absorb it.
    (repo / "staged.txt").write_text("already staged\n")
    _git(repo, "add", "staged.txt")

    rc = init.main(["--config-only", "--commit"])
    assert rc == 0
    capsys.readouterr()
    committed = set(_git(repo, "log", "-1", "--name-only", "--pretty=format:").splitlines())
    committed.discard("")
    assert "staged.txt" not in committed
    assert committed == set(init.CONFIG_FILES)
    # staged.txt is still staged, still uncommitted.
    assert "A  staged.txt" in _tracked_status(repo)


@_needs_git
def test_commit_dry_run_never_commits(tmp_path, monkeypatch, capsys):
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--config-only", "--commit", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" not in out
    assert _git(repo, "rev-parse", "HEAD") == head_before
    # Nothing was written either.
    for dest in init.CONFIG_FILES:
        assert not (repo / dest).exists()


def test_commit_in_non_git_dir_is_safe_no_op(tmp_path, monkeypatch, capsys):
    # repo_root is monkeypatched to a plain dir (no .git). init must still
    # materialize and succeed; --commit is a quiet no-op.
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--commit"])
    out = capsys.readouterr().out
    assert rc == 0
    for dest in init.CONFIG_FILES:
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
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--commit"])
    captured = capsys.readouterr()
    assert rc == 0
    # Files were still materialized.
    for dest in init.CONFIG_FILES:
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
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    pushes = []
    monkeypatch.setattr(init.gh, "git_push_ff", lambda branch, **k: pushes.append(branch))

    rc = init.main(["--config-only", "--push"])
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
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    # A non-managed dirty file on the default branch → push must be skipped.
    (repo / "dirty.txt").write_text("uncommitted\n")

    pushes = []
    monkeypatch.setattr(init.gh, "git_push_ff", lambda branch, **k: pushes.append(branch))

    rc = init.main(["--config-only", "--push"])
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
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--config-only", "--push"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "pushed to main." in out
    # The bare origin now has the managed commit. Read the explicit `main` ref,
    # not the bare's default HEAD: `git init --bare` points HEAD at
    # init.defaultBranch (often `master` on CI), so `git log` with no ref hits an
    # empty branch and exits 128 even though the push to `main` succeeded.
    assert "chore(release)" in _git(bare, "log", "-1", "--pretty=format:%s", "main")


@_needs_git
def test_commit_failure_does_not_fail_init(tmp_path, monkeypatch, capsys):
    # If the commit itself errors, init must still exit 0 (commit is best-effort).
    repo = _init_git_repo(tmp_path / "repo")
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    def boom(*a, **k):
        raise init.gh.proc.ProcError(["git", "commit"], 1, "nope")

    monkeypatch.setattr(init.gh, "git_commit_paths", boom)

    rc = init.main(["--config-only", "--commit"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "--commit skipped" in err


@_needs_yq
def test_main_end_to_end_through_bundle_path(tmp_path, monkeypatch, capsys):
    # Full main([]) over the offline bundle path: detect kind, compose from the
    # bundle, create-if-absent the documented set — no $RELEASE_HOME, no git.
    tpl_root = _fake_bundle(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_bundle_templates_root", lambda: tpl_root)
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "go-cli")

    rc = init.main(["--config-only"])
    out = capsys.readouterr().out
    assert rc == 0
    for name in init.CONFIG_FILES:
        assert (repo / name).is_file(), f"{name} should be materialized offline"
    assert "7 created" in out
    # The composed gate carries the bundle provenance header.
    assert "from the bundled templates" in (repo / "lefthook.yml").read_text()


# --------------------------------------------------------------------------
# init --full : the FULL managed-tree materialize + auto-commit-on-change
# --------------------------------------------------------------------------
# A synthetic full source (a tiny commons/ + a manifest-less kind + one
# distributed skill) is built once, then used BOTH as a wheel bundle (the
# BundleSource path) AND as a git release clone (the GitSource path). The two
# materialized trees must be byte-identical modulo the provenance-marker line —
# this is the load-bearing proof that init --full == release-sync.


def _full_source_tree(root) -> str:
    """Build a synthetic release-source tree under ``root``: templates/commons
    (a lint config + a real bin tool + a lefthook fragment + ORIENTATION.md),
    a manifest-less kind (tree-sitter) with its own fragment, and one PUSH_ALL
    skill (gh-pr-review-loop). Returns the abs path to ``root`` (the layout root,
    mirroring the repo: <root>/templates/… and <root>/skills/…)."""
    tpl = root / "templates"
    (tpl / "commons" / "bin").mkdir(parents=True)
    (tpl / "components").mkdir(parents=True)
    (tpl / "tree-sitter").mkdir(parents=True)
    (root / "skills" / "gh-pr-review-loop").mkdir(parents=True)

    (tpl / "commons" / ".editorconfig").write_text("root = true\n")
    tool = tpl / "commons" / "bin" / "check"
    tool.write_text("#!/bin/sh\necho check\n")
    os.chmod(tool, 0o755)
    (tpl / "commons" / "ORIENTATION.md").write_text("# Orientation\n")
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


def _materialize_via_bundle(tmp_path, src_root, kind):
    out = tmp_path / "out-bundle"
    out.mkdir()
    source = sync.BundleSource(str(src_root), ref_sha="release-core test")
    plan = sync.build_plan(source, kind, [])
    sync.materialize(source, source.ref_sha, plan, str(out))
    return out


def _materialize_via_git(tmp_path, clone, kind):
    out = tmp_path / "out-git"
    out.mkdir()
    source = sync.GitSource(str(clone), "HEAD", "abc123sha")
    plan = sync.build_plan(source, kind, [])
    sync.materialize(source, source.ref_sha, plan, str(out))
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
def test_full_bundle_materialize_matches_git_sync(tmp_path):
    # THE core proof: the BundleSource (init --full) tree is byte-identical to the
    # GitSource (release-sync) tree for the same Kind, modulo only the provenance
    # marker line (.release-sync-source) and the lefthook header sha line.
    src = _full_source_tree(tmp_path / "src")
    clone = _git_clone_from(src, tmp_path / "clone")

    b = _materialize_via_bundle(tmp_path, src, "tree-sitter")
    g = _materialize_via_git(tmp_path, clone, "tree-sitter")

    bf = _tree_files(b)
    gf = _tree_files(g)
    assert set(bf) == set(gf), "the two materialized trees must list the same files"

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
    """A throwaway git consumer wired so init --full takes the BundleSource path
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
def test_bare_init_does_full_materialize_and_auto_commits(tmp_path, monkeypatch, capsys):
    # THE cutover contract: a BARE `release-core init` (exactly what SessionStart
    # runs, no flags) materializes the FULL managed tree from the bundle AND
    # auto-commits the managed change. This is the #476 default.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # An unrelated dirty file must NOT be folded into the managed commit.
    (repo / "my-feature.txt").write_text("wip\n")

    rc = init.main([])  # no flags — the SessionStart invocation
    out = capsys.readouterr().out
    assert rc == 0
    assert "managed-tree change(s) applied" in out
    # Full tree (on disk) + working-tree mirrors + CLAUDE.md block.
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / "bin" / "check").is_symlink()
    assert "@.release/ORIENTATION.md" in (repo / "CLAUDE.md").read_text()
    # Auto-committed (the default) — only the MIRRORS, deterministic message.
    assert "committed" in out
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject.startswith("chore(release): sync managed tree from")
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # WS4 (release#521): the ephemeral .release/ tree is gitignored and never
    # committed — only the mirrors (symlinks, real-file copies, CLAUDE.md block).
    assert "bin/check" in committed
    assert not any(p.startswith(".release/") for p in committed)
    assert "CLAUDE.md" in committed
    assert "my-feature.txt" not in committed


@_needs_yq
@_needs_git
def test_bare_init_idempotent_no_commit_second_run(tmp_path, monkeypatch, capsys):
    # A bare second init with no upstream change → zero changes → no commit (the
    # no-op-when-current property the pull model relies on for churn = cadence).
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
    # symlink whose .release target is gone in a later sync is removed AND the
    # removal committed — no dangling link left behind (#476 / #481).
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main([]) == 0
    capsys.readouterr()
    assert (repo / "bin" / "check").is_symlink()

    os.remove(os.path.join(src, "templates", "commons", "bin", "check"))
    rc = init.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    assert not os.path.lexists(repo / "bin" / "check"), "left a dangling symlink"
    assert not (repo / ".release" / "bin" / "check").exists()
    assert _git(repo, "status", "--porcelain") == ""


@_needs_yq
@_needs_git
def test_full_materializes_tree_and_symlinks(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    rc = init.main(["--full", "--no-commit"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "managed-tree change(s) applied" in out
    # .release/ build dir materialized.
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / ".release" / ".claude" / "skills" / "gh-pr-review-loop" / "SKILL.md").is_file()
    # Working-tree symlinks mirrored.
    assert (repo / "bin" / "check").is_symlink()
    assert (repo / ".claude" / "skills" / "gh-pr-review-loop" / "SKILL.md").is_symlink()
    # CLAUDE.md orientation block created.
    assert "@.release/ORIENTATION.md" in (repo / "CLAUDE.md").read_text()
    # ORIENTATION.md is release-internal: in .release/ but NOT mirrored to root.
    assert (repo / ".release" / "ORIENTATION.md").is_file()
    assert not (repo / "ORIENTATION.md").exists()


@_needs_yq
@_needs_git
def test_full_auto_commits_only_managed_paths_when_changed(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # An unrelated dirty file must NOT be folded into the managed commit.
    (repo / "my-feature.txt").write_text("wip\n")

    rc = init.main(["--full"])  # auto-commit is the default in full mode
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    subject = _git(repo, "log", "-1", "--pretty=format:%s")
    assert subject.startswith("chore(release): sync managed tree from")
    # NO [skip ci]: it would make a managed-only migration PR un-mergeable under a
    # required-status-checks ruleset (CI skipped → required checks never satisfied).
    assert "[skip ci]" not in subject
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # Managed MIRRORS committed; the unrelated file is NOT. WS4 (release#521): the
    # ephemeral .release/ build dir is gitignored and never committed.
    assert not any(p.startswith(".release/") for p in committed)
    assert "bin/check" in committed
    assert "CLAUDE.md" in committed
    assert "my-feature.txt" not in committed
    assert _git(repo, "status", "--porcelain", "my-feature.txt") == "?? my-feature.txt"


@_needs_yq
@_needs_git
def test_full_force_adds_managed_paths_under_a_consumer_gitignore(tmp_path, monkeypatch, capsys):
    """A consumer .gitignore that covers a managed path (e.g. `.claude/` shadowing
    the managed `.claude/skills/`, here modeled with `bin/`) must NOT silently drop
    it from the migration commit — managed paths are release-owned and force-added.
    Regression: 6 fleet consumers gitignored `.claude/`, so their migration staged
    but never committed (`git add` errors on an ignored path)."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # Consumer ignores a directory the managed tree writes into.
    (repo / ".gitignore").write_text("/bin/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore bin")

    rc = init.main(["--full"])
    assert rc == 0
    committed = set(_git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
    # The managed bin/check is committed despite the .gitignore covering bin/.
    assert "bin/check" in committed
    assert _git(repo, "status", "--porcelain") == ""  # clean tree, nothing stranded


@_needs_yq
@_needs_git
def test_full_is_idempotent_no_commit_second_run(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    assert init.main(["--full"]) == 0
    capsys.readouterr()
    head1 = _git(repo, "rev-parse", "HEAD")

    # Second run: byte-identical tree → zero changes → no commit.
    assert init.main(["--full"]) == 0
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

    rc = init.main(["--full", "--no-commit"])
    assert rc == 0
    capsys.readouterr()
    # Tree materialized but no commit made; managed changes left in the worktree.
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain") != ""


@_needs_yq
@_needs_git
def test_full_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--full", "--dry-run"])
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
    # silently materialize an incomplete tree (commons/skills only).
    src = _full_source_tree(tmp_path / "src")
    repo = _init_git_repo(tmp_path / "consumer")
    monkeypatch.delenv("RELEASE_HOME", raising=False)
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_bundle_root", lambda: str(src))
    # detect a Kind the bundle has no templates/<kind>/ dir for.
    monkeypatch.setattr(init.manifest, "detect_kind", lambda root: "no-such-kind")

    rc = init.main(["--full", "--no-commit"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no" in err and "templates/no-such-kind/ tree" in err
    assert not (repo / ".release").exists()


@_needs_yq
@_needs_git
def test_full_commits_removals(tmp_path, monkeypatch, capsys):
    # A "removals-only" managed update (a symlink whose .release target is gone in
    # a later sync) must be staged + committed, not left dangling.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    assert init.main(["--full"]) == 0
    capsys.readouterr()
    assert (repo / "bin" / "check").is_symlink()

    # Drop the bin/check tool from the source, re-run: the managed symlink +
    # its .release/ target must be removed AND that removal committed.
    os.remove(os.path.join(src, "templates", "commons", "bin", "check"))
    rc = init.main(["--full"])
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


@_needs_yq
@_needs_git
def test_full_migration_untracks_previously_committed_release(tmp_path, monkeypatch, capsys):
    """WS4 one-time migration (release#521): a consumer that COMMITTED its `.release/`
    under the old model must, on the first init, have the whole tree untracked —
    INCLUDING paths that survive recomposition (e.g. `.release/bin/check`). The
    trap: a pathspec commit re-reads the work tree, so a naive `git commit --
    .release` resurrects the still-present recomposed files instead of deleting
    them. After init: zero tracked `.release/**`, a clean tree, and `.release/`
    still materialized on disk + the symlinks resolving."""
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    # Simulate a pre-WS4 consumer: commit a `.release/` tree, including a path the
    # recompose will REGENERATE (bin/check) and one it will NOT (lib/foo.py).
    (repo / ".release" / "bin").mkdir(parents=True)
    (repo / ".release" / "lib").mkdir(parents=True)
    (repo / ".release" / "bin" / "check").write_text("old committed check\n")
    (repo / ".release" / "lib" / "foo.py").write_text("stale\n")
    _git(repo, "add", "-f", ".release")
    _git(repo, "commit", "-q", "-m", "pre-WS4: committed .release/")
    assert len(_git(repo, "ls-files", "--", ".release").splitlines()) == 2

    rc = init.main([])  # default full materialize + auto-commit
    out = capsys.readouterr().out
    assert rc == 0
    assert "committed" in out
    # No `.release/**` tracked anymore — the whole ephemeral tree is untracked.
    assert _git(repo, "ls-files", "--", ".release") == ""
    # The migration commit recorded the surviving path as a DELETION, not a modify.
    names = _git(repo, "show", "--name-status", "--pretty=format:", "HEAD")
    assert "D\t.release/bin/check" in names
    assert "D\t.release/lib/foo.py" in names
    # Tree is clean; `.release/` is still on disk (ephemeral) and the symlink resolves.
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / "bin" / "check").is_symlink()
    assert (repo / "bin" / "check").resolve().is_file()
    # No untrack-commit stash left behind.
    assert not (repo / ".release.untrack-commit.tmp").exists()


# --------------------------------------------------------------------------
# Flag-combo guards under the post-#476 default (full IS the default; the guards
# key off "full mode is active" = NOT --config-only, never the literal --full).
# --------------------------------------------------------------------------


def test_push_and_no_commit_is_bad_usage(capsys):
    # --push implies a commit; --no-commit suppresses it — contradictory. In the
    # default (full) mode, with no other flag.
    rc = init.main(["--push", "--no-commit"])
    err = capsys.readouterr().err
    assert rc == 64
    assert "mutually exclusive" in err


def test_no_commit_with_config_only_is_bad_usage(capsys):
    # --no-commit governs only the (default) full auto-commit; --config-only does
    # not auto-commit, so the combo is rejected rather than silently ignored.
    rc = init.main(["--config-only", "--no-commit"])
    err = capsys.readouterr().err
    assert rc == 64
    assert "not valid with --config-only" in err


@_needs_yq
@_needs_git
def test_no_commit_in_default_full_mode_is_valid(tmp_path, monkeypatch, capsys):
    # The flip: a BARE `--no-commit` (default full mode) is now VALID — it skips
    # the auto-commit (was a usage error pre-#476). Run it for real and confirm
    # rc 0, the tree materialized, and NO commit was made.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)
    head_before = _git(repo, "rev-parse", "HEAD")

    rc = init.main(["--no-commit"])
    assert rc == 0
    capsys.readouterr()
    assert (repo / ".release" / "bin" / "check").is_file()  # full tree materialized
    assert _git(repo, "rev-parse", "HEAD") == head_before  # but no commit


# NOTE: --commit/--force in default full mode are now TOLERATED (warn + proceed),
# not rejected — the deployed stale resolver passes --commit on the first cutover
# pull. See test_full_mode_tolerates_commit_flag_from_stale_resolver above.


def test_full_flag_prints_deprecation_warning(capsys, monkeypatch, tmp_path):
    # --full is a redundant alias; passing it explicitly warns on stderr (so
    # stale fleet usage can be cleaned up) but is never an error. Drive it through
    # the mutually-exclusive guard so it exits before any materialize — the
    # warning is emitted regardless. (Gemini review on #482.)
    rc = init.main(["--full", "--config-only"])
    err = capsys.readouterr().err
    assert rc == 64  # the --config-only conflict still fails fast
    assert "--full is deprecated" in err


def test_full_and_config_only_are_mutually_exclusive(capsys):
    # --full (redundant alias of default) and --config-only pick opposite modes.
    rc = init.main(["--full", "--config-only"])
    assert rc == 64
    assert "mutually exclusive" in capsys.readouterr().err


def test_full_alias_is_redundant_no_op_equals_default(tmp_path, monkeypatch, capsys):
    # --full is accepted as a redundant alias of the default: it selects the same
    # full materialize a bare init does. Proven by running both against the same
    # synthetic bundle and asserting identical managed-tree output.
    src = _full_source_tree(tmp_path / "src")
    repo = _setup_full_repo(tmp_path, monkeypatch, src)

    # Bare init (default) materializes the full tree + auto-commits.
    assert init.main([]) == 0
    capsys.readouterr()
    head_after_default = _git(repo, "rev-parse", "HEAD")
    assert (repo / ".release" / "bin" / "check").is_file()
    assert (repo / "bin" / "check").is_symlink()

    # A second run via the --full alias is a no-op (byte-identical) → no commit,
    # exactly like a bare second init: proof the alias == the default.
    assert init.main(["--full"]) == 0
    out = capsys.readouterr().out
    assert "already current" in out
    assert _git(repo, "rev-parse", "HEAD") == head_after_default


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

    rc = init.main(["--full", "--no-commit"])
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

    rc = init.main(["--full"])
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

    rc = init.main(["--full", "--no-commit"])
    assert rc == 0
    capsys.readouterr()
    assert (repo / ".release" / "bin" / "check").is_file()
    # Provenance marker carries the git sha (not the wheel version).
    marker = (repo / ".release" / ".release-sync-source").read_text()
    assert "release-core" not in marker
