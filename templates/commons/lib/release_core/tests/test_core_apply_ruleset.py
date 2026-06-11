"""apply_ruleset verb — payload construction + job-name inference + ruleset lookup.

The byte-for-byte parity with the old `yq|jq` payload is the load-bearing
guarantee, so the template is loaded from the real rulesets/main-protection.json.tmpl
and the built dict is asserted field-by-field. The gh hops are exercised by
monkeypatching gh.rest with recorded JSON (mock at the data layer — never live).
"""

from __future__ import annotations

import json
import os

from release_core import gh, yamlio
from release_core.verbs import apply_ruleset

_TMPL = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "..",
    "..",
    "..",
    "..",
    "..",
    "rulesets",
    "main-protection.json.tmpl",
)


_ROOT = os.path.dirname(os.path.dirname(_TMPL))  # repo root, parent of rulesets/


def _template() -> dict:
    with open(_TMPL, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# Pure payload construction
# --------------------------------------------------------------------------


def test_checks_json_wraps_and_drops_empties_preserving_order():
    assert apply_ruleset.checks_json(["b", "", "a"]) == [{"context": "b"}, {"context": "a"}]


def test_build_payload_injects_contexts_into_required_status_checks():
    body = apply_ruleset.build_payload(_template(), ["Test", "Build (x)"])
    rule = next(r for r in body["rules"] if r["type"] == "required_status_checks")
    assert rule["parameters"]["required_status_checks"] == [
        {"context": "Test"},
        {"context": "Build (x)"},
    ]
    # other rules untouched
    assert any(r["type"] == "pull_request" for r in body["rules"])
    assert body["name"] == "main-branch-protection"


def test_build_payload_does_not_mutate_the_template():
    tmpl = _template()
    apply_ruleset.build_payload(tmpl, ["X"])
    rule = next(r for r in tmpl["rules"] if r["type"] == "required_status_checks")
    assert rule["parameters"]["required_status_checks"] == []


def test_build_payload_matches_recorded_jq_bytes():
    """The crux: json.dumps(body, indent=2) must equal the old jq output bytes.

    The jq reference (jq . over the yq|jq-built payload) was recorded in the PR
    via `diff`; here we re-derive the canonical 2-space form and assert the
    contexts array is the only thing that changed vs the template — i.e. the
    structural carry-through that made the diff empty.
    """
    checks = ["Build (aarch64-apple-darwin)", "Test", "bats-e2e"]
    body = apply_ruleset.build_payload(_template(), checks)
    dumped = json.dumps(body, indent=2)
    # jq emits 2-space indent, ": " separators, no trailing newline, UTF-8 literal.
    reparsed = json.loads(dumped)
    assert reparsed == body
    rule = next(r for r in reparsed["rules"] if r["type"] == "required_status_checks")
    assert [c["context"] for c in rule["parameters"]["required_status_checks"]] == checks


# --------------------------------------------------------------------------
# Workflow trigger inference (the yq->python `on:` normalization)
# --------------------------------------------------------------------------


def test_workflow_triggers_string_array_object_other():
    assert apply_ruleset.workflow_triggers({"on": "push"}) == ["push"]
    assert apply_ruleset.workflow_triggers({"on": ["push", "pull_request"]}) == [
        "push",
        "pull_request",
    ]
    assert apply_ruleset.workflow_triggers({"on": {"pull_request": None, "push": None}}) == [
        "pull_request",
        "push",
    ]
    assert apply_ruleset.workflow_triggers({"on": 5}) == []
    assert apply_ruleset.workflow_triggers("not a dict") == []


def test_is_pr_workflow():
    assert apply_ruleset.is_pr_workflow({"on": {"pull_request": None}}) is True
    assert apply_ruleset.is_pr_workflow({"on": "push"}) is False


def test_pr_trigger_is_path_filtered():
    # Unfiltered pull_request → always-run gate, not filtered.
    assert apply_ruleset.pr_trigger_is_path_filtered({"on": {"pull_request": None}}) is False
    assert apply_ruleset.pr_trigger_is_path_filtered({"on": {"pull_request": {}}}) is False
    # A `paths:` filter makes it conditional.
    assert (
        apply_ruleset.pr_trigger_is_path_filtered(
            {"on": {"pull_request": {"paths": ["tests/changelog/**"]}}}
        )
        is True
    )
    # `paths-ignore:` is equally conditional.
    assert (
        apply_ruleset.pr_trigger_is_path_filtered(
            {"on": {"pull_request": {"paths-ignore": ["docs/**"]}}}
        )
        is True
    )
    # String / array `on:` can't carry a path filter → unfiltered.
    assert apply_ruleset.pr_trigger_is_path_filtered({"on": "pull_request"}) is False
    assert apply_ruleset.pr_trigger_is_path_filtered({"on": ["push", "pull_request"]}) is False
    assert apply_ruleset.pr_trigger_is_path_filtered("not a dict") is False


def test_pr_workflow_paths_excludes_path_filtered_workflows(tmp_path, monkeypatch):
    # Two PR workflows: one always-run (the gate), one path-filtered (a bats
    # suite). Only the unfiltered one should contribute a required check —
    # regression for the release#416 conditional-check deadlock. yamlio.load is
    # monkeypatched (the suite has no yq) to return the parsed doc per file.
    wf = tmp_path / "workflows"
    wf.mkdir()
    (wf / "ci.yml").write_text("placeholder")
    (wf / "changelog-tests.yml").write_text("placeholder")

    docs = {
        "ci.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}},
        "changelog-tests.yml": {
            "on": {"pull_request": {"paths": ["tests/changelog/**"]}},
            "jobs": {"bats": {}},
        },
    }
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: docs[os.path.basename(path)])
    assert apply_ruleset._pr_workflow_paths(str(wf)) == [".github/workflows/ci.yml"]


