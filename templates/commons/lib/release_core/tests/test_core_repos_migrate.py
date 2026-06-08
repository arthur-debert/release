"""release-core admin repos migrate — the fleet pull-model seeder."""

import types

from release_core.verbs import repos_migrate


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_common(monkeypatch, *, roster, ahead, files=3):
    """Stub the I/O boundaries so the orchestration logic is testable offline."""
    monkeypatch.setattr(repos_migrate.managed_repos, "_manifest_path", lambda: "manifest")
    monkeypatch.setattr(repos_migrate.managed_repos, "_pairs", lambda m, f: roster)
    monkeypatch.setattr(
        repos_migrate.gh, "repo_clone", lambda r, d: types.SimpleNamespace(returncode=0)
    )

    def fake_git(args, **kw):
        if "rev-list" in args:
            return str(ahead)
        if "show" in args:
            return "\n".join(f"f{i}" for i in range(files))
        if "symbolic-ref" in args:
            return "refs/remotes/origin/main"
        return ""

    monkeypatch.setattr(repos_migrate.gh, "git", fake_git)
    monkeypatch.setattr(repos_migrate, "_run_init", lambda d: True)


def test_dry_run_never_pushes_or_opens_prs(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, roster=[("owner/repo", "p")], ahead=1)
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return _proc()

    monkeypatch.setattr(repos_migrate.subprocess, "run", fake_run)
    rc = repos_migrate.main(["--dry-run", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run: would open PR" in out
    assert calls == []  # no gh pr create


def test_already_current_when_init_makes_no_commit(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, roster=[("owner/repo", "p")], ahead=0)
    rc = repos_migrate.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already current" in out


def test_unexpected_commit_count_is_held_not_pushed(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, roster=[("owner/repo", "p")], ahead=2)
    pushed = []
    monkeypatch.setattr(repos_migrate.subprocess, "run", lambda *a, **k: pushed.append(a) or None)
    rc = repos_migrate.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1  # errored
    assert "HOLD" in out
    assert pushed == []


def test_opens_a_pr_per_migrated_repo(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, roster=[("owner/a", "pa"), ("owner/b", "pb")], ahead=1)
    monkeypatch.setattr(
        repos_migrate.subprocess,
        "run",
        lambda *a, **k: _proc(stdout="https://github.com/owner/a/pull/1\n"),
    )
    rc = repos_migrate.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("PR https://") == 2


def test_only_filter_is_passed_through(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(repos_migrate.managed_repos, "_manifest_path", lambda: "m")

    def spy_pairs(manifest, filt):
        seen["filter"] = filt
        return []

    monkeypatch.setattr(repos_migrate.managed_repos, "_pairs", spy_pairs)
    repos_migrate.main(["--only", "owner/a,owner/b", "--root", str(tmp_path)])
    assert seen["filter"] == ["owner/a", "owner/b"]
