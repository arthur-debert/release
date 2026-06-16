"""Unit tests for orchestrator.boot.boot_clone (release#578).

No network, no Claude Agent SDK, no real install-release-core: each test
builds a tmp git repo carrying a FAKE bin/setup-dev-env.sh that writes
(or deliberately omits) the two boot-assert artifacts:

  (a) .release/.release-sync-source — provenance marker with the source sha
  (b) the WS7 managed-mirrors sentinel line in git info/exclude
"""

import subprocess
from pathlib import Path

import pytest
from orchestrator.boot import (
    BOOT_SCRIPT,
    EXCLUDE_SENTINEL,
    SOURCE_MARKER,
    BootError,
    boot_clone,
)

FAKE_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=test@test", "-c", "user.name=test", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("consumer clone fixture\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def install_fake_boot_script(
    repo: Path,
    *,
    write_marker: bool = True,
    write_sentinel: bool = True,
    exit_code: int = 0,
    extra: str = "",
) -> None:
    """Drop a fake bin/setup-dev-env.sh that fabricates the boot artifacts."""
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", 'echo "fake boot ran"']
    if write_marker:
        lines += [
            "mkdir -p .release",
            f"printf '# release provenance — fixture\\n{FAKE_SHA}\\n' > {SOURCE_MARKER}",
        ]
    if write_sentinel:
        lines += [
            'exclude="$(git rev-parse --git-path info/exclude)"',
            'mkdir -p "$(dirname "$exclude")"',
            f"printf '%s\\n' '{EXCLUDE_SENTINEL}' >> \"$exclude\"",
        ]
    if extra:
        lines.append(extra)
    lines.append(f"exit {exit_code}")
    script = repo / BOOT_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)


def test_success_path_returns_report_with_parsed_sha(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(repo)
    report = boot_clone(str(repo))
    assert report.source_ref == FAKE_SHA
    assert report.repo_path == str(repo.resolve())
    # The fake boot made no commit: a converged clone is success, not failure.
    assert report.sync_commit is None


def test_boot_created_sync_commit_is_recorded_informationally(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(
        repo,
        extra=(
            "git add -A && "
            "git -c user.email=t@t -c user.name=t commit -q -m "
            "'chore(release): sync managed tree'"
        ),
    )
    report = boot_clone(str(repo))
    assert report.sync_commit == "chore(release): sync managed tree"


def test_sync_commit_found_even_when_not_head(tmp_path):
    # A post-setup hook may commit AFTER init's sync commit; the report must
    # still find the sync commit anywhere in the boot-created range.
    repo = make_repo(tmp_path)
    install_fake_boot_script(
        repo,
        extra=(
            "git add -A && "
            "git -c user.email=t@t -c user.name=t commit -q -m "
            "'chore(release): sync managed tree from fixture' && "
            "touch hook-artifact && git add hook-artifact && "
            "git -c user.email=t@t -c user.name=t commit -q -m 'chore: post-setup hook'"
        ),
    )
    report = boot_clone(str(repo))
    assert report.sync_commit == "chore(release): sync managed tree from fixture"


def test_boot_created_non_sync_commit_is_not_reported_as_sync(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(
        repo,
        extra=(
            "git add -A && "
            "git -c user.email=t@t -c user.name=t commit -q -m 'docs: unrelated commit'"
        ),
    )
    report = boot_clone(str(repo))
    assert report.sync_commit is None


def test_non_git_dir_fails_as_boot_error(tmp_path):
    # Not a git worktree: git failures must surface as BootError (cmd_probe
    # only catches BootError), not a raw CalledProcessError stack trace.
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    install_fake_boot_script(repo)
    with pytest.raises(BootError, match="git rev-parse"):
        boot_clone(str(repo))


def test_missing_boot_script_fails_loudly(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(BootError, match="setup-dev-env.sh not found"):
        boot_clone(str(repo))


def test_nonzero_exit_fails_loudly(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(repo, exit_code=3)
    with pytest.raises(BootError, match="exited 3"):
        boot_clone(str(repo))


def test_missing_provenance_marker_names_assert_a(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(repo, write_marker=False)
    with pytest.raises(BootError, match=r"boot-assert \(a\)"):
        boot_clone(str(repo))


def test_marker_with_only_comments_names_assert_a(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(
        repo,
        write_marker=False,
        extra=f"mkdir -p .release && printf '# comments only\\n' > {SOURCE_MARKER}",
    )
    with pytest.raises(BootError, match=r"boot-assert \(a\)"):
        boot_clone(str(repo))


def test_unreadable_marker_fails_as_boot_error(tmp_path):
    # A non-UTF8 marker must surface as BootError (the module's only failure
    # mode), not a raw UnicodeDecodeError stack trace.
    repo = make_repo(tmp_path)
    install_fake_boot_script(
        repo,
        write_marker=False,
        extra=f"mkdir -p .release && printf '\\xff\\xfe\\x00\\xff' > {SOURCE_MARKER}",
    )
    with pytest.raises(BootError, match="could not read provenance marker"):
        boot_clone(str(repo))


def test_missing_exclude_sentinel_names_assert_b(tmp_path):
    repo = make_repo(tmp_path)
    install_fake_boot_script(repo, write_sentinel=False)
    with pytest.raises(BootError, match=r"boot-assert \(b\)"):
        boot_clone(str(repo))
