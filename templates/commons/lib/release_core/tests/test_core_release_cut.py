"""release_cut verb — per-Kind version readers, semver bump, literal-version
guard, and the gh-dispatch path.

Offline: the per-Kind readers run against real fixture files in tmp_path; the
git/gh boundary is stubbed at proc.run/proc.out (a recorded command map), never
at subprocess. Mirrors the byte-for-byte contract pinned by
tests/release-cut/release-cut.bats.
"""

from __future__ import annotations

import pytest
from release_core import proc
from release_core.verbs import release_cut

# --- per-Kind version readers (pure file parsing) ---------------------


def test_read_toml_package_version(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\nversion = "1.4.2"\n')
    assert release_cut._read_toml_version(str(tmp_path / "Cargo.toml")) == "1.4.2"


def test_read_toml_workspace_package_version(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace.package]\nversion = "0.7.2-rc.2"\nedition = "2024"\n'
    )
    assert release_cut._read_toml_version(str(tmp_path / "Cargo.toml")) == "0.7.2-rc.2"


def test_read_toml_ignores_dependency_section_version(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace.dependencies.serde]\nversion = "1.0.219"\nfeatures = []\n'
    )
    assert release_cut._read_toml_version(str(tmp_path / "Cargo.toml")) is None


def test_read_toml_extension_toplevel(tmp_path):
    # extension.toml shape: top-level version before any [section].
    (tmp_path / "extension.toml").write_text('id = "lex"\nversion = "0.1.2-rc.1"\n')
    assert release_cut._read_toml_version(str(tmp_path / "extension.toml")) == "0.1.2-rc.1"


def test_read_json_version(tmp_path):
    (tmp_path / "package.json").write_text('{\n  "name": "lexed",\n  "version": "0.10.7-rc.2"\n}\n')
    assert release_cut._read_json_version(str(tmp_path / "package.json")) == "0.10.7-rc.2"


def test_read_json_ignores_nested_version(tmp_path):
    (tmp_path / "package.json").write_text(
        '{\n  "name": "foo",\n  "version": "2.0.0",\n'
        '  "devDependencies": { "some-package-version": "9.9.9" }\n}\n'
    )
    assert release_cut._read_json_version(str(tmp_path / "package.json")) == "2.0.0"


def test_read_rust_workspace_only_probes_members(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nresolver = "2"\nmembers = ["crates/lib", "crates/cli"]\n'
    )
    (tmp_path / "crates" / "lib").mkdir(parents=True)
    (tmp_path / "crates" / "lib" / "Cargo.toml").write_text(
        '[package]\nname = "foo-lib"\nversion = "5.0.1-rc.1"\n'
    )
    (tmp_path / "crates" / "cli").mkdir(parents=True)
    (tmp_path / "crates" / "cli" / "Cargo.toml").write_text(
        '[package]\nname = "foo"\nversion.workspace = true\n'
    )
    monkeypatch.chdir(tmp_path)
    assert release_cut._read_rust_version() == "5.0.1-rc.1"


def test_read_rust_multiline_members(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = [\n    "crates/lib",\n    "crates/cli",\n]\n'
    )
    (tmp_path / "crates" / "lib").mkdir(parents=True)
    (tmp_path / "crates" / "lib" / "Cargo.toml").write_text(
        '[package]\nname = "foo-lib"\nversion = "2.0.0"\n'
    )
    monkeypatch.chdir(tmp_path)
    assert release_cut._read_rust_version() == "2.0.0"


# --- literal-version guard --------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "1.2.3",
        "0.0.1",
        "1.0.0-rc.1",
        "10.20.30-beta.2",
        # A 0 in a numeric field is fine (NAT = '0|[1-9][0-9]*'); only a
        # LEADING zero on a multi-digit field is rejected.
        "0.0.0",
        "1.0.0-0",  # bare '0' prerelease identifier is NAT-valid
    ],
)
def test_literal_version_accepts(good):
    assert release_cut._is_valid_literal_version(good)


