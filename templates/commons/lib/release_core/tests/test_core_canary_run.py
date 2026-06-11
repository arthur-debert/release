"""canary_run verb — the pure seams (#587 slice 1).

Fully offline: the ref-rewrite, prerelease-version computation, INFRA/PROJECT
classification, report rendering, and the `canaries:` manifest block
(including the invariant that the `projects:` sweep does NOT include it).
The gh/git glue is exercised by the live canary rounds, not here.
"""

from __future__ import annotations

import pytest
from release_core.verbs import canary_run, managed_repos

BRANCH = "canary/3fa9c12bd04e"


# ── rewrite_self_refs ────────────────────────────────────────────────────────


def test_rewrite_floating_major_ref():
    text = "        uses: arthur-debert/release/.github/actions/arm-gate@v2\n"
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 1
    assert new == f"        uses: arthur-debert/release/.github/actions/arm-gate@{BRANCH}\n"


def test_rewrite_exact_pin_and_v1():
    text = (
        "    uses: arthur-debert/release/.github/workflows/rust-ci.yml@v2.15.0\n"
        "    uses: arthur-debert/release/.github/workflows/bats-e2e.yml@v1\n"
    )
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 2
    assert f"rust-ci.yml@{BRANCH}" in new
    assert f"bats-e2e.yml@{BRANCH}" in new


def test_rewrite_is_idempotent_over_an_earlier_canary_ref():
    text = "  uses: arthur-debert/release/.github/actions/arm-gate@canary/aaaabbbbcccc\n"
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 1
    assert f"arm-gate@{BRANCH}" in new
    # And rewriting the result again is a fixed point.
    again, n2 = canary_run.rewrite_self_refs(new, BRANCH)
    assert n2 == 1
    assert again == new


def test_rewrite_list_item_form():
    text = "      - uses: arthur-debert/release/.github/actions/setup-rust@v2\n"
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 1
    assert f"setup-rust@{BRANCH}" in new


def test_non_release_uses_untouched():
    text = (
        "      - uses: actions/checkout@v6\n"
        "      - uses: softprops/action-gh-release@v2\n"
        "      - uses: bats-core/bats-action@4.0.0\n"
    )
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 0
    assert new == text


def test_commented_self_refs_untouched():
    text = (
        "#       uses: arthur-debert/release/.github/workflows/rust-ci.yml@v1\n"
        "  # uses: arthur-debert/release/.github/actions/arm-gate@v2\n"
    )
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 0
    assert new == text


def test_trailing_comment_preserved():
    text = "    uses: arthur-debert/release/.github/actions/arm-gate@v2  # pinned\n"
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 1
    assert new == f"    uses: arthur-debert/release/.github/actions/arm-gate@{BRANCH}  # pinned\n"


def test_rewrite_mixed_document_counts_only_self_refs():
    text = (
        "jobs:\n"
        "  ci:\n"
        "    uses: arthur-debert/release/.github/workflows/rust-ci.yml@v2\n"
        "    steps:\n"
        "      - uses: actions/checkout@v6\n"
        "      - uses: arthur-debert/release/.github/actions/arm-gate@v2\n"
    )
    new, n = canary_run.rewrite_self_refs(text, BRANCH)
    assert n == 2
    assert "actions/checkout@v6" in new
    assert new.count(BRANCH) == 2


# ── next_canary_version ──────────────────────────────────────────────────────


def test_first_canary_version():
    assert canary_run.next_canary_version([], "20260611120000") == "0.0.1-canary.20260611120000"


def test_version_increments_past_the_highest_canary_tag():
    tags = ["v0.0.3-canary.20260601", "v0.0.1-canary.x", "v0.0.2-canary.y"]
    assert canary_run.next_canary_version(tags, "rid") == "0.0.4-canary.rid"


def test_version_ignores_non_canary_tags():
    tags = ["v1.2.3", "v0.0.9", "v0.1.0-canary.z", "v2.19.0"]
    assert canary_run.next_canary_version(tags, "rid") == "0.0.1-canary.rid"


def test_version_handles_multi_digit_n():
    tags = [f"v0.0.{i}-canary.r" for i in (2, 10, 9)]
    assert canary_run.next_canary_version(tags, "rid") == "0.0.11-canary.rid"


# ── classify_failure ─────────────────────────────────────────────────────────


def _step(name: str, conclusion: str = "success") -> dict:
    return {"name": name, "conclusion": conclusion}


def test_arm_gate_step_failure_is_infra():
    steps = [_step("Checkout"), _step("Arm the gate toolset", "failure")]
    assert canary_run.classify_failure("Arm the gate toolset", steps) == "INFRA"


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
    assert canary_run.classify_failure(step_name, [_step(step_name, "failure")]) == "INFRA"


