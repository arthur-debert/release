"""release-core admin canary run — one pre-ship canary round against a candidate ref.

Exercises a consumer's FULL life — boot from source, materialize, check, e2e,
and a real prerelease cut — on the registered canary repos, against an
UNRELEASED release revision, before `release-core cut` moves the fleet
(#587, epic #583). Collapses "cut → advance → wait for a consumer to go red"
into one command.

Usage:
  release-core admin canary run --ref <branch|main>
      [--family rust,npm]   restrict to these families (default: all registered)
      [--root DIR]          hermetic workdir (default /tmp/release-canary-$USER)
      [--timeout MIN]       per-family poll budget in minutes (default 40)
      [--keep N]            canary prereleases to retain on cleanup (default 5)
      [--json]              machine-readable report

Per family it: publishes `canary/<sha12>` (a clone of release at the candidate
SHA with every `uses: arthur-debert/release/...@vN` self-ref rewritten to the
canary branch, so the composites AND the wheel resolve at the candidate tree);
seeds the canary repo from source in a sandboxed venv (XDG_* under --root —
never the operator's real release-core); pushes the seed (push event → CI) and
dispatches a `0.0.<n>-canary.<runid>` prerelease cut as FRESH events (never
`gh run rerun`); polls both runs to conclusion with transient-error-tolerant
backoff; prints a per-job classified report (INFRA = release bug, PROJECT =
canary-content rot); posts a `canary/<family>` commit status on release@<sha>
(what the slice-4 cut gate reads); and deletes canary prereleases beyond
--keep. `canary/*` branches on release are KEPT (owner decision OQ5).

Exit codes:
  0  — every job green (or fenced-skip)
  1  — at least one job failed (see the classified report)
  2  — setup error (preflight/clone/seed/dispatch/timeout)
  64 — bad usage
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

from .. import gh, proc
from . import managed_repos

RELEASE_REPO = "arthur-debert/release"
CI_WORKFLOW = "ci.yml"
RELEASE_WORKFLOW = "release.yml"

POLL_INITIAL_S = 15.0
POLL_MAX_S = 60.0
POLL_BACKOFF = 1.5
RESOLVE_POLL_S = 10.0
RETRY_ATTEMPTS = 3  # bounded transient-error retries around gh calls (#582)
RETRY_DELAY_S = 5.0
JOBS_SETTLE_ATTEMPTS = 6  # jobs endpoint lags the run resource — re-poll until settled
JOBS_SETTLE_DELAY_S = 10.0

USAGE = __doc__ or ""


class CanaryError(RuntimeError):
    """A setup-phase failure (preflight/clone/seed/dispatch/poll) → exit 2."""


# ── pure functions (pytest-covered) ──────────────────────────────────────────

# Anchored on `uses: arthur-debert/release/`: only release self-refs are
# rewritten — third-party actions, comment lines (`#` precedes `uses:`), and
# trailing comments are untouched. Matches any current ref (vN, vN.x.y, a SHA,
# an earlier canary/<sha12>), so the rewrite is idempotent across re-runs.
_USES_RE = re.compile(
    r"(?m)^(?P<prefix>\s*(?:-\s+)?uses:\s*arthur-debert/release/[^@\s]+@)(?P<ref>[^\s#]+)"
)


def rewrite_self_refs(text: str, target_ref: str) -> tuple[str, int]:
    """Rewrite every release self-ref's `@<ref>` to `@<target_ref>`.

    Returns (new_text, number_of_refs_rewritten)."""
    return _USES_RE.subn(lambda m: m.group("prefix") + target_ref, text)


_CANARY_TAG_RE = re.compile(r"^v0\.0\.(\d+)-canary\.")


def next_canary_version(tag_names: list[str], runid: str) -> str:
    """The next `0.0.<n>-canary.<runid>` prerelease, from the repo's tags.

    <n> is max over existing `v0.0.<n>-canary.*` tags + 1 (1 when none).
    Non-canary tags never participate."""
    highest = 0
    for name in tag_names:
        match = _CANARY_TAG_RE.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"0.0.{highest + 1}-canary.{runid}"


# Classification is mechanical from job/step names (#587 §3). INFRA = the
# release-owned plumbing (arm-gate materialize/provision, the boot resolver,
# init, prepare-release internals); PROJECT = the canary's own content
# (gate checks, cargo, bats, compilation) — still release-owned, different
# file set (fixture rot, not pipeline rot).
INFRA_MARKERS = (
    "arm the gate",
    "materialize",
    "provision",
    "install-release-core",
    "release-core init",
    "resolution probe",
    "prepare",
)
PROJECT_MARKERS = (
    "bin/check",
    "canonical checks",
    "e2e",
    "bats",
    "cargo",
    "pre-test",
    "build",
    "compile",
    "clippy",
    "test",
)
_MATERIALIZE_MARKERS = ("arm the gate", "materialize")


def classify_failure(failed_step: str, steps: list[dict]) -> str:
    """INFRA vs PROJECT for a failed job, from its step names.

    One refinement beyond the marker tables: a PROJECT-looking step that ran
    WITHOUT a successful materialize step before it failed on an un-armed
    tree — the #579 class (`bin/check-e2e` exit 127 on a sparse post-WS7
    checkout, the managed mirrors never composed). That is release's bug, so
    it classifies INFRA. Unknown failures default to INFRA: everything in a
    canary is release-owned, so unattributed breakage escalates here."""
    name = (failed_step or "").lower()
    if any(marker in name for marker in INFRA_MARKERS):
        return "INFRA"
    if any(marker in name for marker in PROJECT_MARKERS):
        armed = any(
            s.get("conclusion") == "success"
            and any(m in (s.get("name") or "").lower() for m in _MATERIALIZE_MARKERS)
            for s in steps
        )
        return "PROJECT" if armed else "INFRA"
    return "INFRA"


def job_rows(
    family: str, workflow: str, jobs: list[dict], run_conclusion: str | None = None
) -> tuple[list[dict], bool]:
    """Per-job report rows for one completed run. Returns (rows, any_failure).

    Skipped jobs are annotated: `skipped (fenced)` when nothing failed before
    them (a designed fence, e.g. publish-crates: false), `skipped (cascade)`
    when an earlier failure cancelled the chain.

    ``run_conclusion`` is the backstop for an UNSETTLED jobs listing (the
    endpoint lags the run resource): a job still reporting in_progress under a
    run whose conclusion is success is a stale snapshot, not a failure — it is
    annotated, never counted as failed."""
    rows: list[dict] = []
    any_failure = False
    for job in jobs:
        conclusion = job.get("conclusion") or job.get("status") or "?"
        status = job.get("status")
        if status is not None and status != "completed" and run_conclusion == "success":
            rows.append(_row(family, workflow, job, "unsettled (run green)", "-", "-"))
            continue
        if conclusion == "success":
            rows.append(_row(family, workflow, job, "success", "-", "-"))
        elif conclusion == "skipped":
            note = "skipped (cascade)" if any_failure else "skipped (fenced)"
            rows.append(_row(family, workflow, job, note, "-", "-"))
        else:
            any_failure = True
            steps = job.get("steps") or []
            failed_step = next(
                (
                    s.get("name") or ""
                    for s in steps
                    if s.get("conclusion") not in (None, "success", "skipped")
                ),
                "",
            )
            klass = classify_failure(failed_step or job.get("name") or "", steps)
            rows.append(_row(family, workflow, job, conclusion.upper(), klass, failed_step or "-"))
    return rows, any_failure


def _row(family: str, workflow: str, job: dict, conclusion: str, klass: str, step: str) -> dict:
    return {
        "family": family,
        "workflow": workflow,
        "job": job.get("name") or "?",
        "conclusion": conclusion,
        "class": klass,
        "step": step,
        "url": job.get("html_url") or "",
    }


_COLUMNS = ("family", "workflow", "job", "conclusion", "class", "step")
_HEADERS = ("family", "workflow", "job", "conclusion", "class", "step (first failure)")


def render_report(header: str, rows: list[dict], footer_lines: list[str]) -> str:
    """The human report: header line, aligned per-job table, per-family + verdict lines."""
    widths = [len(h) for h in _HEADERS]
    for row in rows:
        for i, col in enumerate(_COLUMNS):
            widths[i] = max(widths[i], len(str(row[col])))
    lines = [header, ""]
    lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(_HEADERS)).rstrip())
    for row in rows:
        lines.append(
            "  ".join(str(row[col]).ljust(widths[i]) for i, col in enumerate(_COLUMNS)).rstrip()
        )
    lines.append("")
    lines.extend(footer_lines)
    return "\n".join(lines)


# ── gh/git seams ─────────────────────────────────────────────────────────────


def _retry(fn, *, what: str, attempts: int = RETRY_ATTEMPTS, sleep=time.sleep):
    """Bounded retry with linear backoff around a gh call (the #582 lesson:
    one transient blip — TLS handshake timeout, a 5xx — must not kill a
    40-minute round). Re-raises the last error once attempts are exhausted."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (gh.GhError, proc.ProcError) as exc:
            last = exc
            if attempt < attempts:
                print(
                    f"canary run: transient {what} error "
                    f"(attempt {attempt}/{attempts}, retrying): {exc}",
                    file=sys.stderr,
                )
                sleep(RETRY_DELAY_S * attempt)
    assert last is not None
    raise last