@pytest.mark.parametrize(
    "bad",
    [
        "v1.2.3",
        "V1.2.3",
        "not-a-version",
        "1.2",
        "1.2.3+build.7",
        "minor",
        # Leading-zero parity (FU2): the bash semver-tool's NAT field
        # ('0|[1-9][0-9]*') REJECTS these; release_core.version.parse would
        # silently ACCEPT + normalize them, which was the reject-path break.
        "01.0.0",  # leading-zero major
        "1.01.0",  # leading-zero minor
        "1.0.01",  # leading-zero patch
        "1.00.0",  # multi-zero minor
        "1.0.0-01",  # leading-zero numeric prerelease identifier
        "01.0.0+build",  # leading zero is still caught even with a build part
    ],
)
def test_literal_version_rejects(bad):
    assert not release_cut._is_valid_literal_version(bad)


def test_leading_zero_literal_exits_2(gh_dispatch, capsys):
    """End-to-end: `release-cut 01.0.0` must hit the invalid-literal branch
    (exit 2, same message), not be silently accepted + normalized to 1.0.0."""
    rc = release_cut.main(["01.0.0"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "version must be" in err
    # And it must NOT have dispatched a (normalized) gh workflow run.
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


# --- main() dispatch (git/gh stubbed at the data layer) ---------------


@pytest.fixture
def gh_dispatch(monkeypatch, tmp_path):
    """A repo dir with release.yml + a recording of the gh dispatch call."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    monkeypatch.chdir(tmp_path)
    # Hermetic canary registry: no manifest in tmp_path (and no ambient
    # override) ⇒ no canaries registered ⇒ the #606 cut gate is inert.
    monkeypatch.delenv("MANAGED_REPOS_MANIFEST", raising=False)
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    calls: list[list[str]] = []

    def fake_out(cmd, **kw):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return str(tmp_path)
        raise AssertionError(f"unexpected proc.out: {cmd}")

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", fake_out)
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")
    return calls


def _completed(cmd, *, returncode=0, stdout="", stderr=""):
    import subprocess

    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def test_literal_version_dispatches(gh_dispatch, capsys):
    rc = release_cut.main(["1.2.3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Triggering release.yml for v1.2.3..." in out
    assert "Workflow queued" in out
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=1.2.3"] in gh_dispatch


def test_workflow_dispatch_422_names_remediation(monkeypatch, tmp_path, capsys):
    """#725: a consumer release.yml without a workflow_dispatch trigger makes
    `gh workflow run` fail with HTTP 422. cut forwards gh's error AND names the
    fix (add an `on: workflow_dispatch:` trigger)."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MANAGED_REPOS_MANIFEST", raising=False)
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["gh", "workflow"]:
            return _completed(
                cmd,
                returncode=1,
                stderr="HTTP 422: Workflow does not have 'workflow_dispatch' trigger",
            )
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")

    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "workflow_dispatch:" in err
    assert "not dispatchable" in err


def test_bad_version_exits_2(gh_dispatch, capsys):
    rc = release_cut.main(["not-a-version"])
    assert rc == 2
    assert "version must be" in capsys.readouterr().err


def test_help_surfaces_release_rc_reservation(capsys):
    """#693: `cut --help` documents the reserved `-release-rc` suffix, not just
    the version grammar + pre-release stripping."""
    rc = release_cut.main(["--help"])
    assert rc == 0
    assert "-release-rc" in capsys.readouterr().err


def test_no_args_exits_2(capsys):
    rc = release_cut.main([])
    assert rc == 2
    assert "usage:" in capsys.readouterr().err


def test_help_exits_0(capsys):
    rc = release_cut.main(["--help"])
    assert rc == 0
    assert "usage:" in capsys.readouterr().err


def test_no_release_yml_is_graceful_noop(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    rc = release_cut.main(["minor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no .github/workflows/release.yml" in out
    assert "nothing to do" in out


def test_bump_reads_kind_and_dispatches(monkeypatch, tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\nversion = "1.4.2"\n')
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")

    rc = release_cut.main(["minor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bumping minor: 1.4.2 -> 1.5.0" in out
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=1.5.0"] in calls


def test_bump_on_unclassifiable_dir_reads_git_tag(monkeypatch, tmp_path, capsys):
    """#596: a manifest-less repo with no detectable Kind (the release meta
    repo itself) is tag-sourced — the bump shortcut reads the latest tag."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "README.md").write_text("nothing detectable\n")
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["git", "describe"]:
            return _completed(cmd, returncode=0, stdout="v2.18.0\n")
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")

    rc = release_cut.main(["minor"])
    assert rc == 0
    assert "Bumping minor: 2.18.0 -> 2.19.0" in capsys.readouterr().out
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=2.19.0"] in calls


def test_bump_on_unclassifiable_dir_no_tags_errors(monkeypatch, tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "README.md").write_text("nothing detectable\n")
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "describe"]:
            return _completed(cmd, returncode=128, stdout="", stderr="no tags")
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    rc = release_cut.main(["minor"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no git tags found" in err
    assert "no detectable Kind" in err
    assert "explicit version" in err


def test_gh_missing_exits_1(monkeypatch, tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: None)
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    assert "gh CLI not found" in capsys.readouterr().err


def test_workspace_only_root_git_tag_kind(monkeypatch, tmp_path, capsys):
    # go-cli reads `git describe`; stub it returning a tag.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "go.mod").write_text("module example.com/foo\n")
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["git", "describe"]:
            return _completed(cmd, returncode=0, stdout="v0.3.1\n")
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")

    rc = release_cut.main(["minor"])
    assert rc == 0
    assert "Bumping minor: 0.3.1 -> 0.4.0" in capsys.readouterr().out


# --- canary gate (#606) -------------------------------------------------
# The cut refuses without green canary/<family> commit statuses on the exact
# default-branch HEAD being cut. Registry-driven: no canaries registered ⇒ no
# gate (a consumer repo has no managed-repos.yaml). NO skip flag by design.

_GATE_SHA = "deadbeef" * 5


def _fake_rest(combined_statuses, *, sha=_GATE_SHA, calls=None):
    """A gh.rest stub serving the two gate endpoints (repo → default_branch,
    combined status for that branch head)."""

    def rest(path, **kw):
        if calls is not None:
            calls.append(path)
        if path == "repos/{owner}/{repo}":
            return {"default_branch": "main"}
        if path == "repos/{owner}/{repo}/commits/main/status":
            return {"sha": sha, "statuses": combined_statuses}
        raise AssertionError(f"unexpected gh.rest: {path}")

    return rest


@pytest.fixture
def canary_registry(monkeypatch):
    """Two registered families — the gate must require BOTH green."""
    monkeypatch.setattr(
        release_cut.managed_repos,
        "canaries",
        lambda manifest=None: {
            "rust": "arthur-debert/release-canary-rust",
            "npm": "arthur-debert/release-canary-npm",
        },
    )


def test_gate_all_green_proceeds(gh_dispatch, canary_registry, monkeypatch, capsys):
    monkeypatch.setattr(
        release_cut.gh,
        "rest",
        _fake_rest(
            [
                {"context": "canary/rust", "state": "success"},
                {"context": "canary/npm", "state": "success"},
                # Unrelated contexts never participate in the gate.
                {"context": "ci/lint", "state": "failure"},
            ]
        ),
    )
    rc = release_cut.main(["1.2.3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"Canary gate green on {_GATE_SHA[:12]}" in out
    assert "canary/npm" in out and "canary/rust" in out
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=1.2.3"] in gh_dispatch


def test_gate_missing_context_refuses_naming_it(gh_dispatch, canary_registry, monkeypatch, capsys):
    # rust is green on this sha; npm never reported — the round is incomplete.
    monkeypatch.setattr(
        release_cut.gh,
        "rest",
        _fake_rest([{"context": "canary/rust", "state": "success"}]),
    )
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "REFUSING to cut" in err
    assert "canary/npm: missing (no status on this sha)" in err
    assert "canary/rust" not in err  # only the not-green contexts are named
    assert "run: release-core admin canary run --ref main" in err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


@pytest.mark.parametrize("state", ["pending", "failure", "error"])
def test_gate_non_success_state_refuses(gh_dispatch, canary_registry, monkeypatch, capsys, state):
    monkeypatch.setattr(
        release_cut.gh,
        "rest",
        _fake_rest(
            [
                {"context": "canary/rust", "state": state},
                {"context": "canary/npm", "state": "success"},
            ]
        ),
    )
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    err = capsys.readouterr().err
    assert f"canary/rust: {state}" in err
    assert "run: release-core admin canary run --ref main" in err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


def test_gate_stale_round_is_missing_by_construction(
    gh_dispatch, canary_registry, monkeypatch, capsys
):
    """Exact-sha binding IS the freshness check: a green round on an older
    main sha leaves the CURRENT head with no statuses at all — the combined
    status for the head simply doesn't carry the contexts."""
    monkeypatch.setattr(release_cut.gh, "rest", _fake_rest([]))
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "canary/npm: missing (no status on this sha)" in err
    assert "canary/rust: missing (no status on this sha)" in err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


def test_no_canaries_registered_means_no_gate(gh_dispatch, monkeypatch, capsys):
    """A consumer repo (no canaries registered) never hits the status API —
    the gate is inert by registry, not by a skip flag."""
    monkeypatch.setattr(release_cut.managed_repos, "canaries", lambda manifest=None: {})

    def no_rest(path, **kw):
        raise AssertionError(f"gate must not call gh.rest without a registry: {path}")

    monkeypatch.setattr(release_cut.gh, "rest", no_rest)
    rc = release_cut.main(["1.2.3"])
    assert rc == 0
    assert "Canary gate" not in capsys.readouterr().out
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=1.2.3"] in gh_dispatch


def test_gate_status_api_error_refuses(gh_dispatch, canary_registry, monkeypatch, capsys):
    from release_core import gh as gh_mod

    def boom(path, **kw):
        raise gh_mod.GhError("api unreachable")

    monkeypatch.setattr(release_cut.gh, "rest", boom)
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    assert "cannot read commit statuses" in capsys.readouterr().err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


def test_gate_binds_to_the_repo_being_cut_not_the_shim_env(gh_dispatch, monkeypatch, tmp_path):
    """bin/release-core exports MANAGED_REPOS_SCRIPT_DIR (and tests may set
    MANAGED_REPOS_MANIFEST) pinning managed_repos's default resolution at
    RELEASE's own manifest — that must NOT leak the gate into another repo's
    cut. The registry is the manifest of the repo being cut (cwd repo root),
    which here has none ⇒ no gate, even with the env pointing at a registry."""
    other = tmp_path / "elsewhere-managed-repos.yaml"
    other.write_text("canaries:\n  rust: arthur-debert/release-canary-rust\n")
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(other))
    monkeypatch.setenv("MANAGED_REPOS_SCRIPT_DIR", str(tmp_path))

    def no_rest(path, **kw):
        raise AssertionError(f"gate must not consult another repo's registry: {path}")

    monkeypatch.setattr(release_cut.gh, "rest", no_rest)
    rc = release_cut.main(["1.2.3"])
    assert rc == 0
    assert ["gh", "workflow", "run", "release.yml", "-f", "version=1.2.3"] in gh_dispatch


def test_gate_registry_read_error_refuses(gh_dispatch, monkeypatch, capsys):
    def broken_registry(manifest=None):
        raise RuntimeError("yq exploded")

    monkeypatch.setattr(release_cut.managed_repos, "canaries", broken_registry)
    rc = release_cut.main(["1.2.3"])
    assert rc == 1
    assert "cannot read the canaries registry" in capsys.readouterr().err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh_dispatch)


def test_gate_runs_on_bump_shortcut_path_too(canary_registry, monkeypatch, tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\nversion = "1.4.2"\n')
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setattr(release_cut.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(release_cut.gh, "rest", _fake_rest([]))

    rc = release_cut.main(["minor"])
    assert rc == 1
    assert "REFUSING to cut" in capsys.readouterr().err
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in calls)


def test_go_cli_no_tags_errors(monkeypatch, tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("name: release\n")
    (tmp_path / "go.mod").write_text("module example.com/foo\n")
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "describe"]:
            return _completed(cmd, returncode=128, stdout="", stderr="no tags")
        return _completed(cmd, returncode=0, stdout="")

    monkeypatch.setattr(proc, "out", lambda cmd, **kw: str(tmp_path))
    monkeypatch.setattr(proc, "run", fake_run)
    rc = release_cut.main(["minor"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no git tags found" in err
    assert "explicit version" in err
