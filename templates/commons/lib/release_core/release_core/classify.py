"""classify — THE failing-step classifier, shared by every verdict-reporting verb.

One implementation, three consumers (#594/#595, extracted from the #587 canary):

- ``admin canary run`` — per-job INFRA/PROJECT classification of a canary round,
- ``admin repos poke`` — classified verdict for a fresh-event consumer run,
- ``admin repos verify`` — expected-toolchain-artifact classification of a
  hermetic gate FAIL.

Two hard-won GitHub mechanics live here so no verb re-learns them:

1. **The jobs-endpoint settle re-poll** (:func:`collect_jobs`): the jobs
   endpoint is eventually consistent with the run resource — a just-completed
   run can briefly list a job as still in_progress (caught live: an all-green
   cut reported a macOS build job as IN_PROGRESS/INFRA).
2. **The run-conclusion backstop** (:func:`job_rows`): when the listing never
   settles, a job still reporting in_progress under a run whose conclusion is
   success is a stale snapshot, not a failure — annotated, never counted.

Plus the transient-error retry (:func:`retry`, the #582 lesson: one TLS blip
must not kill a 40-minute round).
"""

from __future__ import annotations

import re
import sys
import time

from . import gh, proc

RETRY_ATTEMPTS = 3  # bounded transient-error retries around gh calls (#582)
RETRY_DELAY_S = 5.0
JOBS_SETTLE_ATTEMPTS = 6  # jobs endpoint lags the run resource — re-poll until settled
JOBS_SETTLE_DELAY_S = 10.0

# ── INFRA vs PROJECT, by failing step (#587 §3) ──────────────────────────────
#
# Classification is mechanical from job/step names. INFRA = the release-owned
# plumbing (arm-gate materialize/provision, the boot resolver, init,
# prepare-release internals) — a release/upstream bug; PROJECT = the repo's
# own content (gate checks, cargo, bats, compilation) — consumer-side (for a
# canary: fixture rot, not pipeline rot).
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
    "project checks",
    "canonical checks",  # legacy step name (pre-#655) — still seen in in-flight runs
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
    it classifies INFRA. Unknown failures default to INFRA: unattributed
    breakage escalates upstream, never silently blames the consumer."""
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
    scope: str, workflow: str, jobs: list[dict], run_conclusion: str | None = None
) -> tuple[list[dict], bool]:
    """Per-job report rows for one completed run. Returns (rows, any_failure).

    ``scope`` labels the first report column (the canary family, the poked
    repo, …). Skipped jobs are annotated: `skipped (fenced)` when nothing
    failed before them (a designed fence, e.g. publish-crates: false),
    `skipped (cascade)` when an earlier failure cancelled the chain.

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
            rows.append(_row(scope, workflow, job, "unsettled (run green)", "-", "-"))
            continue
        if conclusion == "success":
            rows.append(_row(scope, workflow, job, "success", "-", "-"))
        elif conclusion == "skipped":
            note = "skipped (cascade)" if any_failure else "skipped (fenced)"
            rows.append(_row(scope, workflow, job, note, "-", "-"))
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
            rows.append(_row(scope, workflow, job, conclusion.upper(), klass, failed_step or "-"))
    return rows, any_failure


def _row(scope: str, workflow: str, job: dict, conclusion: str, klass: str, step: str) -> dict:
    return {
        "scope": scope,
        "workflow": workflow,
        "job": job.get("name") or "?",
        "conclusion": conclusion,
        "class": klass,
        "step": step,
        "url": job.get("html_url") or "",
    }


COLUMNS = ("scope", "workflow", "job", "conclusion", "class", "step")
HEADERS = ("scope", "workflow", "job", "conclusion", "class", "step (first failure)")


def render_report(header: str, rows: list[dict], footer_lines: list[str]) -> str:
    """The human report: header line, aligned per-job table, footer/verdict lines."""
    widths = [len(h) for h in HEADERS]
    for row in rows:
        for i, col in enumerate(COLUMNS):
            widths[i] = max(widths[i], len(str(row[col])))
    lines = [header, ""]
    lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(HEADERS)).rstrip())
    for row in rows:
        lines.append(
            "  ".join(str(row[col]).ljust(widths[i]) for i, col in enumerate(COLUMNS)).rstrip()
        )
    lines.append("")
    lines.extend(footer_lines)
    return "\n".join(lines)


# ── gh seams shared by the run-watching verbs ────────────────────────────────