# --------------------------------------------------------------------------
# Malformed-YAML handling: yamlio.load raises yamlio.YamlError (NOT
# proc.ProcError, since _yq calls proc.run(check=False)). Both yaml-reading
# sites must swallow YamlError and skip the file, matching the bash's clean
# skip rather than crashing with a traceback. Regression for PR #392 review.
# --------------------------------------------------------------------------


def test_pr_workflow_paths_skips_malformed_yaml(tmp_path, monkeypatch):
    wf = tmp_path / "workflows"
    wf.mkdir()
    (wf / "broken.yml").write_text("this: : is: not: valid\n")

    def boom(_path):
        raise yamlio.YamlError("yq parse failure")

    monkeypatch.setattr(apply_ruleset.yamlio, "load", boom)
    # No exception, broken file simply contributes no path.
    assert apply_ruleset._pr_workflow_paths(str(wf)) == []


def test_checks_from_workflows_skips_malformed_yaml(monkeypatch):
    def boom(_path):
        raise yamlio.YamlError("yq parse failure")

    monkeypatch.setattr(apply_ruleset.yamlio, "load", boom)
    assert apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"]) == []


# --------------------------------------------------------------------------
# Static detection — reusable-workflow callers resolve to nested contexts
# (release#602). The fleet's consumers are thin callers (one job `uses:` a
# release reusable workflow), and the bare caller-job name is NEVER a reported
# context — only `<caller-job> / <called-job>` is. yamlio.load/loads are
# monkeypatched (the suite has no yq); the contents-API hop is mocked at the
# gh.rest data layer with base64 payloads, exercising the real decode path.
# --------------------------------------------------------------------------


def _b64_doc(doc) -> str:
    import base64

    return base64.b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")


_PADZ_CALLER = {
    "on": {"push": {"branches": ["**"]}, "pull_request": None},
    "jobs": {
        "ci": {
            "uses": "arthur-debert/release/.github/workflows/rust-ci.yml@v2",
            "with": {"binary-name": "padz", "bats": True},
        }
    },
}

_RUST_CI = {
    "on": {"workflow_call": {"inputs": {"bats": {"type": "boolean", "default": False}}}},
    "jobs": {
        "check": {"runs-on": "ubuntu-latest"},
        "e2e": {"if": "inputs.bats", "runs-on": "ubuntu-latest"},
    },
}


def _route_contents(monkeypatch, routes: dict):
    """gh.rest stub serving contents-API payloads; yamlio.loads decodes the
    JSON text (JSON is YAML — the b64 decode path runs for real)."""

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path not in routes:
            raise gh.GhError(f"404: {path}")
        return {"content": _b64_doc(routes[path])}

    monkeypatch.setattr(gh, "rest", fake_rest)
    monkeypatch.setattr(apply_ruleset.yamlio, "loads", json.loads)


