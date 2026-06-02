"""init verb (verbs/init.py): the create-if-absent config materializer that is
the pip-bootstrap PoC seam (§2).

The release-sync engine that composes the source content is exercised by
test_core_sync.py / test_core_release_sync_verb.py; here we monkeypatch
init._materialize_config_sources to hand back a temp tree of fixture config
files, then pin init's OWN contract: create-if-absent, idempotency, --force
overwrite, --dry-run, and the hard non-zero exit when a write fails. The source
resolution + Kind/ref failure surfaces are covered by their own tests below.
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
    monkeypatch.setattr(init, "_materialize_config_sources", lambda root, name: sources)


# --------------------------------------------------------------------------
# create-if-absent
# --------------------------------------------------------------------------


def test_init_creates_all_config_files_when_absent(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main([])
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

    rc = init.main([])
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

    assert init.main([]) == 0
    capsys.readouterr()
    # Snapshot the tree after the first run.
    before = {
        dest: (repo / dest).read_text() for dest in init.CONFIG_FILES if (repo / dest).is_file()
    }

    rc = init.main([])
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

    rc = init.main(["--force"])
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

    assert init.main(["--force"]) == 0
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

    rc = init.main([])
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

    assert init.main([]) == 0
    assert os.environ["RELEASE_HOME"] == str(tmp_path / "rel" / "clone")


# --------------------------------------------------------------------------
# --dry-run writes nothing
# --------------------------------------------------------------------------


def test_init_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    sources = _fixture_sources(tmp_path)
    _patch(monkeypatch, repo, sources)

    rc = init.main(["--dry-run"])
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

    rc = init.main([])
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

    def raise_kind(root, name):
        raise manifest.KindError("nope")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_kind)
    rc = init.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "could not detect kind" in err


def test_init_sync_error_exits_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_sync(root, name):
        raise sync.SyncError("release-core init: $RELEASE_HOME=... is not a git clone")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_sync)
    rc = init.main([])
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

    rc = init.main([])
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
    # clean exit 1, never a traceback, matching release_sync's contract.
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))

    def raise_yaml(root, name):
        raise yamlio.YamlError("yq -o=json . failed (1): bad YAML")

    monkeypatch.setattr(init, "_materialize_config_sources", raise_yaml)
    rc = init.main([])
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


def test_init_unknown_flag_is_usage_error(capsys):
    rc = init.main(["--nope"])
    assert rc == 64
