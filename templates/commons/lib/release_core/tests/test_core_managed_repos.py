"""managed_repos verb — manifest parsing, path join, filter, mode dispatch.

Fully offline: a fixture manifest via MANAGED_REPOS_MANIFEST + a synthetic
REPOS_ROOT. No gh, no network (clone mode's gh boundary is not exercised here —
it is covered by the BATS contract test against a stub gh).
"""

from __future__ import annotations

import os
import subprocess

import pytest
from release_core import gh, proc
from release_core.verbs import managed_repos


def _cp(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


FIXTURE = """\
projects:
  lex:
    - { repo: lex-fmt/lex,   path: lex-fmt/lex }
    - { repo: lex-fmt/comms, path: lex-fmt/comms }
  phos:
    - { repo: arthur-debert/phos-app, path: phos/phos-app }
  dodot:
    - { repo: arthur-debert/dodot, path: dodot }
"""


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(FIXTURE)
    root = tmp_path / "root"
    # lex-fmt/lex and dodot "exist" (.git); comms and phos-app do not.
    (root / "lex-fmt" / "lex" / ".git").mkdir(parents=True)
    (root / "dodot" / ".git").mkdir(parents=True)
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPOS_ROOT", str(root))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    return root


def test_list_prints_every_repo_in_manifest_order(fleet, capsys):
    rc = managed_repos.main(["--list"])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["lex-fmt/lex", "lex-fmt/comms", "arthur-debert/phos-app", "arthur-debert/dodot"]


def test_list_is_the_default_mode(fleet, capsys):
    managed_repos.main([])
    assert capsys.readouterr().out.splitlines()[0] == "lex-fmt/lex"


def test_paths_joins_root_and_marks_found_missing(fleet, capsys):
    rc = managed_repos.main(["--paths"])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    root = os.environ["REPOS_ROOT"]
    assert lines[0] == f"lex-fmt/lex\t{root}/lex-fmt/lex\tfound"
    assert f"lex-fmt/comms\t{root}/lex-fmt/comms\tmissing" in lines
    assert f"arthur-debert/dodot\t{root}/dodot\tfound" in lines


def test_paths_uses_tabs(fleet, capsys):
    managed_repos.main(["--paths"])
    assert "\t" in capsys.readouterr().out.splitlines()[0]


def test_filter_restricts_and_preserves_manifest_order(fleet, capsys):
    managed_repos.main(["--list", "arthur-debert/dodot", "lex-fmt/lex"])
    # manifest order, not arg order: lex precedes dodot in the manifest.
    assert capsys.readouterr().out.splitlines() == ["lex-fmt/lex", "arthur-debert/dodot"]


def test_filter_with_no_matches_prints_nothing(fleet, capsys):
    rc = managed_repos.main(["--list", "lex-fmt/nonesuch"])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_unknown_flag_is_usage_error(fleet, capsys):
    rc = managed_repos.main(["--bogus"])
    assert rc == 64
    assert "unknown arg" in capsys.readouterr().err


def test_help_exits_zero(fleet, capsys):
    rc = managed_repos.main(["--help"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


def test_missing_manifest_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(tmp_path / "nope.yaml"))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    rc = managed_repos.main(["--list"])
    assert rc == 2
    assert "manifest not found" in capsys.readouterr().err


# --------------------------------------------------------------------------
# known_repos / validate_repo_targets — the per-repo-targeting registry (#601)
# --------------------------------------------------------------------------

CANARY_FIXTURE = FIXTURE + "canaries:\n  rust: arthur-debert/release-canary-rust\n"


@pytest.fixture
def canary_fleet(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(CANARY_FIXTURE)
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(manifest))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    return manifest


def test_known_repos_unions_projects_and_canaries(canary_fleet):
    assert managed_repos.known_repos() == {
        "lex-fmt/lex",
        "lex-fmt/comms",
        "arthur-debert/phos-app",
        "arthur-debert/dodot",
        "arthur-debert/release-canary-rust",
    }


def test_known_repos_without_canaries_block(fleet):
    # FIXTURE has no canaries: block — projects only, no crash.
    assert managed_repos.known_repos() == {
        "lex-fmt/lex",
        "lex-fmt/comms",
        "arthur-debert/phos-app",
        "arthur-debert/dodot",
    }


def test_validate_repo_targets_all_known_is_empty(canary_fleet):
    err = managed_repos.validate_repo_targets(["lex-fmt/lex", "arthur-debert/release-canary-rust"])
    assert err == ""


def test_validate_repo_targets_unknown_names_the_registry(canary_fleet):
    err = managed_repos.validate_repo_targets(["o/zzz", "lex-fmt/lex", "o/aaa"])
    assert "managed-repos.yaml" in err
    assert "projects: or canaries:" in err
    # only the unknown entries, sorted
    assert err.endswith("o/aaa, o/zzz")
    assert "lex-fmt/lex" not in err


def test_validate_repo_targets_unreadable_registry_is_a_message(tmp_path, monkeypatch):
    # Missing manifest (or yq failure) → clean hard-error string, never a traceback.
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(tmp_path / "nope.yaml"))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    err = managed_repos.validate_repo_targets(["lex-fmt/lex"])
    assert "cannot read the fleet registry" in err
    assert "managed-repos.yaml" in err


def test_validate_repo_targets_structurally_invalid_registry_is_a_message(tmp_path, monkeypatch):
    # Parseable YAML but the wrong SHAPE (entries are strings, not {repo, path}
    # mappings) → the KeyError/TypeError out of _pairs is converted to the same
    # clean hard-error string, never a traceback.
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("projects:\n  o:\n    - just-a-string\n")
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(manifest))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    err = managed_repos.validate_repo_targets(["lex-fmt/lex"])
    assert "cannot read the fleet registry" in err
    assert "managed-repos.yaml" in err