def test_project_step_with_armed_tree_is_project():
    steps = [
        _step("Arm the gate toolset", "success"),
        _step("Run canonical checks (bin/check)", "failure"),
    ]
    assert canary_run.classify_failure("Run canonical checks (bin/check)", steps) == "PROJECT"


def test_project_step_without_materialize_is_infra():
    # The #579 class: e2e job whose arm-gate/materialize step is gone — the
    # bats step fails (bin/check-e2e exit 127) on an un-armed tree. Release's
    # bug, never the canary content's.
    steps = [
        _step("Checkout"),
        _step("Download binary"),
        _step("Install bats-core"),
        _step("Run E2E tests", "failure"),
    ]
    assert canary_run.classify_failure("Run E2E tests", steps) == "INFRA"


def test_project_step_with_failed_materialize_is_infra():
    steps = [
        _step("Arm the gate toolset", "failure"),
        _step("Run E2E tests", "failure"),
    ]
    assert canary_run.classify_failure("Run E2E tests", steps) == "INFRA"


def test_cargo_failure_with_armed_tree_is_project():
    steps = [_step("Arm the gate toolset", "success"), _step("cargo nextest", "failure")]
    assert canary_run.classify_failure("cargo nextest", steps) == "PROJECT"


def test_unknown_step_defaults_to_infra():
    steps = [_step("Some novel step", "failure")]
    assert canary_run.classify_failure("Some novel step", steps) == "INFRA"


# ── job_rows ─────────────────────────────────────────────────────────────────


def _job(name: str, conclusion: str, steps: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": steps or [],
        "html_url": f"https://example.test/{name}",
    }


