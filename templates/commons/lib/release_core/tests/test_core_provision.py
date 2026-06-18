"""provision — the init-side dev-env provisioning (WS5/E, #762).

Covers:
  * init wires --cloud / --no-provision through to provision.run (the flag contract);
  * provision.run's ORDER (toolset FIRST, post-setup hook LAST) + the --cloud gate;
  * idempotency: a second run does not dirty the tree / is a no-op on a steady repo;
  * each step is best-effort (a failing step warns + continues, never raises).
"""

from __future__ import annotations

import os
import subprocess

from release_core import provision
from release_core.verbs import init

# ── init → provision.run flag wiring ─────────────────────────────────────────


def _stub_full_sync(monkeypatch, repo):
    """Stub the managed-tree install so the test isolates the provisioning wiring.
    Returns (changes>0, no managed paths, ref, no conflicts) so the success path
    (which calls provision) runs without a real bundle/clone."""
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_run_full_sync", lambda *a, **k: (1, [], "test-ref", []))
    # auto-commit is a no-op here (no managed paths); silence it regardless.
    monkeypatch.setattr(init, "_auto_commit", lambda *a, **k: None)


def test_init_runs_provision_by_default(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_full_sync(monkeypatch, repo)
    seen = {}

    def _capture(root, *, cloud):
        seen.update(root=root, cloud=cloud)

    monkeypatch.setattr(provision, "run", _capture)
    assert init.main([]) == 0
    assert seen == {"root": str(repo), "cloud": False}


def test_init_cloud_flag_forwarded(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_full_sync(monkeypatch, repo)
    seen = {}

    def _capture(root, *, cloud):
        seen.update(cloud=cloud)

    monkeypatch.setattr(provision, "run", _capture)
    assert init.main(["--cloud"]) == 0
    assert seen["cloud"] is True


def test_init_no_provision_skips(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_full_sync(monkeypatch, repo)
    called = {"ran": False}
    monkeypatch.setattr(provision, "run", lambda *a, **k: called.update(ran=True))
    assert init.main(["--no-provision"]) == 0
    assert called["ran"] is False


def test_init_dry_run_never_provisions(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(init.gh, "repo_root", lambda: str(repo))
    monkeypatch.setattr(init, "_run_full_sync", lambda *a, **k: (1, [], "test-ref", []))
    called = {"ran": False}
    monkeypatch.setattr(provision, "run", lambda *a, **k: called.update(ran=True))
    assert init.main(["--dry-run"]) == 0
    assert called["ran"] is False


# ── provision.run ORDER + --cloud gate ───────────────────────────────────────


def _trace_steps(monkeypatch):
    order: list[str] = []
    for name in (
        "arm_toolset",
        "init_submodules",
        "wire_hook",
        "fetch_tags",
        "dep_caches",
        "import_nss_cert",
        "post_setup_hook",
    ):
        monkeypatch.setattr(provision, name, (lambda n: lambda root: order.append(n))(name))
    return order


def test_run_order_toolset_first_hook_last_local(tmp_path, monkeypatch):
    order = _trace_steps(monkeypatch)
    provision.run(str(tmp_path), cloud=False)
    # Toolset FIRST (the gate + hook need it); post-setup hook LAST. Cloud steps
    # are SKIPPED when cloud=False.
    assert order[0] == "arm_toolset"
    assert order[-1] == "post_setup_hook"
    assert "fetch_tags" not in order
    assert "dep_caches" not in order
    assert "import_nss_cert" not in order
    assert order == ["arm_toolset", "init_submodules", "wire_hook", "post_setup_hook"]


def test_run_cloud_adds_cloud_steps(tmp_path, monkeypatch):
    order = _trace_steps(monkeypatch)
    provision.run(str(tmp_path), cloud=True)
    assert order == [
        "arm_toolset",
        "init_submodules",
        "wire_hook",
        "fetch_tags",
        "dep_caches",
        "import_nss_cert",
        "post_setup_hook",
    ]


def test_run_never_raises_when_a_step_fails(tmp_path, monkeypatch):
    def boom(root):
        raise RuntimeError("registry down")

    # Make EVERY step raise (an unexpected exception, not the steps' own internal
    # best-effort warn) and prove run() still returns without propagating — the
    # load-bearing contract: init must never break the boot over a provisioning
    # hiccup, even from a future step that forgets to swallow its own errors.
    for step in (
        "arm_toolset",
        "init_submodules",
        "wire_hook",
        "fetch_tags",
        "dep_caches",
        "import_nss_cert",
        "post_setup_hook",
    ):
        monkeypatch.setattr(provision, step, boom)
    # cloud=True so the cloud-only steps are reached too; must not raise.
    provision.run(str(tmp_path), cloud=True)


# ── arm_toolset is best-effort (never raises out of init) ────────────────────


def test_arm_toolset_swallows_provisioner_error(tmp_path, monkeypatch):
    from release_core import toolset

    def boom(*, best_effort, bin_dir=None):
        raise toolset.ProvisionError("npm missing")

    monkeypatch.setattr(toolset, "provision", boom)
    # Must NOT raise — init must never break the boot over a provisioning hiccup.
    provision.arm_toolset(str(tmp_path))


def test_arm_toolset_uses_best_effort(tmp_path, monkeypatch):
    from release_core import toolset

    seen = {}

    def _capture(*, best_effort, bin_dir=None):
        seen.update(be=best_effort)

    monkeypatch.setattr(toolset, "provision", _capture)
    provision.arm_toolset(str(tmp_path))
    assert seen["be"] is True


# ── individual steps: idempotent no-ops on a bare repo ───────────────────────


def _git_init(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def test_init_submodules_noop_without_gitmodules(tmp_path):
    _git_init(tmp_path)
    # No .gitmodules → clean no-op, no raise.
    provision.init_submodules(str(tmp_path))


def test_post_setup_hook_runs_and_is_repeatable(tmp_path):
    appdir = tmp_path / "app-bin"
    appdir.mkdir()
    marker = tmp_path / "ran.txt"
    hook = appdir / "post-setup-hook.sh"
    hook.write_text(f"#!/bin/sh\necho ran >> '{marker}'\n")
    hook.chmod(0o755)
    provision.post_setup_hook(str(tmp_path))
    provision.post_setup_hook(str(tmp_path))
    # Ran twice (it's a hook, re-run each session) — the point is it doesn't error.
    assert marker.read_text().count("ran") == 2


def test_post_setup_hook_warns_when_not_executable(tmp_path, capsys):
    appdir = tmp_path / "app-bin"
    appdir.mkdir()
    hook = appdir / "post-setup-hook.sh"
    hook.write_text("#!/bin/sh\ntrue\n")
    hook.chmod(0o644)  # not executable
    provision.post_setup_hook(str(tmp_path))
    assert "not executable" in capsys.readouterr().err


def test_wire_hook_app_bin_pre_commit_symlinked(tmp_path, monkeypatch):
    _git_init(tmp_path)
    appdir = tmp_path / "app-bin"
    appdir.mkdir()
    pre = appdir / "pre-commit"
    pre.write_text("#!/bin/sh\ntrue\n")
    pre.chmod(0o755)
    # Force the fall-through to the app-bin/pre-commit branch: no release-core, no
    # lefthook binary, no .release/lefthook.yml, no root lefthook.yml. (release-core
    # and lefthook may both be on the dev box's PATH, so stub them out.)
    monkeypatch.setattr(provision, "_have", lambda cmd: False)
    monkeypatch.setattr(provision, "_resolve_lefthook", lambda root: None)
    provision.wire_hook(str(tmp_path))
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.is_symlink()
    assert os.path.realpath(hook) == str(pre)


def test_wire_hook_prefers_release_core_install_hook(tmp_path, monkeypatch):
    _git_init(tmp_path)
    # A migrated consumer: .release/lefthook.yml present + release-core available →
    # wire via `release-core gate --install-hook` (not the app-bin branch).
    rel = tmp_path / ".release"
    rel.mkdir()
    (rel / "lefthook.yml").write_text("colors: false\n")
    monkeypatch.setattr(provision, "_have", lambda cmd: cmd == "release-core")
    calls = []
    monkeypatch.setattr(provision, "_run", lambda cmd, *, cwd, **k: calls.append(cmd) or 0)
    provision.wire_hook(str(tmp_path))
    assert calls == [["release-core", "gate", "--install-hook"]]
