"""gate verb — the GATE: verdict line + --quiet behavior (release#628).

The verdict line is derived from lefthook's exit code (authoritative); the
parenthetical detail is best-effort parsed from lefthook's NO_COLOR summary block
("<glyph> <name> (<time>)", ✓/✔ pass, ✗/✖ fail). These tests drive
_run_and_summarize against a fake lefthook that emits a chosen summary + exit code
— no real lefthook, no network.
"""

from __future__ import annotations

import os
import stat

from release_core.verbs import gate

# A realistic lefthook NO_COLOR summary block (the bytes the real binary emits).
_HEAD = "summary: (done in 0.01 seconds)\n"
_SUMMARY = _HEAD + "✓ ruff (0.00 seconds)\n✗ shellcheck (0.00 seconds)\n"
_SUMMARY_PASS = _HEAD + "✓ ruff (0.00 seconds)\n✓ prettier (0.00 seconds)\n"


def _fake_lefthook(tmp_path, *, stdout: str, exit_code: int) -> str:
    """A fake lefthook executable that prints `stdout` then exits `exit_code`."""
    path = tmp_path / "fake-lefthook"
    body = (
        f"#!/usr/bin/env python3\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({exit_code})\n"
    )
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _run(tmp_path, *, stdout, exit_code, quiet=False):
    cmd = [_fake_lefthook(tmp_path, stdout=stdout, exit_code=exit_code)]
    return gate._run_and_summarize(cmd, str(tmp_path), dict(os.environ), quiet=quiet)


def test_verdict_ok_counts_passed_checks(tmp_path, capsys):
    rc = _run(tmp_path, stdout=_SUMMARY_PASS, exit_code=0)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().splitlines()[-1] == "GATE: OK (2 checks)"


def test_verdict_failed_names_failing_check(tmp_path, capsys):
    rc = _run(tmp_path, stdout=_SUMMARY, exit_code=1)
    out = capsys.readouterr().out
    assert rc == 1
    assert out.strip().splitlines()[-1] == "GATE: FAILED (shellcheck)"


def test_verdict_is_the_final_line_after_full_output(tmp_path, capsys):
    # Non-quiet: lefthook's output is streamed through, THEN the verdict — so a
    # `| tail` view still ends on the truth.
    rc = _run(tmp_path, stdout=_SUMMARY_PASS, exit_code=0)
    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ ruff" in out  # full output preserved
    assert out.strip().splitlines()[-1].startswith("GATE: OK")


def test_quiet_success_prints_only_the_verdict(tmp_path, capsys):
    rc = _run(tmp_path, stdout=_SUMMARY_PASS, exit_code=0, quiet=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "GATE: OK (2 checks)"
    assert "summary:" not in out  # the per-check noise is suppressed


def test_quiet_failure_dumps_full_output_then_verdict(tmp_path, capsys):
    rc = _run(tmp_path, stdout=_SUMMARY, exit_code=1, quiet=True)
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗ shellcheck" in out  # failures are NOT suppressed
    assert out.strip().splitlines()[-1] == "GATE: FAILED (shellcheck)"


def test_verdict_degrades_gracefully_without_parseable_summary(tmp_path, capsys):
    # A lefthook whose summary format we can't parse still gets a truthful verdict.
    rc = _run(tmp_path, stdout="some unrecognized output\n", exit_code=0)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().splitlines()[-1] == "GATE: OK (all checks)"


def test_verdict_singular_check(tmp_path, capsys):
    _run(tmp_path, stdout="✓ ruff (0.00 seconds)\n", exit_code=0)
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "GATE: OK (1 check)"


def test_verdict_failed_without_names_falls_back(tmp_path, capsys):
    rc = _run(tmp_path, stdout="boom\n", exit_code=2)
    out = capsys.readouterr().out
    assert rc == 2
    assert out.strip().splitlines()[-1] == "GATE: FAILED (see output above)"


def test_glyph_parsing_tolerates_ansi_escapes(tmp_path, capsys):
    # Defensive: a leading SGR escape before the glyph must not defeat the parse.
    stdout = "\x1b[32m✓ ruff (0.00 seconds)\x1b[m\n"
    _run(tmp_path, stdout=stdout, exit_code=0)
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "GATE: OK (1 check)"