def test_job_rows_green_run():
    rows, failed = canary_run.job_rows(
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
    rows, failed = canary_run.job_rows("rust", "ci", [_job("ci / e2e", "failure", steps)])
    assert failed
    assert rows[0]["conclusion"] == "FAILURE"
    assert rows[0]["class"] == "PROJECT"
    assert rows[0]["step"] == "Run E2E tests"


def test_job_rows_skipped_annotation_fenced_vs_cascade():
    jobs = [
        _job("release / publish-crates", "skipped"),  # before any failure → fenced
        _job("release / prepare", "failure", [_step("Prepare release", "failure")]),
        _job("release / build", "skipped"),  # after a failure → cascade
    ]
    rows, failed = canary_run.job_rows("rust", "release", jobs)
    assert failed
    assert rows[0]["conclusion"] == "skipped (fenced)"
    assert rows[2]["conclusion"] == "skipped (cascade)"


# ── render_report ────────────────────────────────────────────────────────────


def test_render_report_table_shape():
    rows = [
        {
            "family": "rust",
            "workflow": "ci",
            "job": "ci / check",
            "conclusion": "success",
            "class": "-",
            "step": "-",
            "url": "",
        },
        {
            "family": "rust",
            "workflow": "ci",
            "job": "ci / e2e",
            "conclusion": "FAILURE",
            "class": "INFRA",
            "step": "Arm the gate toolset",
            "url": "https://example.test/run",
        },
    ]
    out = canary_run.render_report(
        "canary run: release@3fa9c12bd04e (main) → canary/3fa9c12bd04e",
        rows,
        ["rust: FAIL — 1 INFRA failure. https://example.test/run", "verdict: FAIL"],
    )
    lines = out.splitlines()
    assert lines[0].startswith("canary run: release@3fa9c12bd04e")
    header = lines[2]
    assert header.startswith("family")
    for col in ("workflow", "job", "conclusion", "class", "step (first failure)"):
        assert col in header
    assert any("FAILURE" in ln and "INFRA" in ln and "Arm the gate toolset" in ln for ln in lines)
    assert lines[-1] == "verdict: FAIL"


# ── canaries: manifest block ─────────────────────────────────────────────────

MANIFEST = """\
projects:
  dodot:
    - { repo: arthur-debert/dodot, path: dodot }
  lex:
    - { repo: lex-fmt/lex, path: lex-fmt/lex }

canaries:
  rust: arthur-debert/release-canary-rust
"""


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    path = tmp_path / "managed-repos.yaml"
    path.write_text(MANIFEST)
    monkeypatch.setenv("MANAGED_REPOS_MANIFEST", str(path))
    monkeypatch.delenv("MANAGED_REPOS_SCRIPT_DIR", raising=False)
    return str(path)


def test_canaries_block_parses(manifest):
    assert managed_repos.canaries(manifest) == {"rust": "arthur-debert/release-canary-rust"}


def test_canaries_resolves_via_manifest_path_env(manifest):
    assert managed_repos.canaries() == {"rust": "arthur-debert/release-canary-rust"}


def test_canaries_absent_block_is_empty(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("projects:\n  dodot:\n    - { repo: a/d, path: d }\n")
    assert managed_repos.canaries(str(path)) == {}


def test_canaries_missing_manifest_is_empty(tmp_path):
    # A consumer repo has no managed-repos.yaml at all: that means "no
    # canaries registered", not an error — the #606 cut gate is inert there
    # by construction (registry-driven, not a skip flag).
    assert managed_repos.canaries(str(tmp_path / "nope.yaml")) == {}


def test_projects_sweep_does_not_include_canaries(manifest):
    # THE OQ6 invariant: everything built on _pairs (verify / migrate / inbox /
    # audit, --list/--paths) never sweeps the canary repos.
    pairs = managed_repos._pairs(manifest, [])
    repos = [repo for repo, _ in pairs]
    assert repos == ["arthur-debert/dodot", "lex-fmt/lex"]
    assert "arthur-debert/release-canary-rust" not in repos


def test_list_mode_excludes_canaries(manifest, capsys):
    rc = managed_repos.main(["--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "release-canary-rust" not in out


def test_job_rows_unsettled_listing_under_green_run_is_not_a_failure():
    # The jobs endpoint can stay stale past the settle re-polls: a job still
    # in_progress under a run whose conclusion is success must be annotated,
    # never counted as a failure (the run conclusion is the backstop).
    jobs = [
        {"name": "ci / check", "conclusion": "success", "status": "completed", "steps": []},
        {"name": "ci / e2e", "conclusion": None, "status": "in_progress", "steps": []},
    ]
    rows, failed = canary_run.job_rows("rust", "ci", jobs, "success")
    assert not failed
    assert rows[1]["conclusion"] == "unsettled (run green)"
    assert rows[1]["class"] == "-"


def test_job_rows_in_progress_under_failed_run_still_counts():
    jobs = [{"name": "ci / e2e", "conclusion": None, "status": "in_progress", "steps": []}]
    rows, failed = canary_run.job_rows("rust", "ci", jobs, "failure")
    assert failed
    assert rows[0]["conclusion"] == "IN_PROGRESS"


# ── arg validation ───────────────────────────────────────────────────────────


def test_negative_keep_is_usage_error(capsys):
    rc = canary_run.main(["--ref", "main", "--keep", "-1"])
    assert rc == 64
    assert "non-negative" in capsys.readouterr().err


def test_missing_ref_is_usage_error(capsys):
    rc = canary_run.main([])
    assert rc == 64
    assert "--ref is required" in capsys.readouterr().err


def test_non_positive_timeout_is_usage_error(capsys):
    rc = canary_run.main(["--ref", "main", "--timeout", "0"])
    assert rc == 64
    assert "positive" in capsys.readouterr().err


def test_family_list_strips_whitespace():
    opts = canary_run._parse_args(["--ref", "main", "--family", "rust, npm"])
    assert isinstance(opts, dict)
    assert opts["families"] == ["rust", "npm"]


# ── jobs-endpoint settling ───────────────────────────────────────────────────


def test_collect_jobs_repolls_until_listing_settles(monkeypatch):
    # The jobs endpoint is eventually consistent with the run resource: the
    # first snapshot after run completion can still list a job in_progress
    # (caught live — an all-green cut reported a macOS build job as
    # IN_PROGRESS). _collect_jobs must re-poll until every job is completed.
    snapshots = [
        {"jobs": [{"name": "a", "status": "completed"}, {"name": "b", "status": "in_progress"}]},
        {"jobs": [{"name": "a", "status": "completed"}, {"name": "b", "status": "completed"}]},
    ]
    calls = {"n": 0}

    def fake_rest(path, **kw):
        data = snapshots[min(calls["n"], len(snapshots) - 1)]
        calls["n"] += 1
        return data

    monkeypatch.setattr(canary_run.gh, "rest", fake_rest)
    jobs = canary_run._collect_jobs("o/r", 1, sleep=lambda _s: None)
    assert calls["n"] == 2
    assert all(j["status"] == "completed" for j in jobs)


def test_collect_jobs_gives_up_after_bounded_attempts(monkeypatch):
    monkeypatch.setattr(
        canary_run.gh,
        "rest",
        lambda path, **kw: {"jobs": [{"name": "a", "status": "in_progress"}]},
    )
    jobs = canary_run._collect_jobs("o/r", 1, sleep=lambda _s: None)
    assert jobs == [{"name": "a", "status": "in_progress"}]


# ── CLI registration ─────────────────────────────────────────────────────────


def test_admin_canary_run_registered():
    from release_core.cli import admin

    assert "canary" in admin.group.commands
    assert "run" in admin.canary.group.commands
