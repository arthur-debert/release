"""orc propagate — run release-sync across multiple consumer repos.

Per-repo flow:

    pre-flight:   working tree clean + on base branch
    git pull --ff-only base
    release-sync --check          # canonical "is there drift?" probe
        exit 0  → skip; no branch created; report "no-changes"
        exit 1  → drift detected; proceed
        other   → fail loud
    git checkout -b <branch>
    release-sync                  # real sync (env: RELEASE_HOME, RELEASE_REF)
    git add -A && git commit
    if --dry-run: revert to base + drop branch; report "dry-run"
    else:
        git push + gh pr create + report "ok" with PR URL
        (on gh failure: checkout back to base; pushed branch left intact
         on origin for manual recovery)

The `--check`-first design matters: `.release-sync-state.yaml`'s
`synced_at` timestamp updates on every sync write. A naive "run sync,
then `git status`" would always show drift even when content matches.
`--check` preserves the timestamp during comparison, giving an
accurate signal.

Per-repo failures are captured and reported; a failure in one repo
does not abort the rest. On any error the local working tree is
restored to base_branch.

Mechanical git/gh operations only — no Claude Agent SDK involvement.
The Agent SDK is what powers `orc probe` (verification-by-proxy);
propagate is mechanical and doesn't need LLM intelligence.
"""

import os
import shlex
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

    # Pre-flight: must be a git work tree, not a bare repo / non-checkout
    # state / non-repo at all. `git rev-parse --is-inside-work-tree`
    # subsumes a basic "is this a git repo?" check — it exits non-zero
    # for non-repos and prints "false" for bare-repo CWDs. Empirical:
    # ~/h/padz is configured as a bare repo (`core.bare=true`) even
    # though files live in the directory, and `git status` there
    # exit-128s with "this operation must be run in a work tree."
    is_wt = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if is_wt.returncode != 0 or is_wt.stdout.strip() != "true":
        quoted = shlex.quote(str(repo_path))
        quoted_base = shlex.quote(base_branch)
        detail = (is_wt.stderr or is_wt.stdout or "").strip()
        suffix = f" ({detail})" if detail else ""
        return PropagateResult(
            repo_str, "error",
            error=(
                f"not a git work tree (bare repo or non-checkout state){suffix}. "
                "If this is a bare repo, set up a linked worktree on the base "
                f"branch first: `git -C {quoted} worktree add <path> "
                f"{quoted_base}`, then propagate against that path."
            ),
        )

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

    # Pull base — ff-only so an accidentally-diverged local doesn't get
    # a silent merge commit dropped on it.
    try:
        _run(["git", "pull", "--ff-only", "--quiet"], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        return PropagateResult(
            repo_str, "error",
            error=f"pull --ff-only failed: {e.stderr or e.stdout or e}",
        )

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

    # Refuse to clobber an existing local branch. The caller is expected
    # to pick a unique-per-ref branch name (cli.py appends the ref's
    # short SHA by default), so collision means a prior partial run left
    # state behind. Fail loud rather than silently destroying that work.
    existing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if existing.returncode == 0:
        return PropagateResult(
            repo_str, "error",
            error=(
                f"branch '{branch}' already exists locally — delete it "
                f"(`git -C {repo_path} branch -D {branch}`) or rerun with "
                "--branch <unique-name>."
            ),
        )

    try:
        _run(["git", "checkout", "-b", branch], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        return PropagateResult(
            repo_str, "error",
            error=f"branch create failed: {e.stderr or e.stdout or e}",
        )

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
            repo_str, "error",
            error=f"release-sync failed: {e.stderr or e.stdout or e}",
        )

    try:
        _run(["git", "add", "-A"], cwd=repo_path)
        _run(["git", "commit", "-q", "-m", commit_msg], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        _restore()
        return PropagateResult(
            repo_str, "error",
            error=f"commit failed: {e.stderr or e.stdout or e}",
        )

    if dry_run:
        _restore()
        return PropagateResult(repo_str, "dry-run", branch=branch)

    try:
        _run(["git", "push", "-u", "origin", branch], cwd=repo_path)
    except subprocess.CalledProcessError as e:
        _restore()
        return PropagateResult(
            repo_str, "error",
            error=f"push failed: {e.stderr or e.stdout or e}",
        )

    try:
        pr_out = _run(
            [
                "gh", "pr", "create",
                "--base", base_branch,
                "--title", pr_title,
                "--body", pr_body,
            ],
            cwd=repo_path,
        ).stdout.strip()
        # gh prints status warnings on stdout first; PR URL is the last line.
        pr_url = pr_out.splitlines()[-1]
    except subprocess.CalledProcessError as e:
        # Push succeeded but PR failed — leave the pushed branch in place
        # on origin for manual recovery, but checkout the local working
        # tree back to base so subsequent runs (and the user's shell)
        # land in a predictable state.
        try:
            _run(["git", "checkout", base_branch], cwd=repo_path)
        except subprocess.CalledProcessError:
            pass
        return PropagateResult(
            repo_str, "error", branch=branch,
            error=f"pr create failed: {e.stderr or e.stdout or e}",
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
    # Wrap each propagate_one in a catch-all: propagate_one returns
    # PropagateResult(status="error", ...) for known subprocess failures,
    # but uncaught surprises (FileNotFoundError if `git`/`gh` is missing,
    # CalledProcessError from one of the pre-flight `_run`s, ...) would
    # otherwise abort the whole batch. The whole point of fan-out is
    # that one repo's failure can't take out the others.
    results: list[PropagateResult] = []
    for p in repo_paths:
        try:
            results.append(
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
            )
        except Exception as e:  # noqa: BLE001 — intentional broad catch
            results.append(
                PropagateResult(str(p), "error", error=f"unexpected: {e!r}")
            )
    return results


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