def retry(fn, *, what: str, prefix: str, attempts: int = RETRY_ATTEMPTS, sleep=time.sleep):
    """Bounded retry with linear backoff around a gh call (the #582 lesson:
    one transient blip — TLS handshake timeout, a 5xx — must not kill a
    40-minute round). Re-raises the last error once attempts are exhausted.
    ``prefix`` names the calling verb in the stderr chatter."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (gh.GhError, proc.ProcError) as exc:
            last = exc
            if attempt < attempts:
                print(
                    f"{prefix}: transient {what} error "
                    f"(attempt {attempt}/{attempts}, retrying): {exc}",
                    file=sys.stderr,
                )
                sleep(RETRY_DELAY_S * attempt)
    assert last is not None
    raise last


def collect_jobs(repo: str, run_id: int, *, prefix: str, sleep=time.sleep) -> list[dict]:
    """The completed run's jobs, re-polled until the listing settles.

    The jobs endpoint is eventually consistent with the run resource: a
    just-completed run can briefly list a job as still in_progress (caught
    live on the first green canary round — an all-green cut reported one
    macOS build job as IN_PROGRESS/INFRA). Re-poll until every job reports
    completed, bounded; return the last snapshot if it never settles (the
    run-conclusion backstop in :func:`job_rows` still decides pass/fail)."""
    jobs: list[dict] = []
    for attempt in range(JOBS_SETTLE_ATTEMPTS):
        data = retry(
            lambda: gh.rest(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"),
            what="job collection",
            prefix=prefix,
            sleep=sleep,
        )
        jobs = (data or {}).get("jobs", [])
        if jobs and all(job.get("status") == "completed" for job in jobs):
            return jobs
        if attempt + 1 < JOBS_SETTLE_ATTEMPTS:
            print(
                f"{prefix}: jobs listing for run {run_id} not settled yet "
                f"(attempt {attempt + 1}/{JOBS_SETTLE_ATTEMPTS}), re-polling",
                file=sys.stderr,
            )
            sleep(JOBS_SETTLE_DELAY_S)
    return jobs


# ── hermetic-gate FAIL classification (#594) ─────────────────────────────────
#
# `admin repos verify` runs the shared gate in clones WITHOUT the consumer's
# project toolchain (no `npm install` — out of scope by design). The
# npm-quality checks invoke project-local tools (`npx --no-install eslint` /
# `prettier`, `npm run typecheck`), so in a hermetic clone they CANNOT run
# faithfully — any failure of one of these checks there is an expected
# toolchain artifact, not a regression (the consumer's own PR CI is the real
# gate for them). Everything else in the gate (markdownlint / yamllint /
# shellcheck / rust checks) runs from the release-provisioned toolset, so its
# failure is real.
GATE_DEP_CHECKS = frozenset({"eslint", "prettier", "typecheck"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# A failed check in the pinned lefthook's summary block: `✗ <name> (<time>)`
# (U+2717; passes are `✓` U+2713). The glyphs are MODE-DEPENDENT: interactive
# (tty) runs render `🥊` fail / `✔️` pass, captured (piped) runs render `✗`/`✓`.
# The classifier only ever reads captured logs (verify/poke/canary run the gate
# through subprocess capture), so the piped glyph is the one format — verified
# against live verify-gate logs (the tty-glyph guess was this regex's original
# bug: every npm-artifact FAIL read as unexpected).
# Parsing this is deterministic, not fragile: the gate runs ONE pinned lefthook
# everywhere (release#567 — version skew between gate runs is forbidden), so
# the summary format is fixed until the pin moves (and the pin bump's fleet
# sweep would surface a format change immediately as unclassifiable FAILs).
_SUMMARY_FAIL_RE = re.compile(r"^\s*✗\s+(\S+)")


def failed_gate_checks(log_text: str) -> list[str]:
    """The failed check names from a captured `lefthook run pre-commit` log.

    Reads the trailing `summary:` block (one `✗ <check>` line per failure),
    ANSI-stripped. Returns [] when no summary/failure lines are found — an
    unparseable log classifies as unexpected (fail toward visibility)."""
    failed: list[str] = []
    in_summary = False
    for raw in log_text.splitlines():
        line = _ANSI_RE.sub("", raw)
        if line.startswith("summary:"):
            in_summary = True
            continue
        if in_summary:
            match = _SUMMARY_FAIL_RE.match(line)
            if match:
                failed.append(match.group(1))
    return failed


def expected_artifact_fail(failed_checks: list[str]) -> bool:
    """True iff a hermetic gate FAIL is an expected toolchain artifact: at
    least one failed check, and EVERY one needs the absent project toolchain
    (:data:`GATE_DEP_CHECKS`). A mixed failure (any toolset-provided check in
    the set) stays unexpected — that part is real debt."""
    return bool(failed_checks) and all(check in GATE_DEP_CHECKS for check in failed_checks)
