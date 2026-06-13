"""repos poke — one-command fresh-event consumer verification (#595, epic #583).

Acting on the rerun-vs-fresh-event trap used to be a five-step hand ritual:
throwaway clone (never ~/h), empty commit, push through the ruleset-bypass
noise, find the new run id, watch it, classify the result by failing step.
This verb is that ritual as ONE command. It NEVER uses `gh run rerun`: a rerun
re-executes the run's ORIGINAL reusable-workflow snapshot (refs are resolved
when the run is created), so after a cut advances `@vN` a rerun still
exercises the pre-advance release and proves nothing — the trap this verb
exists to kill.

Usage:
  release-core admin repos poke <owner/name>
      [--reason "..."]   the empty commit's message (default:
                         `ci: fresh-event verify of release <latest tag>`)
      [--watch]          block until every triggered run concludes and print
                         the classified per-job report
      [--root DIR]       hermetic workdir (default /tmp/release-fleet-verify-$USER;
                         poke clones under <root>/poke/, never your ~/h checkouts)
      [--timeout MIN]    resolve/watch budget in minutes (default 30)
      [--json]           machine-readable report

It: resolves <owner/name> through managed-repos.yaml (the fleet registry, same
as every `admin repos` verb); shallow-clones the consumer into the hermetic
root; pushes an EMPTY commit to its default branch recording why; resolves the
run(s) that push triggered by HEAD SHA; and — with --watch — polls them to
conclusion and reports the classified verdict by failing step via the shared
classifier (release_core.classify): INFRA = release/upstream (arm-gate
build/provision, boot, init); PROJECT = consumer-side (build/test/deps).

Exit codes:
  0  — poked (and, with --watch, every triggered run green)
  1  — --watch: at least one run failed (see the classified report)
  2  — setup error (registry/auth/clone/push/resolve/timeout)
  64 — bad usage
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

from .. import classify, gh, proc
from . import managed_repos

RELEASE_REPO = "arthur-debert/release"
PREFIX = "repos poke"

POLL_INITIAL_S = 15.0
POLL_MAX_S = 60.0
POLL_BACKOFF = 1.5
RESOLVE_POLL_S = 5.0

USAGE = __doc__ or ""


class PokeError(RuntimeError):
    """A setup-phase failure (registry/auth/clone/push/resolve/timeout) → exit 2."""


def _usage_error(msg: str) -> int:
    print(f"release-core admin repos poke: {msg}", file=sys.stderr)
    print(USAGE.strip("\n"), file=sys.stderr)
    return 64


def _parse_args(argv: list[str]) -> dict | int:
    user = os.environ.get("USER") or "shared"
    opts: dict = {
        "repo": None,
        "reason": None,
        "watch": False,
        "root": f"/tmp/release-fleet-verify-{user}",  # noqa: S108 — per-user, verify's convention
        "timeout_min": 30.0,
        "json": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(USAGE.strip("\n"))
            return 0
        if arg in ("--reason", "--root", "--timeout"):
            if i + 1 >= len(argv):
                return _usage_error(f"{arg} needs a value")
            value = argv[i + 1]
            i += 2
            if arg == "--reason":
                opts["reason"] = value
            elif arg == "--root":
                opts["root"] = value
            else:
                try:
                    opts["timeout_min"] = float(value)
                except ValueError:
                    return _usage_error(f"--timeout wants minutes, got {value!r}")
                if opts["timeout_min"] <= 0:
                    return _usage_error(f"--timeout wants a positive number, got {value!r}")
        elif arg == "--watch":
            opts["watch"] = True
            i += 1
        elif arg == "--json":
            opts["json"] = True
            i += 1
        elif arg.startswith("-"):
            return _usage_error(f"unknown arg: {arg}")
        elif opts["repo"] is None:
            opts["repo"] = arg
            i += 1
        else:
            return _usage_error(f"one repo only (got {opts['repo']!r} and {arg!r})")
    if opts["repo"] is None:
        return _usage_error("a repo (owner/name) is required")
    return opts


# ── gh seams ─────────────────────────────────────────────────────────────────


def _default_message() -> str:
    """`ci: fresh-event verify of release <tag>` — <tag> is release's latest
    published release (what the poked consumer's fresh event will resolve)."""
    data = classify.retry(
        lambda: gh.rest(f"repos/{RELEASE_REPO}/releases/latest"),
        what="latest-release lookup",
        prefix=PREFIX,
    )
    tag = (data or {}).get("tag_name")
    if not tag:
        raise PokeError(
            "cannot resolve release's latest tag for the default message — pass --reason"
        )
    return f"ci: fresh-event verify of release {tag}"


def _list_runs(repo: str, sha: str) -> list[dict]:
    """Every workflow run triggered by the poke commit — keyed by HEAD SHA, the
    #587 phase-4 pattern (the SHA is brand-new, so the listing is exactly this
    push's fresh events; NEVER `gh run rerun`)."""
    data = classify.retry(
        lambda: gh.rest(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=50"),
        what="run resolution",
        prefix=PREFIX,
    )
    return (data or {}).get("workflow_runs", [])


def _resolve_runs(repo: str, sha: str, deadline: float, *, sleep=time.sleep) -> list[dict]:
    """Poll until the push's run(s) register (run creation lags the push)."""
    while True:
        runs = _list_runs(repo, sha)
        if runs:
            return runs
        if time.time() >= deadline:
            raise PokeError(f"timed out waiting for {repo} to register a run for {sha[:12]}")
        sleep(RESOLVE_POLL_S)


def _watch_runs(repo: str, sha: str, deadline: float, *, sleep=time.sleep) -> list[dict]:
    """Poll every run for the poke SHA to conclusion (backoff + transient
    retry). Re-lists by HEAD SHA each cycle, so a workflow that registers late
    still joins the verdict."""
    interval = POLL_INITIAL_S
    while True:
        runs = _list_runs(repo, sha)
        pending = [r for r in runs if r.get("status") != "completed"]
        if runs and not pending:
            return runs
        if time.time() >= deadline:
            still = (
                ", ".join(f"{r.get('name') or '?'} ({r.get('html_url', '?')})" for r in pending)
                or "run registration"
            )
            raise PokeError(f"--timeout exceeded waiting for: {still}")
        label = "+".join(sorted({r.get("name") or "?" for r in pending})) or "run registration"
        print(f"{PREFIX}: waiting on {label} (next poll in {interval:.0f}s)", file=sys.stderr)
        sleep(min(interval, max(0.0, deadline - time.time())))
        interval = min(interval * POLL_BACKOFF, POLL_MAX_S)


# ── main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:  # noqa: C901, PLR0912, PLR0915 — linear phase pipeline
    opts = _parse_args(argv)
    if isinstance(opts, int):
        return opts
    repo = opts["repo"]

    try:
        fleet = dict(managed_repos.pairs())
    except Exception as exc:
        print(f"{PREFIX}: cannot read the fleet registry: {exc}", file=sys.stderr)
        return 2
    if repo not in fleet:
        print(
            f"{PREFIX}: {repo} is not in managed-repos.yaml (fleet: {', '.join(sorted(fleet))})",
            file=sys.stderr,
        )
        return 2

    if proc.run(["gh", "auth", "status"], check=False).returncode != 0:
        print(f"{PREFIX}: `gh auth status` failed — authenticate first", file=sys.stderr)
        return 2

    # Namespaced under <root>/poke/ so the always-fresh shallow clone never
    # clobbers the verify sweep's reusable full clones at <root>/<path>.
    dest = os.path.join(opts["root"], "poke", fleet[repo])
    try:
        message = opts["reason"] or _default_message()
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if gh.repo_clone(repo, dest, git_args=["--depth", "1"]).returncode != 0:
            raise PokeError(f"shallow clone of {repo} failed")
        branch = gh.git_current_branch(cwd=dest)
        if branch is None:
            raise PokeError(f"cannot resolve {repo}'s default branch in the clone")
        gh.git(["-C", dest, "commit", "--allow-empty", "-m", message])
        sha = gh.git_rev_parse("HEAD", cwd=dest)
        gh.git(["-C", dest, "push", "--quiet", "origin", branch])
        print(
            f"{PREFIX}: pushed empty commit {sha[:12]} to {repo}@{branch} ({message!r})",
            file=sys.stderr,
        )
        deadline = time.time() + opts["timeout_min"] * 60.0
        runs = _resolve_runs(repo, sha, deadline)
        for run in runs:
            print(
                f"{PREFIX}: triggered {run.get('name') or '?'}: {run.get('html_url', '')}",
                file=sys.stderr,
            )
    except (PokeError, gh.GhError, proc.ProcError, OSError) as exc:
        # GhError/ProcError/OSError too: the git wrappers and the retried gh
        # calls raise them past the bounded retries — every pre-watch failure
        # is a setup error, never an unhandled stack trace.
        print(f"{PREFIX}: {exc}", file=sys.stderr)
        return 2

    payload: dict = {"repo": repo, "branch": branch, "sha": sha, "message": message}

    if not opts["watch"]:
        payload["verdict"] = "DISPATCHED"
        payload["runs"] = [
            {"id": r["id"], "workflow": r.get("name") or "?", "url": r.get("html_url", "")}
            for r in runs
        ]
        if opts["json"]:
            print(json.dumps(payload, indent=2))
        else:
            print(f"{PREFIX}: dispatched — re-run with --watch for the classified verdict")
        return 0

    try:
        done = _watch_runs(repo, sha, deadline)
    except (PokeError, gh.GhError, proc.ProcError) as exc:
        print(f"{PREFIX}: {exc}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    footer: list[str] = []
    payload_runs: list[dict] = []
    any_failure = False
    for run in sorted(done, key=lambda r: r["id"]):
        workflow = run.get("name") or "?"
        run_rows, failed = classify.job_rows(
            repo,
            workflow,
            classify.collect_jobs(repo, run["id"], prefix=PREFIX),
            run.get("conclusion"),
        )
        # A run can fail with zero failed jobs visible (e.g. startup failure);
        # trust the run conclusion as the backstop.
        failed = failed or run.get("conclusion") not in ("success", "skipped")
        rows.extend(run_rows)
        any_failure = any_failure or failed
        footer.append(f"{workflow}: {'FAIL' if failed else 'PASS'} {run.get('html_url', '')}")
        payload_runs.append(
            {
                "id": run["id"],
                "workflow": workflow,
                "url": run.get("html_url", ""),
                "conclusion": run.get("conclusion"),
                "verdict": "FAIL" if failed else "PASS",
            }
        )

    n_infra = sum(1 for r in rows if r["class"] == "INFRA")
    n_project = sum(1 for r in rows if r["class"] == "PROJECT")
    if any_failure:
        parts = []
        if n_infra:
            parts.append(f"{n_infra} INFRA failure{'s' if n_infra > 1 else ''} (release/upstream)")
        if n_project:
            parts.append(
                f"{n_project} PROJECT failure{'s' if n_project > 1 else ''} (consumer-side)"
            )
        detail = ", ".join(parts) or "run-level failure"
        footer.append(f"verdict: FAIL — {detail}.")
        payload["verdict"] = "FAIL"
    else:
        footer.append(f"verdict: PASS — fresh event on {repo} is green.")
        payload["verdict"] = "PASS"
    payload["runs"] = payload_runs
    payload["jobs"] = rows

    if opts["json"]:
        print(json.dumps(payload, indent=2))
    else:
        header = f"{PREFIX}: {repo}@{branch} {sha[:12]} ({message})"
        print(classify.render_report(header, rows, footer))
    return 1 if any_failure else 0
