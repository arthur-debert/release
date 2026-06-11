"""classify — THE shared failing-step classifier (#594/#595, extracted from #587).

Fully offline: INFRA/PROJECT classification, the per-job report rows (incl.
the run-conclusion backstop), the report renderer, the jobs-endpoint settle
re-poll, the bounded transient retry, and the hermetic-gate FAIL
classification (lefthook-summary parsing + the expected-artifact rule).
"""

from __future__ import annotations

import pytest
from release_core import classify, gh


def _step(name: str, conclusion: str = "success") -> dict:
    return {"name": name, "conclusion": conclusion}


def _job(name: str, conclusion: str, steps: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": steps or [],
        "html_url": f"https://example.test/{name}",
    }


# ── classify_failure ─────────────────────────────────────────────────────────


def test_arm_gate_step_failure_is_infra():
    steps = [_step("Checkout"), _step("Arm the gate toolset", "failure")]
    assert classify.classify_failure("Arm the gate toolset", steps) == "INFRA"


@pytest.mark.parametrize(
    "step_name",
    [
        "Materialize the managed tree",
        "Provision the gate toolset",
        "Run install-release-core",
        "release-core init",
        "Resolution probe",
        "Run arthur-debert/release/.github/actions/prepare-release@v2",
    ],
)
def test_infra_step_names_classify_infra(step_name):
    assert classify.classify_failure(step_name, [_step(step_name, "failure")]) == "INFRA"


def test_project_step_with_armed_tree_is_project():
    steps = [
        _step("Arm the gate toolset", "success"),
        _step("Run canonical checks (bin/check)", "failure"),
    ]
    assert classify.classify_failure("Run canonical checks (bin/check)", steps) == "PROJECT"


def test_project_step_without_materialize_is_infra():
    # The #579 class: e2e job whose arm-gate/materialize step is gone — the
    # bats step fails (bin/check-e2e exit 127) on an un-armed tree. Release's
    # bug, never the project content's.
    steps = [
        _step("Checkout"),
        _step("Download binary"),
        _step("Install bats-core"),
        _step("Run E2E tests", "failure"),
    ]
    assert classify.classify_failure("Run E2E tests", steps) == "INFRA"


def test_project_step_with_failed_materialize_is_infra():
    steps = [
        _step("Arm the gate toolset", "failure"),
        _step("Run E2E tests", "failure"),
    ]
    assert classify.classify_failure("Run E2E tests", steps) == "INFRA"


def test_cargo_failure_with_armed_tree_is_project():
    steps = [_step("Arm the gate toolset", "success"), _step("cargo nextest", "failure")]
    assert classify.classify_failure("cargo nextest", steps) == "PROJECT"


def test_unknown_step_defaults_to_infra():
    steps = [_step("Some novel step", "failure")]
    assert classify.classify_failure("Some novel step", steps) == "INFRA"


# ── job_rows ─────────────────────────────────────────────────────────────────


def test_job_rows_green_run():
    rows, failed = classify.job_rows(
        "rust", "ci", [_job("ci / check", "success"), _job("ci / e2e", "success")]
    )
    assert not failed
    assert [r["conclusion"] for r in rows] == ["success", "success"]
    assert all(r["class"] == "-" for r in rows)


def test_job_rows_failure_carries_class_and_first_failing_step():
    steps = [
        _step("Arm the gate toolset", "success"),
        _step("Run E2E tests", "failure"),
    ]
    rows, failed = classify.job_rows("rust", "ci", [_job("ci / e2e", "failure", steps)])
    assert failed
    assert rows[0]["conclusion"] == "FAILURE"
    assert rows[0]["class"] == "PROJECT"
    assert rows[0]["step"] == "Run E2E tests"


def test_job_rows_scope_labels_the_first_column():
    rows, _failed = classify.job_rows("arthur-debert/padz", "ci", [_job("ci / check", "success")])
    assert rows[0]["scope"] == "arthur-debert/padz"


def test_job_rows_skipped_annotation_fenced_vs_cascade():
    jobs = [
        _job("release / publish-crates", "skipped"),  # before any failure → fenced
        _job("release / prepare", "failure", [_step("Prepare release", "failure")]),
        _job("release / build", "skipped"),  # after a failure → cascade
    ]
    rows, failed = classify.job_rows("rust", "release", jobs)
    assert failed
    assert rows[0]["conclusion"] == "skipped (fenced)"
    assert rows[2]["conclusion"] == "skipped (cascade)"


def test_job_rows_unsettled_listing_under_green_run_is_not_a_failure():
    # The jobs endpoint can stay stale past the settle re-polls: a job still
    # in_progress under a run whose conclusion is success must be annotated,
    # never counted as a failure (the run conclusion is the backstop).
    jobs = [
        {"name": "ci / check", "conclusion": "success", "status": "completed", "steps": []},
        {"name": "ci / e2e", "conclusion": None, "status": "in_progress", "steps": []},
    ]
    rows, failed = classify.job_rows("rust", "ci", jobs, "success")
    assert not failed
    assert rows[1]["conclusion"] == "unsettled (run green)"
    assert rows[1]["class"] == "-"


def test_job_rows_in_progress_under_failed_run_still_counts():
    jobs = [{"name": "ci / e2e", "conclusion": None, "status": "in_progress", "steps": []}]
    rows, failed = classify.job_rows("rust", "ci", jobs, "failure")
    assert failed
    assert rows[0]["conclusion"] == "IN_PROGRESS"


# ── render_report ────────────────────────────────────────────────────────────