# --------------------------------------------------------------------------
# --clone: refresh existing clones UNCONDITIONALLY + name the ref/sha (#624)
# --------------------------------------------------------------------------


class _GitDriver:
    """Records git/proc.run calls and scripts their returncodes.

    `fail_fetch` / `fail_reset` are sets of abspaths whose fetch/reset should
    return non-zero, so a test can force a refresh failure for one repo."""

    def __init__(self, fail_fetch=None, fail_reset=None, sha="deadbee"):
        self.fail_fetch = fail_fetch or set()
        self.fail_reset = fail_reset or set()
        self.sha = sha
        self.calls: list[list[str]] = []
        self._real_run = proc.run  # delegate non-git calls (yq manifest read)

    def __call__(self, cmd, **kw):
        # The manifest read goes through yq (yamlio) — pass it to the real
        # proc.run; this driver only scripts the git fetch/reset/rev-parse.
        if cmd and cmd[0] != "git":
            return self._real_run(cmd, **kw)
        self.calls.append(cmd)
        # git -C <abspath> <subcmd> ...
        abspath = cmd[2] if len(cmd) > 2 and cmd[1] == "-C" else None
        sub = cmd[3] if len(cmd) > 3 else ""
        if sub == "symbolic-ref":
            return _cp(0, stdout="refs/remotes/origin/main\n")
        if sub == "fetch":
            return _cp(1 if abspath in self.fail_fetch else 0)
        if sub == "reset":
            return _cp(1 if abspath in self.fail_reset else 0)
        if sub == "rev-parse":
            return _cp(0, stdout=f"{self.sha}\n")
        raise AssertionError(f"unexpected proc.run: {cmd}")


def _git_calls(driver, abspath, subcmd):
    return [c for c in driver.calls if c[:4] == ["git", "-C", abspath, subcmd]]


def test_clone_refreshes_existing_clone_unconditionally(fleet, monkeypatch, capsys):
    # No --refresh flag anywhere — an EXISTING clone (lex-fmt/lex) must still be
    # fetched + hard-reset to origin's default branch on a bare --clone (#624).
    root = str(fleet)
    lex = os.path.join(root, "lex-fmt", "lex")
    driver = _GitDriver(sha="cafef00")
    monkeypatch.setattr(proc, "run", driver)
    # Missing repos would call gh.repo_clone; make it a no-op success so the test
    # stays focused on the refresh path.
    monkeypatch.setattr(gh, "repo_clone", lambda repo, dest, **kw: _cp(0))

    rc = managed_repos.main(["--clone", "lex-fmt/lex"])
    assert rc == 0
    # The existing clone was fetched (shallow) and hard-reset — unconditionally.
    fetch = _git_calls(driver, lex, "fetch")
    assert fetch and "--depth" in fetch[0] and fetch[0][-2:] == ["origin", "main"]
    reset = _git_calls(driver, lex, "reset")
    assert reset and reset[0][-1] == "origin/main"
    # The readout NAMES the ref/sha the clone now sits at (freshness is visible).
    err = capsys.readouterr().err
    assert "lex-fmt/lex: refreshed to main@cafef00" in err
    assert "exists, skipping" not in err


def test_clone_refresh_failure_is_nonzero_and_named(fleet, monkeypatch, capsys):
    root = str(fleet)
    lex = os.path.join(root, "lex-fmt", "lex")
    driver = _GitDriver(fail_fetch={lex})
    monkeypatch.setattr(proc, "run", driver)
    monkeypatch.setattr(gh, "repo_clone", lambda repo, dest, **kw: _cp(0))

    rc = managed_repos.main(["--clone", "lex-fmt/lex"])
    assert rc == 1
    assert "lex-fmt/lex: refresh FAILED" in capsys.readouterr().err


def test_clone_clones_missing_and_names_sha(fleet, monkeypatch, capsys):
    # comms is MISSING in the fixture → it gets cloned (gh.repo_clone) and the
    # readout names the cloned-at sha; the rev-parse runs in the new clone.
    driver = _GitDriver(sha="abc1234")
    monkeypatch.setattr(proc, "run", driver)
    cloned: list[str] = []

    def _fake_clone(repo, dest, **kw):
        cloned.append(repo)
        return _cp(0)

    monkeypatch.setattr(gh, "repo_clone", _fake_clone)

    rc = managed_repos.main(["--clone", "lex-fmt/comms"])
    assert rc == 0
    assert cloned == ["lex-fmt/comms"]
    err = capsys.readouterr().err
    assert "lex-fmt/comms: cloned at abc1234" in err


def test_clone_rejects_removed_refresh_flag(fleet, capsys):
    # #624: --refresh was removed with no tolerated no-op — it is now an unknown
    # arg, the same usage error as any other bogus flag.
    rc = managed_repos.main(["--clone", "--refresh"])
    assert rc == 64
    assert "unknown arg: --refresh" in capsys.readouterr().err
