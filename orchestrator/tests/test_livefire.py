"""Unit tests for orc livefire pure logic — no SDK, no network (release#663)."""

import textwrap

import pytest

from orchestrator import livefire

# ── load_prompt ───────────────────────────────────────────────────────────


def _doc(body: str) -> str:
    """A minimal live-fire-prompt.md with a 4-backtick text fence."""
    return f"# heading\n\nintro\n\n````text\n{body}\n````\n\n## after\n"


def test_load_prompt_extracts_text_fence(tmp_path):
    doc = tmp_path / "p.md"
    doc.write_text(_doc("Do the thing.\nStep 2."), encoding="utf-8")
    assert livefire.load_prompt(doc) == "Do the thing.\nStep 2."


def test_load_prompt_preserves_inner_yaml_fence(tmp_path):
    body = "Emit feedback:\n\n```yaml\nrepo: <owner/name>\n```"
    doc = tmp_path / "p.md"
    doc.write_text(_doc(body), encoding="utf-8")
    out = livefire.load_prompt(doc)
    assert "```yaml" in out and "repo: <owner/name>" in out


def test_load_prompt_raises_without_fence(tmp_path):
    doc = tmp_path / "p.md"
    doc.write_text("# no fence here\n", encoding="utf-8")
    with pytest.raises(livefire.LiveFireError):
        livefire.load_prompt(doc)


def test_load_prompt_real_doc():
    """The shipped doc must always yield a non-trivial prompt."""
    prompt = livefire.load_prompt()
    assert "coverage" in prompt.lower()
    assert "-release-rc" in prompt


# ── parse_feedback ────────────────────────────────────────────────────────

_TRANSCRIPT = textwrap.dedent(
    """\
    Here is my work. I improved coverage and opened a PR.

    ```yaml
    repo: arthur-debert/padz
    verdict: minor-friction
    pr: https://github.com/arthur-debert/padz/pull/42
    rc: v1.4.3-release-rc
    findings:
      - step: discovery
        component: how-to
        severity: friction
        what: could not find the coverage command
        expected: release-core how-to should mention it
      - step: commit
        component: gate
        severity: ok
        what: gate ran fine
        expected: nothing
    ```
    """
)


def test_parse_feedback_reads_last_yaml_block():
    fb = livefire.parse_feedback(_TRANSCRIPT)
    assert fb["repo"] == "arthur-debert/padz"
    assert fb["verdict"] == "minor-friction"
    assert len(fb["findings"]) == 2


def test_parse_feedback_picks_last_block():
    t = "```yaml\nx: 1\n```\n\nmore\n\n```yaml\nrepo: r\nverdict: clean\n```"
    assert livefire.parse_feedback(t)["repo"] == "r"


def test_parse_feedback_raises_without_block():
    with pytest.raises(livefire.LiveFireError):
        livefire.parse_feedback("no yaml here at all")


def test_parse_feedback_raises_on_non_mapping():
    with pytest.raises(livefire.LiveFireError):
        livefire.parse_feedback("```yaml\n- just\n- a\n- list\n```")


# ── findings_to_issues ────────────────────────────────────────────────────


def test_findings_to_issues_drops_ok_and_maps():
    fb = livefire.parse_feedback(_TRANSCRIPT)
    specs = livefire.findings_to_issues(fb, consumer="arthur-debert/padz")
    assert len(specs) == 1  # the ok one is dropped
    spec = specs[0]
    assert spec["component"] == "how-to"
    assert spec["title"].startswith("[how-to] live-fire (friction):")
    assert "arthur-debert/padz" in spec["body"]


def test_findings_to_issues_defaults_component():
    fb = {"findings": [{"severity": "blocker", "what": "boom"}]}
    specs = livefire.findings_to_issues(fb, consumer="o/n")
    assert specs[0]["component"] == "uncategorized"


def test_findings_to_issues_empty():
    assert livefire.findings_to_issues({}, consumer="o/n") == []
    assert livefire.findings_to_issues({"findings": None}, consumer="o/n") == []


# ── teardown_command ──────────────────────────────────────────────────────


def test_teardown_command_for_real_rc():
    cmd = livefire.teardown_command("o/n", "v1.4.3-release-rc")
    assert cmd == [
        "gh",
        "release",
        "delete",
        "v1.4.3-release-rc",
        "--repo",
        "o/n",
        "--yes",
        "--cleanup-tag",
    ]


@pytest.mark.parametrize("rc", [None, "", "none — blocked at step 3", "v1.2.3", "v1.2.3-rc.1"])
def test_teardown_command_skips_non_verification_tags(rc):
    assert livefire.teardown_command("o/n", rc) is None


# ── file_findings / teardown_rc dry-run (no subprocess) ───────────────────


def test_file_findings_dry_run_files_nothing(capsys):
    fb = livefire.parse_feedback(_TRANSCRIPT)
    filed = livefire.file_findings(fb, consumer="arthur-debert/padz", dry_run=True)
    assert len(filed) == 1
    assert "would file" in capsys.readouterr().err


def test_teardown_rc_dry_run(capsys):
    out = livefire.teardown_rc("o/n", "v1.4.3-release-rc", dry_run=True)
    assert out == "v1.4.3-release-rc"
    assert "would teardown" in capsys.readouterr().err


def test_teardown_rc_nothing_to_do():
    assert livefire.teardown_rc("o/n", None, dry_run=True) is None
