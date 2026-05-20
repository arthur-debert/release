"""orc propagate — run release-sync across multiple consumer repos.

Per-repo flow:

    pre-flight: working tree clean + on base branch
    git pull
    git checkout -b <branch>
    release-sync (env: RELEASE_HOME, RELEASE_REF)
    if no changes: drop branch, report "no-changes"
    else:
        git add -A && git commit
        if --dry-run: drop branch, report "dry-run"
        else: git push + gh pr create + report "ok" with PR URL

Per-repo failures are caught and reported; a failure in one repo does
not abort the rest. On any error the script attempts to restore the
repo to base_branch with the work-branch deleted.

Mechanical git/gh operations only — no Claude Agent SDK involvement.
The Agent SDK is what powers `orc probe` (verification-by-proxy);
propagate is mechanical and doesn't need LLM intelligence.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PropagateResult:
    repo: str
    status: str  # "ok" | "no-changes" | "dry-run" | "error"
    branch: str | None = None
    pr_url: str | None = None
    error: str | None = None


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def propagate_one(
    repo_path: Path,
    release_home: Path,
    ref: str,
    branch: str,
    pr_title: str,
    pr_body: str,
    commit_msg: str,
    base_branch: str = "main",
    dry_run: bool = False,
) -> PropagateResult:
    repo_str = str(repo_path)
    if not (repo_path / ".git").exists():
        return PropagateResult(repo_str, "error", error="not a git repo")

    # Pre-flight: clean working tree
    status = _run(["git", "status", "--porcelain"], cwd=repo_path).stdout.strip()
    if status:
        return PropagateResult(
            repo_str, "error", error=f"working tree not clean:\n{status}"
        )

    current = _run(["git", "branch", "--show-current"], cwd=repo_path).stdout.strip()
    if current != base_branch:
        return PropagateResult(
            repo_str, "error", error=f"not on {base_branch} (on {current})"
        )

    # Pull base.
    try:
        _run(["git", "pull", "--quiet"], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        return PropagateResult(repo_str, "error", error=f"pull failed: {e.stderr or e}")

    sync_env = {
        **os.environ,
        "RELEASE_HOME": str(release_home),
        "RELEASE_REF": ref,
    }

    # Cheap pre-flight: release-sync --check is the canonical "is there
    # drift?" probe. Exit 0 means already in sync — skip everything
    # (don't create a branch we'll just delete). --check preserves the
    # state-file timestamp during comparison so the bytes-equal check
    # doesn't false-positive on the time-of-last-sync field.
    check = subprocess.run(
        ["release-sync", "--check"],
        cwd=repo_path,
        env=sync_env,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return PropagateResult(repo_str, "no-changes")
    if check.returncode != 1:
        # 1 = drift detected (expected). Anything else = real failure.
        return PropagateResult(
            repo_str, "error",
            error=f"release-sync --check exited {check.returncode}: {check.stderr or check.stdout}",
        )

    try:
        _run(["git", "checkout", "-b", branch], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        return PropagateResult(repo_str, "error", error=f"branch create failed: {e.stderr or e}")

    # Best-effort restore on any error past this point.
    def _restore() -> None:
        try:
            _run(["git", "checkout", base_branch], cwd=repo_path)
            _run(["git", "branch", "-D", branch], cwd=repo_path)
        except subprocess.CalledProcessError:
            pass

    try:
        _run(["release-sync"], cwd=repo_path, env=sync_env)
    except subprocess.CalledProcessError as e:
        _restore()
        return PropagateResult(
            repo_str, "error", error=f"release-sync failed: {e.stderr or e}"
        )

    try:
        _run(["git", "add", "-A"], cwd=repo_path)
        _run(["git", "commit", "-q", "-m", commit_msg], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        _restore()
        return PropagateResult(
            repo_str, "error", error=f"commit failed: {e.stderr or e}"
        )

    if dry_run:
        _restore()
        return PropagateResult(repo_str, "dry-run", branch=branch)

    try:
        _run(["git", "push", "-u", "origin", branch], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        _restore()
        return PropagateResult(
            repo_str, "error", error=f"push failed: {e.stderr or e}"
        )

    try:
        pr_out = _run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base_branch,
                "--title",
                pr_title,
                "--body",
                pr_body,
            ],
            cwd=repo_path,
        ).stdout.strip()
        # gh prints status warnings on stdout first; the PR URL is the last line.
        pr_url = pr_out.splitlines()[-1]
    except subprocess.CalledProcessError as e:
        # Push succeeded but PR failed — leave the branch in place for manual recovery.
        return PropagateResult(
            repo_str, "error", branch=branch, error=f"pr create failed: {e.stderr or e}"
        )

    return PropagateResult(repo_str, "ok", branch=branch, pr_url=pr_url)


def propagate_many(
    repo_paths: list[Path],
    release_home: Path,
    ref: str,
    branch: str,
    pr_title: str,
    pr_body: str,
    commit_msg: str,
    base_branch: str = "main",
    dry_run: bool = False,
) -> list[PropagateResult]:
    return [
        propagate_one(
            p,
            release_home=release_home,
            ref=ref,
            branch=branch,
            pr_title=pr_title,
            pr_body=pr_body,
            commit_msg=commit_msg,
            base_branch=base_branch,
            dry_run=dry_run,
        )
        for p in repo_paths
    ]


def render_summary(results: list[PropagateResult]) -> str:
    lines = []
    for r in results:
        if r.status == "ok":
            lines.append(f"  ✓ {r.repo}  →  {r.pr_url}")
        elif r.status == "no-changes":
            lines.append(f"  - {r.repo}  (already in sync)")
        elif r.status == "dry-run":
            lines.append(f"  • {r.repo}  (dry-run; would create branch {r.branch})")
        else:
            err = (r.error or "").strip().replace("\n", " | ")
            lines.append(f"  ✗ {r.repo}  ({err})")
    return "\n".join(lines)