def test_checks_from_workflows_padz_ground_truth(monkeypatch):
    # padz's real ruleset is the validation target: `ci / check`, `ci / e2e`
    # (bats: true enables the `if: inputs.bats` job) — never the bare `ci`.
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: _PADZ_CALLER)
    _route_contents(
        monkeypatch,
        {"repos/arthur-debert/release/contents/.github/workflows/rust-ci.yml?ref=v2": _RUST_CI},
    )
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == ["ci / check", "ci / e2e"]


def test_checks_from_workflows_excludes_input_gated_job_when_not_enabled(monkeypatch):
    # Same caller without `bats: true` → the `if: inputs.bats` job never runs
    # for this consumer and must not be required.
    caller = {
        "on": {"pull_request": None},
        "jobs": {
            "ci": {
                "uses": "arthur-debert/release/.github/workflows/rust-ci.yml@v2",
                "with": {"binary-name": "padz"},
            }
        },
    }
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: caller)
    _route_contents(
        monkeypatch,
        {"repos/arthur-debert/release/contents/.github/workflows/rust-ci.yml?ref=v2": _RUST_CI},
    )
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == ["ci / check"]


def test_checks_from_workflows_mixed_plain_and_caller_jobs(monkeypatch):
    # A plain job reports its display name (static `name:` override wins; a
    # `${{ … }}` name falls back to the job ID); a caller job reports nested
    # contexts. Both coexist in one workflow.
    caller = {
        "on": {"pull_request": None},
        "jobs": {
            "lint": {"name": "Lint (fast)"},
            "dyn": {"name": "build-${{ matrix.os }}"},
            "ci": {"uses": "o/r/.github/workflows/inner.yml@v1"},
        },
    }
    inner = {"jobs": {"check": {}}}
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: caller)
    _route_contents(monkeypatch, {"repos/o/r/contents/.github/workflows/inner.yml?ref=v1": inner})
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == ["Lint (fast)", "ci / check", "dyn"]


def test_checks_from_workflows_recurses_nested_reusable_calls(monkeypatch):
    # A called workflow that itself calls a reusable workflow reports the full
    # ancestry: `a / b / c`.
    caller = {
        "on": {"pull_request": None},
        "jobs": {"a": {"uses": "o/r/.github/workflows/mid.yml@v1"}},
    }
    mid = {"jobs": {"b": {"uses": "o/r/.github/workflows/leaf.yml@v1"}}}
    leaf = {"jobs": {"c": {}}}
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: caller)
    _route_contents(
        monkeypatch,
        {
            "repos/o/r/contents/.github/workflows/mid.yml?ref=v1": mid,
            "repos/o/r/contents/.github/workflows/leaf.yml?ref=v1": leaf,
        },
    )
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == ["a / b / c"]


def test_checks_from_workflows_self_reference_terminates_at_nesting_cap(monkeypatch, capsys):
    # A pathological self-referencing workflow must terminate (GitHub's own
    # nesting cap bounds the recursion) and contribute nothing, with a warning.
    caller = {
        "on": {"pull_request": None},
        "jobs": {"a": {"uses": "o/r/.github/workflows/self.yml@v1"}},
    }
    selfdoc = {"jobs": {"again": {"uses": "o/r/.github/workflows/self.yml@v1"}}}
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: caller)
    _route_contents(monkeypatch, {"repos/o/r/contents/.github/workflows/self.yml?ref=v1": selfdoc})
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == []
    assert "nesting too deep" in capsys.readouterr().err


def test_checks_from_workflows_unresolvable_uses_warns_and_skips(monkeypatch, capsys):
    # A fetch failure (or a malformed reference) must NOT degrade to the bare
    # caller name — that context is never reported and would deadlock every PR.
    caller = {
        "on": {"pull_request": None},
        "jobs": {
            "ci": {"uses": "o/r/.github/workflows/gone.yml@v1"},
            "noref": {"uses": "o/r/.github/workflows/x.yml"},  # missing @ref
        },
    }
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: caller)
    _route_contents(monkeypatch, {})  # every fetch 404s
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == []
    err = capsys.readouterr().err
    assert "cannot resolve reusable workflow" in err
    assert "ci" not in out and "noref" not in out


