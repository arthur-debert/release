"""repos_poke verb — one-command fresh-event consumer verification (#595).

Fully offline: every gh/git boundary (clone, commit, push, REST run/job
resolution) is mocked at the gh layer; nothing is cloned or pushed. We assert
on the empty-commit mechanics (default message from release's latest tag,
--reason override, push to the clone's default branch), HEAD-SHA run
resolution (never `gh run rerun`), the classified --watch verdict via the
shared classifier, and the exit-code policy (0 poked/green, 1 watch saw a
failure, 2 setup error, 64 bad usage).
"""

from __future__ import annotations

import subprocess

import pytest
from release_core import gh, proc
from release_core.verbs import repos_poke as rp

REPO = "arthur-debert/padz"
SHA = "f" * 40
LATEST = {"tag_name": "v2.20.0"}

MANIFEST = """\
projects:
  padz:
    - { repo: arthur-debert/padz, path: padz }
  dodot:
    - { repo: arthur-debert/dodot, path: dodot }
"""


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _GitHub:
    """Scripted gh-layer double: records every git/gh call, serves REST by path."""

    def __init__(self, runs=None, jobs=None):
        self.git_calls: list[list[str]] = []
        self.clones: list[tuple[str, str, list[str] | None]] = []
        self.rest_paths: list[str] = []
        self.runs = runs if runs is not None else []
        self.jobs = jobs or {}

    def repo_clone(self, repo, dest, *, git_args=None):
        self.clones.append((repo, dest, git_args))
        return _cp(0)

    def git(self, args, **kw):
        self.git_calls.append(args)
        return ""

    def git_rev_parse(self, ref, *, cwd):
        return SHA

    def git_current_branch(self, *, cwd):
        return "main"

    def rest(self, path, **kw):
        self.rest_paths.append(path)
        if path.endswith("releases/latest"):
            return LATEST
        if "/actions/runs?head_sha=" in path:
            return {"workflow_runs": self.runs}
        if "/jobs?" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return {"jobs": self.jobs.get(run_id, [])}
        raise AssertionError(f"unexpected rest path: {path}")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Fleet registry stubbed, gh auth green, all gh seams scripted.

    The registry is stubbed at the accessor (the blanket proc.run stub below
    would otherwise starve the yq the real accessor shells to); the real
    manifest→pairs read is covered by test_pairs_reads_the_manifest."""
    monkeypatch.setattr(
        rp.managed_repos,
        "pairs",
        lambda: [("arthur-debert/padz", "padz"), ("arthur-debert/dodot", "dodot")],
    )
    monkeypatch.setenv("USER", "tester")

    hub = _GitHub()
    monkeypatch.setattr(gh, "repo_clone", hub.repo_clone)
    monkeypatch.setattr(gh, "git", hub.git)
    monkeypatch.setattr(gh, "git_rev_parse", hub.git_rev_parse)
    monkeypatch.setattr(gh, "git_current_branch", hub.git_current_branch)
    monkeypatch.setattr(gh, "rest", hub.rest)
    # The only proc.run left at this layer is the `gh auth status` preflight.
    monkeypatch.setattr(proc, "run", lambda cmd, **kw: _cp(0))
    hub.root = str(tmp_path / "hermetic")
    return hub


def _run(hub, *argv):
    return rp.main([REPO, "--root", hub.root, *argv])


def _green_run(run_id=7):
    return {
        "id": run_id,
        "name": "ci",
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://example.test/runs/{run_id}",
    }


# ── usage / setup errors ──────────────────────────────────────────────────────


def test_help_exits_zero(capsys):
    assert rp.main(["--help"]) == 0
    assert "repos poke" in capsys.readouterr().out


def test_missing_repo_is_usage_error(capsys):
    assert rp.main([]) == 64
    assert "a repo (owner/name) is required" in capsys.readouterr().err


def test_unknown_flag_is_usage_error(capsys):
    assert rp.main([REPO, "--bogus"]) == 64
    assert "unknown arg: --bogus" in capsys.readouterr().err


def test_two_repos_is_usage_error(capsys):
    assert rp.main([REPO, "other/repo"]) == 64
    assert "one repo only" in capsys.readouterr().err


def test_non_positive_timeout_is_usage_error(capsys):
    assert rp.main([REPO, "--timeout", "0"]) == 64
    assert "positive" in capsys.readouterr().err


def test_unmanaged_repo_exits_2_listing_the_fleet(env, capsys):
    rc = rp.main(["o/stranger", "--root", env.root])
    assert rc == 2
    err = capsys.readouterr().err
    assert "o/stranger is not in managed-repos.yaml" in err
    assert "arthur-debert/dodot" in err


def test_failed_gh_auth_exits_2(env, monkeypatch, capsys):
    monkeypatch.setattr(proc, "run", lambda cmd, **kw: _cp(1))
    assert _run(env) == 2
    assert "gh auth status" in capsys.readouterr().err


def test_failed_clone_exits_2(env, monkeypatch, capsys):
    monkeypatch.setattr(gh, "repo_clone", lambda repo, dest, *, git_args=None: _cp(1))
    assert _run(env) == 2
    assert "shallow clone" in capsys.readouterr().err


# ── the poke mechanics ────────────────────────────────────────────────────────


def test_poke_shallow_clones_under_the_poke_namespace(env):
    env.runs = [_green_run()]
    assert _run(env) == 0
    (repo, dest, git_args) = env.clones[0]
    assert repo == REPO
    assert dest == f"{env.root}/poke/padz"  # never the verify sweep's <root>/<path> clone
    assert git_args == ["--depth", "1"]


def test_poke_commits_empty_with_the_default_release_message_and_pushes_main(env):
    env.runs = [_green_run()]
    assert _run(env) == 0
    commit = next(c for c in env.git_calls if "commit" in c)
    assert "--allow-empty" in commit
    assert "ci: fresh-event verify of release v2.20.0" in commit
    push = next(c for c in env.git_calls if "push" in c)
    assert push[-2:] == ["origin", "main"]


def test_reason_overrides_the_commit_message(env):
    env.runs = [_green_run()]
    assert _run(env, "--reason", "ci: re-resolve @v2 after the hotfix") == 0
    commit = next(c for c in env.git_calls if "commit" in c)
    assert "ci: re-resolve @v2 after the hotfix" in commit
    # No --reason → no latest-release lookup needed either way; with one, the
    # default-message REST call must not happen.
    assert not any(p.endswith("releases/latest") for p in env.rest_paths)


def test_poke_resolves_runs_by_head_sha_never_rerun(env, capsys):
    env.runs = [_green_run()]
    assert _run(env) == 0
    resolve = [p for p in env.rest_paths if "/actions/runs?head_sha=" in p]
    assert resolve and all(f"head_sha={SHA}" in p for p in resolve)
    # The whole point of the verb: no `gh run rerun` anywhere near it.
    assert not any("rerun" in p for p in env.rest_paths)
    err = capsys.readouterr().err
    assert "triggered ci: https://example.test/runs/7" in err


def test_poke_without_watch_reports_dispatch_and_exits_zero(env, capsys):
    env.runs = [_green_run()]
    assert _run(env) == 0
    out = capsys.readouterr().out
    assert "--watch" in out  # points at the classified-verdict follow-up
    # No job collection without --watch.
    assert not any("/jobs?" in p for p in env.rest_paths)


def test_resolve_timeout_exits_2(env, monkeypatch, capsys):
    env.runs = []  # the push never registers a run
    monkeypatch.setattr(rp, "RESOLVE_POLL_S", 0.0)
    assert _run(env, "--timeout", "0.0001") == 2
    assert "timed out waiting" in capsys.readouterr().err


# ── --watch: the classified verdict ──────────────────────────────────────────


def test_watch_green_run_exits_zero_with_pass_verdict(env, capsys):
    env.runs = [_green_run()]
    env.jobs = {7: [{"name": "ci / check", "status": "completed", "conclusion": "success"}]}
    assert _run(env, "--watch") == 0
    out = capsys.readouterr().out
    assert f"verdict: PASS — fresh event on {REPO} is green." in out


def test_watch_failing_job_is_classified_and_exits_one(env, capsys):
    run = dict(_green_run(), conclusion="failure")
    env.runs = [run]
    env.jobs = {
        7: [
            {
                "name": "ci / check",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {"name": "Materialize the managed tree", "conclusion": "failure"},
                ],
            }
        ]
    }
    assert _run(env, "--watch") == 1
    out = capsys.readouterr().out
    assert "INFRA" in out
    assert "Materialize the managed tree" in out
    assert "verdict: FAIL — 1 INFRA failure (release/upstream)." in out


def test_watch_consumer_side_failure_classifies_project(env, capsys):
    run = dict(_green_run(), conclusion="failure")
    env.runs = [run]
    env.jobs = {
        7: [
            {
                "name": "ci / test",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {"name": "Arm the gate toolset", "conclusion": "success"},
                    {"name": "cargo nextest", "conclusion": "failure"},
                ],
            }
        ]
    }
    assert _run(env, "--watch") == 1
    out = capsys.readouterr().out
    assert "PROJECT" in out
    assert "verdict: FAIL — 1 PROJECT failure (consumer-side)." in out


def test_watch_trusts_run_conclusion_when_no_job_failed(env, capsys):
    # Startup failure: the run concludes failure with zero failed jobs visible.
    run = dict(_green_run(), conclusion="failure")
    env.runs = [run]
    env.jobs = {7: [{"name": "ci / check", "status": "completed", "conclusion": "success"}]}
    assert _run(env, "--watch") == 1
    assert "verdict: FAIL" in capsys.readouterr().out


def test_watch_json_payload_shape(env, capsys):
    import json as jsonlib

    env.runs = [_green_run()]
    env.jobs = {7: [{"name": "ci / check", "status": "completed", "conclusion": "success"}]}
    assert _run(env, "--watch", "--json") == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["repo"] == REPO
    assert payload["sha"] == SHA
    assert payload["verdict"] == "PASS"
    assert payload["runs"][0]["workflow"] == "ci"
    assert payload["jobs"][0]["scope"] == REPO


# ── _watch_runs re-listing (late-registering workflows) ──────────────────────


def test_watch_runs_picks_up_a_late_registering_workflow(monkeypatch):
    # Re-list by HEAD SHA each cycle: a second workflow that registers late
    # still joins the verdict.
    snapshots = [
        [{"id": 1, "name": "ci", "status": "in_progress"}],
        [
            {"id": 1, "name": "ci", "status": "completed", "conclusion": "success"},
            {"id": 2, "name": "release", "status": "completed", "conclusion": "success"},
        ],
    ]
    calls = {"n": 0}

    def fake_list(repo, sha):
        runs = snapshots[min(calls["n"], len(snapshots) - 1)]
        calls["n"] += 1
        return runs

    monkeypatch.setattr(rp, "_list_runs", fake_list)
    runs = rp._watch_runs(REPO, SHA, deadline=float("inf"), sleep=lambda _s: None)
    assert {r["id"] for r in runs} == {1, 2}


# ── the registry accessor ─────────────────────────────────────────────────────


def test_pairs_reads_the_manifest(tmp_path, monkeypatch):
    # The real accessor (the env fixture stubs it): poke resolves repos through
    # the same `projects:` set the other `admin repos` verbs sweep.
    from release_core.verbs import managed_repos as mr

    manifest = tmp_path / "managed-repos.yaml"
    manifest.write_text(MANIFEST)
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(manifest))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    assert mr.pairs() == [("arthur-debert/padz", "padz"), ("arthur-debert/dodot", "dodot")]


# ── CLI registration ──────────────────────────────────────────────────────────


def test_admin_repos_poke_registered():
    from release_core.cli import admin

    assert "poke" in admin.repos.group.commands
