"""canary_run verb — the pure seams (#587 slice 1).

Fully offline: the ref-rewrite, prerelease-version computation, arg
validation, and the `canaries:` manifest block (including the invariant that
the `projects:` sweep does NOT include it). The INFRA/PROJECT classification,
report rows/rendering, and the jobs-endpoint settle re-poll moved to the
shared classifier (release_core.classify; #594/#595) and are covered by
test_core_classify.py. The gh/git glue is exercised by the live canary
rounds, not here.
"""

from __future__ import annotations

import os

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
    opts = canary_run._parse_args(["--ref", "main", "--family", "rust, vscode-ext"])
    assert isinstance(opts, dict)
    assert opts["families"] == ["rust", "vscode-ext"]


# ── multi-family round (#605) ────────────────────────────────────────────────
#
# The wiring that lets rust + vscode-ext share one round: the candidate
# branch is published ONCE, every family is seeded + dispatched BEFORE any
# polling starts (the GH runs execute concurrently), and each family gets
# its own classified verdict + canary/<family> commit status.

TWO_FAMILY_REGISTRY = {
    "rust": "arthur-debert/release-canary-rust",
    "vscode-ext": "arthur-debert/release-canary-vscode-ext",
}


@pytest.fixture
def round_seams(tmp_path, monkeypatch):
    """A two-canary registry + every gh/git seam of main() stubbed, recording
    the order of phase events. The registry accessor is stubbed directly
    (yamlio shells out via proc, which this fixture also stubs)."""
    import types

    monkeypatch.setattr(
        canary_run.managed_repos, "canaries", lambda manifest=None: dict(TWO_FAMILY_REGISTRY)
    )

    events: list[str] = []
    monkeypatch.setattr(canary_run.proc, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        canary_run.gh, "repo_clone", lambda repo, dest: types.SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(canary_run, "_resolve_ref", lambda d, r: "a" * 40)
    monkeypatch.setattr(canary_run, "_inflight_run", lambda repo, sha12: None)
    monkeypatch.setattr(
        canary_run,
        "_publish_candidate",
        lambda *a: (events.append("publish"), 3)[1],
    )
    monkeypatch.setattr(
        canary_run,
        "_seed_canary",
        lambda **kw: (events.append(f"seed:{kw['family']}"), str(tmp_path / kw["family"]))[1],
    )
    monkeypatch.setattr(canary_run, "_retry", lambda fn, **kw: [])  # tag list
    monkeypatch.setattr(
        canary_run,
        "_dispatch",
        lambda repo, dest, version: (events.append(f"dispatch:{repo}"), ("f" * 40, set()))[1],
    )
    monkeypatch.setattr(
        canary_run,
        "_resolve_runs",
        lambda repo, seed_sha, before, deadline: (
            events.append(f"poll:{repo}"),
            {"ci": {"id": 1}, "release": {"id": 2}},
        )[1],
    )
    monkeypatch.setattr(
        canary_run,
        "_poll_to_completion",
        lambda repo, runs, deadline: {
            "ci": {"id": 1, "conclusion": "success", "html_url": "ci-url"},
            "release": {"id": 2, "conclusion": "success", "html_url": "cut-url"},
        },
    )
    monkeypatch.setattr(canary_run.classify, "collect_jobs", lambda repo, rid, prefix: [])
    monkeypatch.setattr(
        canary_run.classify, "job_rows", lambda family, wf, jobs, conclusion: ([], False)
    )
    statuses: list[str] = []
    monkeypatch.setattr(
        canary_run,
        "_post_commit_status",
        lambda sha, family, **kw: statuses.append(f"{family}:{kw['success']}"),
    )
    monkeypatch.setattr(canary_run, "_cleanup_prereleases", lambda repo, keep: [])
    return events, statuses, str(tmp_path / "root")


def test_two_families_dispatch_before_any_poll(round_seams):
    events, statuses, root = round_seams
    assert canary_run.main(["--ref", "main", "--root", root]) == 0
    # ONE branch publish for the whole round, regardless of family count.
    assert events.count("publish") == 1
    # Both families dispatched BEFORE the first poll — the GH rounds overlap.
    dispatch_idx = [i for i, e in enumerate(events) if e.startswith("dispatch:")]
    poll_idx = [i for i, e in enumerate(events) if e.startswith("poll:")]
    assert len(dispatch_idx) == 2 and len(poll_idx) == 2
    assert max(dispatch_idx) < min(poll_idx)
    # One commit status per family.
    assert statuses == ["rust:True", "vscode-ext:True"]


def test_family_flag_restricts_the_round(round_seams):
    events, statuses, root = round_seams
    assert canary_run.main(["--ref", "main", "--family", "vscode-ext", "--root", root]) == 0
    assert events == [
        "publish",
        "seed:vscode-ext",
        "dispatch:arthur-debert/release-canary-vscode-ext",
        "poll:arthur-debert/release-canary-vscode-ext",
    ]
    assert statuses == ["vscode-ext:True"]


def test_one_family_dispatch_failure_does_not_stop_the_other(round_seams, monkeypatch, capsys):
    events, statuses, root = round_seams

    def seed(**kw):
        if kw["family"] == "rust":
            raise canary_run.CanaryError("seed exploded")
        events.append(f"seed:{kw['family']}")
        return root

    monkeypatch.setattr(canary_run, "_seed_canary", seed)
    # An incomplete round is a setup error (exit 2), but the healthy family
    # still ran to its verdict.
    assert canary_run.main(["--ref", "main", "--root", root]) == 2
    assert statuses == ["vscode-ext:True"]
    out = capsys.readouterr().out
    assert "rust: SETUP ERROR" in out and "verdict: ERROR" in out


# ── sandbox env hermeticity ──────────────────────────────────────────────────


def test_sandbox_env_strips_checkout_and_release_vars(monkeypatch, tmp_path):
    # The sandbox must boot from the CANDIDATE wheel, never the operator's
    # checkout: the bin/release-core shim re-execs with PYTHONPATH pinned to
    # the checkout lib, and an inherited PYTHONPATH makes the sandbox venv's
    # python shadow the candidate wheel's package (the checkout never carries
    # _bundled_templates, so init dies with "no bundled templates" — caught
    # live as a deterministic canary setup failure pre-v3).
    for var in canary_run._SANDBOX_STRIP:
        monkeypatch.setenv(var, "/somewhere/checkout")
    monkeypatch.setenv("KEEP_ME", "1")
    env = canary_run._sandbox_env(str(tmp_path))
    for var in canary_run._SANDBOX_STRIP:
        assert var not in env
    assert env["KEEP_ME"] == "1"
    assert env["XDG_DATA_HOME"] == str(tmp_path / "xdg" / "data")
    assert env["XDG_BIN_HOME"] == str(tmp_path / "xdg" / "bin")
    assert env["PATH"].startswith(env["XDG_BIN_HOME"] + os.pathsep)


# ── CLI registration ─────────────────────────────────────────────────────────


def test_admin_canary_run_registered():
    from release_core.cli import admin

    assert "canary" in admin.group.commands
    assert "run" in admin.canary.group.commands