def test_checks_from_workflows_resolves_repo_local_uses_from_working_tree(monkeypatch):
    # `uses: ./.github/workflows/inner.yml` is read from the same working tree
    # the caller was read from — no API hop for the repo's own files.
    docs = {
        "ci.yml": {
            "on": {"pull_request": None},
            "jobs": {"ci": {"uses": "./.github/workflows/inner.yml"}},
        },
        "inner.yml": {"jobs": {"check": {}, "package": {}}},
    }
    monkeypatch.setattr(apply_ruleset.yamlio, "load", lambda path: docs[os.path.basename(path)])
    out = apply_ruleset._checks_from_workflows("/top", [".github/workflows/ci.yml"])
    assert out == ["ci / check", "ci / package"]


def test_called_job_included_resolves_simple_inputs_conditions():
    fn = apply_ruleset._called_job_included
    assert fn({}, {}) is True  # no `if:` → always runs
    assert fn({"if": "inputs.bats"}, {"bats": True}) is True
    assert fn({"if": "inputs.bats"}, {}) is False
    assert fn({"if": "${{ inputs.e2e }}"}, {"e2e": "true"}) is True
    assert fn({"if": "${{ inputs.e2e }}"}, {"e2e": False}) is False
    # Unresolvable expressions are included: a job-level skip still reports a
    # (satisfying) check run, unlike a never-reporting path-filtered workflow.
    assert fn({"if": "github.event_name == 'push'"}, {}) is True
    assert fn({"if": "inputs.bats == true"}, {"bats": True}) is True


def test_job_display_name_static_override_else_id():
    assert apply_ruleset.job_display_name("check", {}) == "check"
    assert apply_ruleset.job_display_name("check", {"name": "Check (fast)"}) == "Check (fast)"
    assert apply_ruleset.job_display_name("b", {"name": "x-${{ matrix.os }}"}) == "b"
    assert apply_ruleset.job_display_name("j", "not a dict") == "j"


# --------------------------------------------------------------------------
# Existing-ruleset lookup
# --------------------------------------------------------------------------


def test_existing_ruleset_id_first_match_or_none():
    rs = [
        {"id": 1, "name": "other"},
        {"id": 7, "name": "main-branch-protection"},
        {"id": 9, "name": "main-branch-protection"},
    ]
    assert apply_ruleset._existing_ruleset_id(rs, "main-branch-protection") == 7
    assert apply_ruleset._existing_ruleset_id(rs, "nope") is None
    assert apply_ruleset._existing_ruleset_id(None, "x") is None


# --------------------------------------------------------------------------
# Job-name inference from recorded runs/jobs JSON (mock gh.rest at data layer)
# --------------------------------------------------------------------------


def test_checks_from_runs_collects_sorted_unique_job_names(monkeypatch):
    routes = {
        "repos/o/r/actions/workflows": {
            "workflows": [{"path": ".github/workflows/ci.yml", "id": 100}]
        },
        "repos/o/r/actions/workflows/100/runs?branch=main&per_page=1": {
            "workflow_runs": [{"id": 555}]
        },
        "repos/o/r/actions/runs/555/jobs": [
            {"name": "Test"},
            {"name": "Build (aarch64-apple-darwin)"},
            {"name": "Test"},
        ],
    }

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        return routes[path]

    monkeypatch.setattr(gh, "rest", fake_rest)
    out = apply_ruleset._checks_from_runs("o/r", "main", [".github/workflows/ci.yml"])
    assert out == ["Build (aarch64-apple-darwin)", "Test"]


def test_checks_from_runs_skips_workflows_without_runs(monkeypatch):
    routes = {
        "repos/o/r/actions/workflows": {
            "workflows": [{"path": ".github/workflows/ci.yml", "id": 100}]
        },
        "repos/o/r/actions/workflows/100/runs?branch=main&per_page=1": {"workflow_runs": []},
    }

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path not in routes:
            raise gh.GhError("404")
        return routes[path]

    monkeypatch.setattr(gh, "rest", fake_rest)
    assert apply_ruleset._checks_from_runs("o/r", "main", [".github/workflows/ci.yml"]) == []


