"""repos_saturation verb — the #569 sunset-condition meter.

Two layers, both offline:

  1. Each predicate against a real on-disk clone fixture (a tmp git repo
     materialized green or red) — green + red per row, the kind-gated N/A path,
     and the resolver-vintage byte compare against a fake RELEASE_HOME.
  2. The matrix: REMOVABLE only when no consumer is RED and at least one is
     GREEN (all-N/A is NOT removable); the text + JSON renderers; and the
     informational exit policy of main() with the clone/paths subprocesses
     mocked at the proc layer.

The condition table is pinned: its task-verb kinds and bootstrap quartet must
stay equal to the sync-engine source of truth, so a future drift fails here.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from release_core import proc, sync
from release_core.verbs import repos_saturation as sat

# ── on-disk clone fixtures ───────────────────────────────────────────────────


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _make_repo(tmp_path, name="clone"):
    """A real git repo so `git ls-files` / `git config` answer truthfully."""
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    return root


def _track(root, rel, body=b"x", *, executable=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    if executable:
        p.chmod(0o755)
    _git(root, "add", "-f", rel)
    _git(root, "commit", "-q", "-m", rel)


def _consumer(root, kind):
    return sat.Consumer(
        repo="o/n",
        path=str(root),
        kind=kind,
        tracked=sat._tracked_files(str(root)),
    )


@pytest.fixture
def ctx(tmp_path):
    """A fake RELEASE_HOME whose templates/commons carries the CURRENT bootstrap
    quartet bytes (used by the resolver-vintage predicate)."""
    home = tmp_path / "release_home"
    for dest in sat.BOOTSTRAP_QUARTET:
        p = home / "templates" / "commons" / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"current-" + dest.encode())
    return sat.Context(release_home=str(home))


# ── item 1: managed task verbs ───────────────────────────────────────────────


def test_task_verbs_green_when_build_present_and_executable(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "bin/build", b"#!/bin/sh\n", executable=True)
    verdict, _ = sat._p_task_verbs(_consumer(root, "electron-app"), ctx)
    assert verdict == sat.GREEN


def test_task_verbs_red_when_build_missing(tmp_path, ctx):
    root = _make_repo(tmp_path)
    verdict, detail = sat._p_task_verbs(_consumer(root, "tauri-app"), ctx)
    assert verdict == sat.RED
    assert "missing" in detail


def test_task_verbs_red_when_build_not_executable(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "bin/build", b"#!/bin/sh\n", executable=False)
    verdict, detail = sat._p_task_verbs(_consumer(root, "vscode-ext"), ctx)
    assert verdict == sat.RED
    assert "executable" in detail


def test_task_verbs_na_on_known_non_task_kind(tmp_path, ctx):
    root = _make_repo(tmp_path)
    verdict, _ = sat._p_task_verbs(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.NA


def test_task_verbs_red_on_unknown_kind(tmp_path, ctx):
    # detect-kind failed → kind '?'. Applicability is indeterminate, so this is
    # RED, NOT N/A — item 1 must not go REMOVABLE on a kind guess.
    root = _make_repo(tmp_path)
    verdict, detail = sat._p_task_verbs(_consumer(root, "?"), ctx)
    assert verdict == sat.RED
    assert "indeterminate" in detail


# ── item 2: pull-model seeded ────────────────────────────────────────────────


def test_pull_seeded_green_when_resolver_tracked(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "bin/install-release-core", executable=True)
    verdict, _ = sat._p_pull_seeded(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.GREEN


def test_pull_seeded_red_when_resolver_absent(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "README.md")
    verdict, _ = sat._p_pull_seeded(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED


def test_pull_seeded_red_when_tracked_indeterminate():
    # git ls-files failed → tracked is None → RED, never a false GREEN/clean read.
    c = sat.Consumer(repo="o/n", path="/nope", kind="rust-cli", tracked=None)
    verdict, detail = sat._p_pull_seeded(c, sat.Context(release_home="/x"))
    assert verdict == sat.RED
    assert "indeterminate" in detail


# ── item 3: resolver vintage (quartet bytes) ─────────────────────────────────


def test_resolver_vintage_green_when_quartet_matches_current(tmp_path, ctx):
    root = _make_repo(tmp_path)
    for dest in sat.BOOTSTRAP_QUARTET:
        _track(root, dest, b"current-" + dest.encode(), executable=True)
    verdict, _ = sat._p_resolver_vintage(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.GREEN


def test_resolver_vintage_red_when_bytes_differ(tmp_path, ctx):
    root = _make_repo(tmp_path)
    for dest in sat.BOOTSTRAP_QUARTET:
        _track(root, dest, b"current-" + dest.encode(), executable=True)
    # Stale one TRACKED file's bytes (re-write after commit; still tracked).
    (root / "bin" / "install-release-core").write_bytes(b"STALE")
    verdict, detail = sat._p_resolver_vintage(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "install-release-core" in detail
    assert "bytes differ" in detail


def test_resolver_vintage_red_when_quartet_file_absent(tmp_path, ctx):
    root = _make_repo(tmp_path)
    # Only one of four tracked → the other three are not tracked → RED.
    _track(root, ".claude/settings.json", b"current-.claude/settings.json")
    verdict, detail = sat._p_resolver_vintage(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "not tracked" in detail


def test_resolver_vintage_red_when_tracked_but_file_gone_from_disk(tmp_path, ctx):
    # A quartet path that IS tracked but whose file was removed from the working
    # tree → the byte read fails → "absent" → RED (the deployed-but-deleted case).
    root = _make_repo(tmp_path)
    for dest in sat.BOOTSTRAP_QUARTET:
        _track(root, dest, b"current-" + dest.encode(), executable=True)
    (root / ".claude" / "settings.json").unlink()  # tracked, but gone from disk
    verdict, detail = sat._p_resolver_vintage(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "absent" in detail


def test_resolver_vintage_red_when_quartet_present_but_untracked(tmp_path, ctx):
    # The quartet bytes match on disk but the files are NOT tracked → a stray
    # untracked file is not a deployed managed copy → RED (not a false GREEN).
    root = _make_repo(tmp_path)
    for dest in sat.BOOTSTRAP_QUARTET:
        p = root / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"current-" + dest.encode())  # on disk, never `git add`ed
    # tracked is the real (empty) set — git ran, nothing committed.
    verdict, detail = sat._p_resolver_vintage(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "not tracked" in detail


def test_resolver_vintage_red_when_tracked_indeterminate(ctx):
    # git ls-files failed → tracked None → can't prove the quartet deployed → RED.
    c = sat.Consumer(repo="o/n", path="/nope", kind="rust-cli", tracked=None)
    verdict, detail = sat._p_resolver_vintage(c, ctx)
    assert verdict == sat.RED
    assert "indeterminate" in detail


# ── item 4: WS4/WS7 untracked ────────────────────────────────────────────────


def test_ws4_ws7_green_when_nothing_legacy_tracked(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "src/main.rs")
    verdict, _ = sat._p_ws4_ws7_complete(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.GREEN


def test_ws4_ws7_red_when_release_dir_tracked(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, ".release/lefthook.yml", b"x")
    verdict, detail = sat._p_ws4_ws7_complete(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert ".release/lefthook.yml" in detail


def test_ws4_ws7_red_when_root_gate_mirror_tracked(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "lefthook.yml", b"x")
    verdict, detail = sat._p_ws4_ws7_complete(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "lefthook.yml" in detail


def test_ws4_ws7_red_when_tracked_indeterminate():
    # git ls-files failed → tracked is None → RED, not a false "nothing tracked".
    c = sat.Consumer(repo="o/n", path="/nope", kind="rust-cli", tracked=None)
    verdict, detail = sat._p_ws4_ws7_complete(c, sat.Context(release_home="/x"))
    assert verdict == sat.RED
    assert "indeterminate" in detail


# ── item 5: husky core.hooksPath ─────────────────────────────────────────────


def test_hooks_path_green_when_unset(tmp_path, ctx):
    root = _make_repo(tmp_path)
    verdict, _ = sat._p_hooks_path_unset(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.GREEN


def test_hooks_path_red_when_set(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _git(root, "config", "--local", "core.hooksPath", ".husky")
    verdict, detail = sat._p_hooks_path_unset(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert ".husky" in detail


def test_hooks_path_red_when_set_empty_value(tmp_path, ctx):
    # A set-but-EMPTY core.hooksPath: `git config --get` exits 0 (key present),
    # so by exit-code semantics the key IS present → RED, NOT a false "unset".
    root = _make_repo(tmp_path)
    _git(root, "config", "--local", "core.hooksPath", "")
    verdict, detail = sat._p_hooks_path_unset(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED
    assert "set" in detail


def test_hooks_path_red_when_config_unreadable(tmp_path, ctx):
    # A plain (non-git) dir: `git config --local --get` exits 128, NOT 1. That is
    # introspection failure, not a real unset → RED, never a false GREEN.
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    c = sat.Consumer(repo="o/n", path=str(plain), kind="rust-cli", tracked=frozenset())
    verdict, detail = sat._p_hooks_path_unset(c, sat.Context(release_home="/x"))
    assert verdict == sat.RED
    assert "indeterminate" in detail


# ── fragment model: CHANGELOG/ ───────────────────────────────────────────────


def test_fragment_model_green_when_changelog_dir_present(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "CHANGELOG/unreleased-x.md", b"- x\n")
    verdict, _ = sat._p_fragment_model(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.GREEN


def test_fragment_model_red_when_no_changelog_dir(tmp_path, ctx):
    root = _make_repo(tmp_path)
    _track(root, "CHANGELOG.md", b"# changelog\n")
    verdict, _ = sat._p_fragment_model(_consumer(root, "rust-cli"), ctx)
    assert verdict == sat.RED


# ── _tracked_files: None (introspection failed) vs empty (clean) ─────────────


def test_tracked_files_empty_set_when_repo_tracks_nothing(tmp_path):
    # A real git repo with no commits: git ls-files succeeds, returns nothing →
    # empty frozenset (introspected, genuinely clean), NOT None.
    root = _make_repo(tmp_path)
    tracked = sat._tracked_files(str(root))
    assert tracked == frozenset()
    assert tracked is not None


def test_tracked_files_none_when_not_a_repo(tmp_path):
    # A plain dir: git ls-files fails → None (could not introspect), DISTINCT
    # from the empty-but-introspected case above.
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert sat._tracked_files(str(plain)) is None


# ── the condition table is pinned to the sync source of truth ────────────────


def test_bootstrap_quartet_matches_sync_engine():
    assert set(sat.BOOTSTRAP_QUARTET) == set(sync.BOOTSTRAP_REAL_FILES)


def test_one_row_per_569_item():
    # All five #569 items + the fragment surface, no more, no fewer.
    items = [c.item for c in sat.CONDITIONS]
    assert items == ["1", "2", "3", "4", "5", "fragment"]


# ── removable() verdict semantics ────────────────────────────────────────────


def test_removable_true_when_all_green():
    row = {"a": (sat.GREEN, ""), "b": (sat.GREEN, "")}
    assert sat.removable(row) is True


def test_removable_true_when_green_and_na_mix():
    row = {"a": (sat.GREEN, ""), "b": (sat.NA, "")}
    assert sat.removable(row) is True


def test_removable_false_when_any_red():
    row = {"a": (sat.GREEN, ""), "b": (sat.RED, "")}
    assert sat.removable(row) is False


def test_removable_false_when_all_na():
    # An all-N/A row is not evidence the surface is unused.
    row = {"a": (sat.NA, ""), "b": (sat.NA, "")}
    assert sat.removable(row) is False


# ── rendering ────────────────────────────────────────────────────────────────


def _two_consumers():
    return [
        sat.Consumer(repo="o/green", path="/x", kind="rust-cli", tracked=frozenset()),
        sat.Consumer(repo="o/red", path="/y", kind="rust-cli", tracked=frozenset()),
    ]


def test_render_matrix_reports_removable_and_blocked():
    consumers = _two_consumers()
    matrix = {
        "task-verbs": {"o/green": (sat.NA, ""), "o/red": (sat.NA, "")},
        "pull-seeded": {"o/green": (sat.GREEN, ""), "o/red": (sat.GREEN, "")},
        "resolver-vintage": {"o/green": (sat.GREEN, ""), "o/red": (sat.RED, "stale")},
        "ws4-ws7": {"o/green": (sat.GREEN, ""), "o/red": (sat.GREEN, "")},
        "hooks-path": {"o/green": (sat.GREEN, ""), "o/red": (sat.GREEN, "")},
        "fragment-model": {"o/green": (sat.GREEN, ""), "o/red": (sat.GREEN, "")},
    }
    out = sat.render_matrix(consumers, matrix)
    # pull-seeded is all-green → REMOVABLE; resolver-vintage has a red → blocked.
    assert "REMOVABLE — see #569 item 2" in out
    assert "blocked  — #569 item 3" in out
    assert "o/red" in out


def test_matrix_json_shape():
    consumers = _two_consumers()
    cell = {"o/green": (sat.GREEN, "g"), "o/red": (sat.GREEN, "g")}
    matrix = {c.id: dict(cell) for c in sat.CONDITIONS}
    payload = json.loads(sat.matrix_json(consumers, matrix))
    assert [c["repo"] for c in payload["consumers"]] == ["o/green", "o/red"]
    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["pull-seeded"]["removable"] is True
    assert by_id["pull-seeded"]["consumers"]["o/green"]["verdict"] == sat.GREEN
    assert by_id["pull-seeded"]["issue_569_item"] == "2"


# ── main(): informational exit + arg handling ────────────────────────────────


class _Driver:
    """Scripts the clone + --paths subprocesses by leading command vector."""

    _LIST = [*sat._SELF_CLI, "admin", "repos", "list"]

    def __init__(self, paths_stdout, clone_rc=0):
        self.paths_stdout = paths_stdout
        self.clone_rc = clone_rc

    def __call__(self, cmd, **kw):
        if cmd[: len(self._LIST)] == self._LIST and "--clone" in cmd:
            return subprocess.CompletedProcess(cmd, self.clone_rc, "", "")
        if cmd[: len(self._LIST)] == self._LIST and "--paths" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.paths_stdout, "")
        # detect-kind / git ls-files etc. on the (nonexistent) fake paths.
        return subprocess.CompletedProcess(cmd, 1, "", "")


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    monkeypatch.setattr(sat.shutil, "which", lambda _t: "/usr/bin/x")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_main_bad_arg_is_64(base_env):
    assert sat.main(["--bogus"]) == 64


def test_main_help_is_0(base_env, capsys):
    assert sat.main(["--help"]) == 0
    assert "#569" in capsys.readouterr().out


def test_main_setup_error_when_no_consumers(base_env, monkeypatch):
    # --paths returns a missing clone → no consumers → exit 2 (meter can't run).
    driver = _Driver("o/n\t/nope\tmissing\n")
    monkeypatch.setattr(proc, "run", driver)
    assert sat.main(["--no-clone"]) == 2


def test_main_informational_exit_zero(base_env, monkeypatch, tmp_path):
    # A real clone so git ls-files answers; measured matrix → exit 0.
    root = _make_repo(tmp_path, name="found")
    _track(root, "bin/install-release-core", executable=True)
    driver = _Driver(f"o/n\t{root}\tfound\n")
    real_run = proc.run  # capture BEFORE patching

    def hybrid(cmd, **kw):
        # The driver scripts only the `admin repos list` subprocesses; the
        # per-clone `git ls-files` reads inside evaluate() must hit the real
        # tool against the real `found` clone.
        if cmd[: len(_Driver._LIST)] == _Driver._LIST:
            return driver(cmd, **kw)
        return real_run(cmd, **kw)

    monkeypatch.setattr(sat.proc, "run", hybrid)
    monkeypatch.setattr(sat, "_detect_kind", lambda _p: "rust-cli")
    assert sat.main(["--no-clone"]) == 0