def _resolve_ref(release_dir: str, ref: str) -> str:
    """`--ref` → full SHA inside the fresh release clone (origin/<ref> first,
    then <ref> for a tag/SHA)."""
    for candidate in (f"origin/{ref}", ref):
        if gh.git_rev_parse_verify(candidate, cwd=release_dir):
            return gh.git_rev_parse(candidate, cwd=release_dir)
    raise CanaryError(f"cannot resolve --ref {ref!r} in the release clone ({release_dir})")


def _inflight_run(repo: str, sha12: str) -> dict | None:
    """An already-running round for this candidate SHA on the canary repo, or
    None. Seed commits stamp `release@<sha12>` into the commit subject, so a
    queued/in-progress run whose display title carries it IS a live round."""
    data = _retry(
        lambda: gh.rest(f"repos/{repo}/actions/runs?per_page=30"),
        what="in-flight run list",
    )
    for run in (data or {}).get("workflow_runs", []):
        live = run.get("status") in ("queued", "in_progress", "waiting", "requested", "pending")
        if live and f"release@{sha12}" in (run.get("display_title") or ""):
            return run
    return None


def _publish_candidate(release_dir: str, sha: str, branch: str, ref: str) -> int:
    """Phase 1: rewrite the self-refs at <sha> and push `canary/<sha12>`.

    Returns the number of refs rewritten. Force-push: the branch is
    canary-owned and a re-run of the same SHA recreates it."""
    sha12 = sha[:12]
    gh.git(["-C", release_dir, "checkout", "--quiet", "--detach", sha])
    rewritten = 0
    for path in sorted(glob.glob(os.path.join(release_dir, ".github", "workflows", "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new_text, count = rewrite_self_refs(text, branch)
        if count:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            rewritten += count
    if rewritten == 0:
        raise CanaryError("no `uses: arthur-debert/release/...` self-refs found — wrong tree?")
    gh.git(["-C", release_dir, "add", ".github/workflows"])
    gh.git(
        [
            "-C",
            release_dir,
            "commit",
            "--quiet",
            "-m",
            f"canary: self-refs @vN -> @{branch} ({ref}@{sha12})",
        ]
    )
    gh.git(["-C", release_dir, "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}"])
    return rewritten


def _sandbox_env(root: str) -> dict[str, str]:
    """A full child env for the sandboxed boot: XDG_DATA_HOME/XDG_BIN_HOME under
    --root and PATH prefixed with the sandbox bin — the operator's real
    release-core venv (~/.local/...) is never touched. RELEASE_HOME/RELEASE_REF
    are STRIPPED so init materializes from the candidate wheel bundle, not a
    maintainer checkout."""
    env = {k: v for k, v in os.environ.items() if k not in ("RELEASE_HOME", "RELEASE_REF")}
    xdg = os.path.join(root, "xdg")
    env["XDG_DATA_HOME"] = os.path.join(xdg, "data")
    env["XDG_BIN_HOME"] = os.path.join(xdg, "bin")
    env["PATH"] = env["XDG_BIN_HOME"] + os.pathsep + env.get("PATH", "")
    return env


def _run_sandboxed(cmd: list[str], *, cwd: str, env: dict[str, str], what: str) -> None:
    res = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, cwd=cwd, env=env, capture_output=True, text=True
    )
    if res.returncode != 0:
        tail = "\n".join((res.stdout + "\n" + res.stderr).strip().splitlines()[-15:])
        raise CanaryError(f"{what} failed ({res.returncode}):\n{tail}")


def _seed_canary(
    *,
    root: str,
    family: str,
    repo: str,
    release_dir: str,
    branch: str,
    sha12: str,
    ref: str,
    runid: str,
) -> str:
    """Phase 2: clone the canary, boot it from the candidate source (sandboxed),
    point its callers at `canary/<sha12>`, add the changelog fragment (satisfies
    the prepare fragment gate AND guarantees a non-empty push), commit the seed.
    Returns the clone dir."""
    dest = os.path.join(root, f"canary-{family}")
    shutil.rmtree(dest, ignore_errors=True)
    if gh.repo_clone(repo, dest).returncode != 0:
        raise CanaryError(f"{family}: clone of {repo} failed")

    env = _sandbox_env(root)
    installer = os.path.join(release_dir, "templates", "commons", "bin", "install-release-core")
    _run_sandboxed(
        ["bash", installer, "--from-source", release_dir, "--no-init"],
        cwd=dest,
        env=env,
        what=f"{family}: sandboxed install-release-core --from-source",
    )
    sandbox_cli = os.path.join(env["XDG_BIN_HOME"], "release-core")
    _run_sandboxed(
        [sandbox_cli, "init"],
        cwd=dest,
        env=env,
        what=f"{family}: release-core init (sandbox, candidate wheel)",
    )

    seed_paths: list[str] = []
    for name in (CI_WORKFLOW, RELEASE_WORKFLOW):
        rel = os.path.join(".github", "workflows", name)
        path = os.path.join(dest, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new_text, count = rewrite_self_refs(text, branch)
        if count == 0:
            raise CanaryError(f"{family}: no release caller ref found in {rel}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        seed_paths.append(rel)

    fragment = os.path.join("CHANGELOG", f"unreleased-canary-{runid}.md")
    os.makedirs(os.path.join(dest, "CHANGELOG"), exist_ok=True)
    with open(os.path.join(dest, fragment), "w", encoding="utf-8") as fh:
        fh.write(f"Canary round {runid}: exercise release@{sha12} ({ref}) pre-ship.\n")
    seed_paths.append(fragment)

    gh.git(["-C", dest, "add", *seed_paths])
    gh.git(["-C", dest, "commit", "--quiet", "-m", f"canary: seed from release@{sha12} ({ref})"])
    return dest


def _release_run_ids(repo: str) -> set[int]:
    data = _retry(
        lambda: gh.rest(f"repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}/runs?per_page=30"),
        what="release-run snapshot",
    )
    return {run["id"] for run in (data or {}).get("workflow_runs", [])}


def _dispatch(repo: str, dest: str, version: str) -> tuple[str, set[int]]:
    """Phase 3: FRESH events only (never `gh run rerun`) — push the seed commit
    (push event → CI) and dispatch the prerelease cut, in parallel. Returns
    (seed_sha, pre-dispatch release-run ids — the skew-proof resolver seam)."""
    before = _release_run_ids(repo)
    gh.git(["-C", dest, "push", "--quiet", "origin", "main"])
    seed_sha = gh.git_rev_parse("HEAD", cwd=dest)
    _retry(
        lambda: gh.rest(
            f"repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}/dispatches",
            method="POST",
            body={"ref": "main", "inputs": {"version": version}},
        ),
        what="release dispatch",
    )
    return seed_sha, before


def _resolve_runs(
    repo: str, seed_sha: str, before_ids: set[int], deadline: float, *, sleep=time.sleep
) -> dict[str, dict]:
    """Phase 4a: resolve the two fresh runs — CI by head_sha == seed sha (also
    excludes the noise run prepare's release commit triggers), the cut by
    workflow + head_sha == seed sha + a run id NOT in the pre-dispatch
    snapshot (id-set diff, immune to local-vs-GitHub clock skew; the head_sha
    tie keeps a concurrent dispatch by someone else from mis-associating)."""
    runs: dict[str, dict] = {}
    while time.time() < deadline:
        if "ci" not in runs:
            data = _retry(
                lambda: gh.rest(
                    f"repos/{repo}/actions/workflows/{CI_WORKFLOW}/runs"
                    f"?head_sha={seed_sha}&per_page=10"
                ),
                what="CI run resolution",
            )
            found = (data or {}).get("workflow_runs", [])
            if found:
                runs["ci"] = found[0]
        if "release" not in runs:
            data = _retry(
                lambda: gh.rest(
                    f"repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}/runs?per_page=30"
                ),
                what="release run resolution",
            )
            fresh = [
                run
                for run in (data or {}).get("workflow_runs", [])
                if run["id"] not in before_ids
                and run.get("event") == "workflow_dispatch"
                and run.get("head_sha") == seed_sha
            ]
            if fresh:
                runs["release"] = min(fresh, key=lambda run: run["id"])
        if "ci" in runs and "release" in runs:
            return runs
        sleep(RESOLVE_POLL_S)
    missing = sorted({"ci", "release"} - set(runs))
    raise CanaryError(f"timed out resolving fresh runs on {repo}: {', '.join(missing)}")


def _poll_to_completion(
    repo: str, runs: dict[str, dict], deadline: float, *, sleep=time.sleep
) -> dict[str, dict]:
    """Phase 4b: poll both runs to conclusion (backoff + transient retry)."""
    pending = dict(runs)
    done: dict[str, dict] = {}
    interval = POLL_INITIAL_S
    while pending:
        for key, run in list(pending.items()):
            run_id = run["id"]
            data = _retry(
                lambda rid=run_id: gh.rest(f"repos/{repo}/actions/runs/{rid}"),
                what=f"{key} run poll",
            )
            if (data or {}).get("status") == "completed":
                done[key] = data
                del pending[key]
        if not pending:
            break
        if time.time() >= deadline:
            still = ", ".join(f"{k} ({v.get('html_url', '?')})" for k, v in pending.items())
            raise CanaryError(f"--timeout exceeded waiting for: {still}")
        print(
            f"canary run: waiting on {'+'.join(sorted(pending))} (next poll in {interval:.0f}s)",
            file=sys.stderr,
        )
        sleep(min(interval, max(0.0, deadline - time.time())))
        interval = min(interval * POLL_BACKOFF, POLL_MAX_S)
    return done


def _collect_jobs(repo: str, run_id: int, *, sleep=time.sleep) -> list[dict]:
    """The completed run's jobs, re-polled until the listing settles.

    The jobs endpoint is eventually consistent with the run resource: a
    just-completed run can briefly list a job as still in_progress (caught
    live on the first green round — an all-green cut reported one macOS
    build job as IN_PROGRESS/INFRA). Re-poll until every job reports
    completed, bounded; return the last snapshot if it never settles (the
    run-conclusion backstop in main still decides pass/fail)."""
    jobs: list[dict] = []
    for attempt in range(JOBS_SETTLE_ATTEMPTS):
        data = _retry(
            lambda: gh.rest(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"),
            what="job collection",
        )
        jobs = (data or {}).get("jobs", [])
        if jobs and all(job.get("status") == "completed" for job in jobs):
            return jobs
        if attempt + 1 < JOBS_SETTLE_ATTEMPTS:
            print(
                f"canary run: jobs listing for run {run_id} not settled yet "
                f"(attempt {attempt + 1}/{JOBS_SETTLE_ATTEMPTS}), re-polling",
                file=sys.stderr,
            )
            sleep(JOBS_SETTLE_DELAY_S)
    return jobs


def _post_commit_status(
    sha: str, family: str, *, success: bool, target_url: str, description: str
) -> None:
    """Phase 5: the durable record — a `canary/<family>` commit status on
    release@<sha>, what the slice-4 cut gate reads."""
    _retry(
        lambda: gh.rest(
            f"repos/{RELEASE_REPO}/statuses/{sha}",
            method="POST",
            fields={
                "state": "success" if success else "failure",
                "context": f"canary/{family}",
                "target_url": target_url,
                "description": description[:140],
            },
        ),
        what="commit status",
    )


def _cleanup_prereleases(repo: str, keep: int) -> list[str]:
    """Phase 6: delete canary prerelease releases+tags beyond --keep (newest
    kept). Best-effort per item — cleanup must never fail the round."""
    data = _retry(
        lambda: gh.rest(f"repos/{repo}/releases?per_page=100"),
        what="release list (cleanup)",
    )
    canary_releases = [
        rel
        for rel in (data or [])
        if rel.get("prerelease") and "-canary." in (rel.get("tag_name") or "")
    ]
    canary_releases.sort(key=lambda rel: rel.get("created_at") or "", reverse=True)
    deleted: list[str] = []
    for rel in canary_releases[keep:]:
        tag = rel["tag_name"]
        try:
            gh.rest(f"repos/{repo}/releases/{rel['id']}", method="DELETE")
            gh.rest(f"repos/{repo}/git/refs/tags/{tag}", method="DELETE")
            deleted.append(tag)
        except gh.GhError as exc:
            print(f"canary run: cleanup of {tag} failed (ignored): {exc}", file=sys.stderr)
    return deleted


# ── main ─────────────────────────────────────────────────────────────────────


def _usage_error(msg: str) -> int:
    print(f"release-core admin canary run: {msg}", file=sys.stderr)
    print(USAGE.strip("\n"), file=sys.stderr)
    return 64


def _parse_args(argv: list[str]) -> dict | int:
    user = os.environ.get("USER") or "shared"
    opts: dict = {
        "ref": None,
        "families": None,
        "root": f"/tmp/release-canary-{user}",  # noqa: S108 — per-user, matches verify's pattern
        "timeout_min": 40.0,
        "keep": 5,
        "json": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(USAGE.strip("\n"))
            return 0
        if arg in ("--ref", "--family", "--root", "--timeout", "--keep"):
            if i + 1 >= len(argv):
                return _usage_error(f"{arg} needs a value")
            value = argv[i + 1]
            i += 2
            if arg == "--ref":
                opts["ref"] = value
            elif arg == "--family":
                opts["families"] = [f.strip() for f in value.split(",") if f.strip()]
            elif arg == "--root":
                opts["root"] = value
            elif arg == "--timeout":
                try:
                    opts["timeout_min"] = float(value)
                except ValueError:
                    return _usage_error(f"--timeout wants minutes, got {value!r}")
                if opts["timeout_min"] <= 0:
                    # A non-positive budget expires the deadline immediately.
                    return _usage_error(f"--timeout wants a positive number, got {value!r}")
            elif arg == "--keep":
                try:
                    opts["keep"] = int(value)
                except ValueError:
                    return _usage_error(f"--keep wants an integer, got {value!r}")
                if opts["keep"] < 0:
                    # Negative slicing would prune an unexpected subset.
                    return _usage_error(f"--keep wants a non-negative integer, got {value!r}")
        elif arg == "--json":
            opts["json"] = True
            i += 1
        else:
            return _usage_error(f"unknown arg: {arg}")
    if opts["ref"] is None:
        return _usage_error("--ref is required (e.g. --ref main)")
    return opts


def main(argv: list[str]) -> int:  # noqa: C901, PLR0912, PLR0915 — linear phase pipeline
    opts = _parse_args(argv)
    if isinstance(opts, int):
        return opts

    try:
        registry = managed_repos.canaries()
    except Exception as exc:
        print(f"canary run: cannot read the canaries registry: {exc}", file=sys.stderr)
        return 2
    if not registry:
        print("canary run: no canaries registered in managed-repos.yaml", file=sys.stderr)
        return 2
    families = opts["families"] or sorted(registry)
    unknown = [f for f in families if f not in registry]
    if unknown:
        print(
            f"canary run: unregistered famil{'ies' if len(unknown) > 1 else 'y'}: "
            f"{', '.join(unknown)} (registered: {', '.join(sorted(registry))})",
            file=sys.stderr,
        )
        return 2

    if proc.run(["gh", "auth", "status"], check=False).returncode != 0:
        print("canary run: `gh auth status` failed — authenticate first", file=sys.stderr)
        return 2

    root = opts["root"]
    os.makedirs(root, exist_ok=True)
    runid = time.strftime("%Y%m%d%H%M%S", time.gmtime())

    try:
        # Phase 0/1 — clone release at the candidate, resolve, publish canary/<sha12>.
        release_dir = os.path.join(root, "release")
        shutil.rmtree(release_dir, ignore_errors=True)
        if gh.repo_clone(RELEASE_REPO, release_dir).returncode != 0:
            raise CanaryError(f"clone of {RELEASE_REPO} failed")
        sha = _resolve_ref(release_dir, opts["ref"])
        sha12 = sha[:12]
        branch = f"canary/{sha12}"
        for family in families:
            inflight = _inflight_run(registry[family], sha12)
            if inflight:
                raise CanaryError(
                    f"a round for release@{sha12} is already in flight on "
                    f"{registry[family]}: {inflight.get('html_url')}"
                )
        rewritten = _publish_candidate(release_dir, sha, branch, opts["ref"])
        print(
            f"canary run: published {branch} ({rewritten} self-refs rewritten)",
            file=sys.stderr,
        )
    except (CanaryError, gh.GhError, proc.ProcError, OSError) as exc:
        # GhError/ProcError/OSError too: the helpers under here (clone,
        # _retry'd gh calls, git plumbing, the workflow-file rewrites) raise
        # them past the bounded retries — every phase-0/1 failure is a setup
        # error, never an unhandled stack trace.
        print(f"canary run: {exc}", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    footer: list[str] = []
    payload: dict = {
        "ref": opts["ref"],
        "sha": sha,
        "branch": branch,
        "runid": runid,
        "families": {},
    }
    any_failure = False
    setup_error = False

    for family in families:
        repo = registry[family]
        try:
            dest = _seed_canary(
                root=root,
                family=family,
                repo=repo,
                release_dir=release_dir,
                branch=branch,
                sha12=sha12,
                ref=opts["ref"],
                runid=runid,
            )
            tags = _retry(
                lambda r=repo: gh.rest(f"repos/{r}/tags?per_page=100"),
                what="tag list",
            )
            version = next_canary_version(
                [t.get("name", "") for t in (tags or [])],
                runid,
            )
            seed_sha, before_ids = _dispatch(repo, dest, version)
            print(
                f"canary run: {family}: seed {seed_sha[:12]} pushed, release {version} dispatched",
                file=sys.stderr,
            )
            deadline = time.time() + opts["timeout_min"] * 60.0
            runs = _resolve_runs(repo, seed_sha, before_ids, deadline)
            done = _poll_to_completion(repo, runs, deadline)

            family_rows: list[dict] = []
            family_failed = False
            run_urls: dict[str, str] = {}
            for key in ("ci", "release"):
                run = done[key]
                run_urls[key] = run.get("html_url", "")
                workflow = "ci" if key == "ci" else "release"
                rows, failed = job_rows(
                    family, workflow, _collect_jobs(repo, run["id"]), run.get("conclusion")
                )
                # A run can fail with zero failed jobs visible (e.g. startup
                # failure); trust the run conclusion as the backstop.
                failed = failed or run.get("conclusion") not in ("success", "skipped")
                family_rows.extend(rows)
                family_failed = family_failed or failed

            n_infra = sum(1 for r in family_rows if r["class"] == "INFRA")
            n_project = sum(1 for r in family_rows if r["class"] == "PROJECT")
            if family_failed:
                parts = []
                if n_infra:
                    parts.append(f"{n_infra} INFRA failure{'s' if n_infra > 1 else ''}")
                if n_project:
                    parts.append(f"{n_project} PROJECT failure{'s' if n_project > 1 else ''}")
                detail = ", ".join(parts) or "run-level failure"
                failing_url = next(
                    (r["url"] for r in family_rows if r["class"] in ("INFRA", "PROJECT")),
                    run_urls["ci"],
                )
                footer.append(f"{family}: FAIL — {detail}. {failing_url}")
                status_url = failing_url
                description = f"canary round FAILED ({detail}; {version})"
            else:
                footer.append(
                    f"{family}: PASS ({len(family_rows)} jobs) "
                    f"ci: {run_urls['ci']} cut: {run_urls['release']}"
                )
                status_url = run_urls["release"]
                description = f"canary round green (ci + {version} cut)"
            _post_commit_status(
                sha,
                family,
                success=not family_failed,
                target_url=status_url,
                description=description,
            )
            deleted = _cleanup_prereleases(repo, opts["keep"])
            if deleted:
                print(
                    f"canary run: {family}: pruned prereleases: {', '.join(deleted)}",
                    file=sys.stderr,
                )
            all_rows.extend(family_rows)
            any_failure = any_failure or family_failed
            payload["families"][family] = {
                "repo": repo,
                "seed_sha": seed_sha,
                "version": version,
                "verdict": "FAIL" if family_failed else "PASS",
                "runs": {k: {"id": done[k]["id"], "url": run_urls[k]} for k in done},
                "jobs": family_rows,
                "commit_status": {
                    "context": f"canary/{family}",
                    "state": "failure" if family_failed else "success",
                    "target_url": status_url,
                },
                "pruned": deleted,
            }
        except (CanaryError, gh.GhError, proc.ProcError, OSError) as exc:
            # GhError/ProcError/OSError too: _retry re-raises after the
            # bounded attempts, the git wrappers raise directly, and the
            # caller-rewrite/fragment writes can hit filesystem errors — any
            # of them is a family-level setup error; record it and continue
            # to the next family rather than aborting the whole round.
            print(f"canary run: {family}: {exc}", file=sys.stderr)
            footer.append(f"{family}: SETUP ERROR — {exc}")
            payload["families"][family] = {"repo": repo, "verdict": "ERROR", "error": str(exc)}
            setup_error = True

    # Verdict mirrors the exit code exactly (ERROR↔2 dominates FAIL↔1 dominates
    # PASS↔0): an incomplete round is never trustworthy, so a setup error wins
    # even when another family also has real job failures.
    if setup_error:
        suffix = " Job failures also present above." if any_failure else ""
        footer.append(f"verdict: ERROR — round incomplete (setup failure above).{suffix}")
        payload["verdict"] = "ERROR"
    elif any_failure:
        footer.append(f"verdict: FAIL — do NOT cut/advance from {sha12}.")
        payload["verdict"] = "FAIL"
    else:
        footer.append(f"verdict: PASS — release@{sha12} survived the consumer life.")
        payload["verdict"] = "PASS"

    if opts["json"]:
        print(json.dumps(payload, indent=2))
    else:
        header = f"canary run: release@{sha12} ({opts['ref']}) → {branch}"
        print(render_report(header, all_rows, footer))

    if setup_error:
        return 2
    return 1 if any_failure else 0