def test_render_report_table_shape():
    rows = [
        {
            "scope": "rust",
            "workflow": "ci",
            "job": "ci / check",
            "conclusion": "success",
            "class": "-",
            "step": "-",
            "url": "",
        },
        {
            "scope": "rust",
            "workflow": "ci",
            "job": "ci / e2e",
            "conclusion": "FAILURE",
            "class": "INFRA",
            "step": "Arm the gate toolset",
            "url": "https://example.test/run",
        },
    ]
    out = classify.render_report(
        "canary run: release@3fa9c12bd04e (main) → canary/3fa9c12bd04e",
        rows,
        ["rust: FAIL — 1 INFRA failure. https://example.test/run", "verdict: FAIL"],
    )
    lines = out.splitlines()
    assert lines[0].startswith("canary run: release@3fa9c12bd04e")
    header = lines[2]
    assert header.startswith("scope")
    for col in ("workflow", "job", "conclusion", "class", "step (first failure)"):
        assert col in header
    assert any("FAILURE" in ln and "INFRA" in ln and "Arm the gate toolset" in ln for ln in lines)
    assert lines[-1] == "verdict: FAIL"


# ── retry ────────────────────────────────────────────────────────────────────


def test_retry_recovers_from_a_transient_error(capsys):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise gh.GhError("tls blip")
        return "ok"

    assert classify.retry(flaky, what="probe", prefix="t", sleep=lambda _s: None) == "ok"
    assert "t: transient probe error" in capsys.readouterr().err


def test_retry_reraises_after_bounded_attempts():
    calls = {"n": 0}

    def always_down():
        calls["n"] += 1
        raise gh.GhError("down")

    with pytest.raises(gh.GhError, match="down"):
        classify.retry(always_down, what="probe", prefix="t", sleep=lambda _s: None)
    assert calls["n"] == classify.RETRY_ATTEMPTS


# ── collect_jobs (jobs-endpoint settling) ────────────────────────────────────


def test_collect_jobs_repolls_until_listing_settles(monkeypatch):
    # The jobs endpoint is eventually consistent with the run resource: the
    # first snapshot after run completion can still list a job in_progress
    # (caught live — an all-green cut reported a macOS build job as
    # IN_PROGRESS). collect_jobs must re-poll until every job is completed.
    snapshots = [
        {"jobs": [{"name": "a", "status": "completed"}, {"name": "b", "status": "in_progress"}]},
        {"jobs": [{"name": "a", "status": "completed"}, {"name": "b", "status": "completed"}]},
    ]
    calls = {"n": 0}

    def fake_rest(path, **kw):
        data = snapshots[min(calls["n"], len(snapshots) - 1)]
        calls["n"] += 1
        return data

    monkeypatch.setattr(classify.gh, "rest", fake_rest)
    jobs = classify.collect_jobs("o/r", 1, prefix="t", sleep=lambda _s: None)
    assert calls["n"] == 2
    assert all(j["status"] == "completed" for j in jobs)


def test_collect_jobs_gives_up_after_bounded_attempts(monkeypatch):
    monkeypatch.setattr(
        classify.gh,
        "rest",
        lambda path, **kw: {"jobs": [{"name": "a", "status": "in_progress"}]},
    )
    jobs = classify.collect_jobs("o/r", 1, prefix="t", sleep=lambda _s: None)
    assert jobs == [{"name": "a", "status": "in_progress"}]


# ── hermetic-gate FAIL classification (#594) ─────────────────────────────────

# The piped output of THE pinned lefthook (2.1.9, release#567), captured live:
# truecolor box-drawing around the banner (which itself carries a 🥊), per-check
# sections, then the summary block — ✔️ pass lines and 🥊 fail lines.
_GATE_LOG = (
    "\x1b[38;2;0;0;0m╭──────────────────────────────╮\x1b[m\n"
    "\x1b[38;2;0;0;0m│\x1b[m 🥊 lefthook v2.1.9  hook:  \x1b[1mpre-commit\x1b[m │\n"
    "\x1b[38;2;6;6;6m╰──────────────────────────────╯\x1b[m\n"
    "┃  eslint ❯\n"
    "\n"
    "sh: 1: eslint: not found\n"
    "exit status 127\n"
    "summary: (done in 0.42 seconds)\n"
    "✔️ markdownlint (0.10 seconds)\n"
    "✔️ shellcheck (0.08 seconds)\n"
    "🥊 eslint (0.01 seconds)\n"
    "🥊 typecheck (0.02 seconds)\n"
)


def test_failed_gate_checks_parses_the_summary_block():
    assert classify.failed_gate_checks(_GATE_LOG) == ["eslint", "typecheck"]


def test_failed_gate_checks_ignores_the_banner_glove():
    # The 🥊 in the lefthook banner is BEFORE the summary block — never a failure.
    banner_only = _GATE_LOG.split("summary:")[0]
    assert classify.failed_gate_checks(banner_only) == []


def test_failed_gate_checks_empty_on_unparseable_log():
    assert classify.failed_gate_checks("gate-out\ngate-err\n") == []


def test_expected_artifact_fail_all_dep_needing():
    assert classify.expected_artifact_fail(["eslint", "typecheck"])
    assert classify.expected_artifact_fail(["prettier"])


def test_expected_artifact_fail_rejects_mixed_failures():
    # A toolset-provided check in the failed set is real debt — unexpected.
    assert not classify.expected_artifact_fail(["eslint", "markdownlint"])


def test_expected_artifact_fail_rejects_empty():
    # No parseable failed checks (unparseable log) → fail toward visibility.
    assert not classify.expected_artifact_fail([])