# --------------------------------------------------------------------------
# main() dispatch — dry-run prints the payload, never sends; PUT vs POST routing
# --------------------------------------------------------------------------


def test_main_dry_run_with_checks_override_prints_payload_no_send(monkeypatch, capsys):
    monkeypatch.setattr(apply_ruleset, "_current_repo", lambda: "o/r")
    monkeypatch.setattr(apply_ruleset, "_release_root", lambda: _ROOT)

    sent = []

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path == "repos/o/r/rulesets" and method is None:
            return [{"id": 7, "name": "main-branch-protection"}]
        sent.append((path, method))
        return None

    monkeypatch.setattr(gh, "rest", fake_rest)
    rc = apply_ruleset.main(["--dry-run", "--checks", "Test,Build (x)"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == []  # nothing PUT/POSTed
    assert "repo:    o/r" in out
    assert "ruleset: main-branch-protection (existing id: 7)" in out
    assert "checks:  Test,Build (x)" in out
    assert "--- payload (dry-run, not sent) ---" in out
    # the dumped payload carries the override checks in order
    payload = json.loads(out.split("--- payload (dry-run, not sent) ---\n", 1)[1])
    rule = next(r for r in payload["rules"] if r["type"] == "required_status_checks")
    assert [c["context"] for c in rule["parameters"]["required_status_checks"]] == [
        "Test",
        "Build (x)",
    ]


def test_main_creates_when_no_existing_ruleset(monkeypatch, capsys):
    monkeypatch.setattr(apply_ruleset, "_current_repo", lambda: "o/r")
    monkeypatch.setattr(apply_ruleset, "_release_root", lambda: _ROOT)
    sent = []

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path == "repos/o/r/rulesets" and method is None:
            return []
        sent.append((path, method, body is not None))
        return None

    monkeypatch.setattr(gh, "rest", fake_rest)
    rc = apply_ruleset.main(["--checks", "Test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == [("repos/o/r/rulesets", "POST", True)]
    assert out.rstrip().endswith("created")


def test_main_updates_when_existing_ruleset(monkeypatch, capsys):
    monkeypatch.setattr(apply_ruleset, "_current_repo", lambda: "o/r")
    monkeypatch.setattr(apply_ruleset, "_release_root", lambda: _ROOT)
    sent = []

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path == "repos/o/r/rulesets" and method is None:
            return [{"id": 42, "name": "main-branch-protection"}]
        sent.append((path, method, body is not None))
        return None

    monkeypatch.setattr(gh, "rest", fake_rest)
    rc = apply_ruleset.main(["--checks", "Test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sent == [("repos/o/r/rulesets/42", "PUT", True)]
    assert out.rstrip().endswith("updated")


def test_main_no_checks_determinable_exits_1(monkeypatch, capsys, tmp_path):
    # Empty override falls through to auto-detect (matching the bash `[ -n ]`
    # guard). With a workflows dir that has no PR-triggered workflow, no runs,
    # and no yq fallback, checks stay empty → the same error + exit 1.
    monkeypatch.setattr(apply_ruleset, "_current_repo", lambda: "o/r")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(apply_ruleset.gh, "repo_root", lambda: str(tmp_path))

    def fake_rest(path, *, method=None, fields=None, body=None, paginate=False):
        if path == "repos/o/r":
            return {"default_branch": "main"}
        raise gh.GhError("404")

    monkeypatch.setattr(gh, "rest", fake_rest)
    rc = apply_ruleset.main(["--checks", ""])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no required checks" in err


def test_main_no_workflows_dir_exits_1(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(apply_ruleset, "_current_repo", lambda: "o/r")
    monkeypatch.setattr(apply_ruleset.gh, "repo_root", lambda: str(tmp_path))
    rc = apply_ruleset.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no .github/workflows dir" in err


def test_main_help_exits_0_and_prints_usage(capsys):
    rc = apply_ruleset.main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage:" in out
    assert "apply-ruleset" in out
    assert "Shell→Python" not in out  # the migration note is stripped from --help


def test_main_unknown_flag_is_usage_error(capsys):
    rc = apply_ruleset.main(["--nope"])
    assert rc == 64
