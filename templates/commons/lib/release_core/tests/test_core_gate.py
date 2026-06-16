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
import subprocess

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


def test_ansi_escapes_stripped_when_not_a_tty(tmp_path, capsys):
    # lefthook hardcodes a bold meta-header + a truecolor summary rule that no
    # color knob suppresses; the gate strips them on passthrough when stdout is
    # not a TTY (capsys), so captured / CI / agent logs stay clean.
    # SGR color/bold AND a non-SGR CSI (erase-line) — all must be stripped.
    noisy = "\x1b[1mpre-commit\x1b[m\n\x1b[2K\x1b[38;2;56;56;56m  ----\x1b[m\n" + _SUMMARY_PASS
    rc = _run(tmp_path, stdout=noisy, exit_code=0)
    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b" not in out  # no escape sequence of any kind leaks
    assert "pre-commit" in out  # the underlying text survives the strip
    assert out.strip().splitlines()[-1] == "GATE: OK (2 checks)"


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
    _run(tmp_path, stdout=_HEAD + "✓ ruff (0.00 seconds)\n", exit_code=0)
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "GATE: OK (1 check)"


def test_glyphs_before_the_summary_header_are_not_counted(tmp_path, capsys):
    # A tool's own output that starts with ✓/✗ (before lefthook's summary block)
    # must NOT be parsed as a check result — release#628 review.
    stdout = "✓ this is tool output, not a check\n" + _HEAD + "✓ ruff (0.00 seconds)\n"
    _run(tmp_path, stdout=stdout, exit_code=0)
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "GATE: OK (1 check)"


def test_verdict_failed_without_names_falls_back(tmp_path, capsys):
    rc = _run(tmp_path, stdout="boom\n", exit_code=2)
    out = capsys.readouterr().out
    assert rc == 2
    assert out.strip().splitlines()[-1] == "GATE: FAILED (see output above)"


def test_glyph_parsing_tolerates_ansi_escapes(tmp_path, capsys):
    # Defensive: a leading SGR escape before the glyph must not defeat the parse.
    stdout = _HEAD + "\x1b[32m✓ ruff (0.00 seconds)\x1b[m\n"
    _run(tmp_path, stdout=stdout, exit_code=0)
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "GATE: OK (1 check)"


def test_unspawnable_lefthook_still_prints_a_verdict(tmp_path, capsys):
    # A present-but-not-executable lefthook raises OSError (PermissionError) on
    # spawn; the gate must still emit GATE: FAILED, not crash (release#628 review).
    not_exec = tmp_path / "lefthook"
    not_exec.write_text("#!/bin/sh\n")  # NOT chmod +x
    rc = gate._run_and_summarize([str(not_exec)], str(tmp_path), dict(os.environ), quiet=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert out.strip().splitlines()[-1] == "GATE: FAILED (lefthook unavailable)"


def test_verdict_is_standalone_when_output_lacks_trailing_newline(tmp_path, capsys):
    # lefthook's final chunk without a trailing newline must not get the verdict
    # glued onto it — release#628 review.
    _run(tmp_path, stdout=_HEAD + "✓ ruff (0.00 seconds)\nno trailing newline here", exit_code=0)
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "GATE: OK (1 check)"
    assert lines[-2] == "no trailing newline here"


# --- root lefthook stub: defend the gate model against `lefthook install` ----
# (release#714). A stock npm/pnpm `prepare: lefthook install` step otherwise
# creates an empty starter lefthook.yml at the root and can re-open the
# release#567 ungated-commit boot hole. `--install-hook` writes a hook-less,
# git-excluded redirect stub so `lefthook install` finds a config (no starter)
# and leaves our binary-driven hook untouched.


def _git_init(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)


def test_install_hook_writes_the_root_stub_in_a_ws3_consumer(tmp_path):
    _git_init(tmp_path)
    rc = gate._install_hook(str(tmp_path))
    assert rc == 0
    stub = tmp_path / "lefthook.yml"
    assert stub.is_file()
    assert gate._STUB_MARKER in stub.read_text()
    # The stub declares NO hooks (no top-level `<hook>:` key) — so `lefthook
    # install` won't sync/clobber the binary-driven pre-commit hook.
    active = [line for line in stub.read_text().splitlines() if line and not line.startswith("#")]
    assert active == ["colors: false"]


def test_install_hook_git_excludes_the_stub(tmp_path):
    _git_init(tmp_path)
    gate._install_hook(str(tmp_path))
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text().splitlines()
    assert "lefthook.yml" in [line.strip() for line in exclude]


def test_install_hook_does_not_clobber_a_real_root_gate(tmp_path):
    # release-self / not-yet-migrated repo: a hand-authored root lefthook.yml must
    # never be overwritten by the stub.
    _git_init(tmp_path)
    real = tmp_path / "lefthook.yml"
    real.write_text("pre-commit:\n  commands:\n    ruff:\n      run: ruff check .\n")
    gate._install_hook(str(tmp_path))
    assert real.read_text().startswith("pre-commit:")
    assert gate._STUB_MARKER not in real.read_text()


def test_real_root_config_present_ignores_the_stub(tmp_path):
    # The stub is NOT a real config — it declares no hooks.
    (tmp_path / "lefthook.yml").write_text(gate._STUB_BODY)
    assert gate._real_root_config_present(str(tmp_path)) is False


def test_real_root_config_present_true_for_authored_config(tmp_path):
    (tmp_path / "lefthook.yml").write_text("pre-commit:\n  commands: {}\n")
    assert gate._real_root_config_present(str(tmp_path)) is True


def test_git_exclude_is_idempotent(tmp_path):
    _git_init(tmp_path)
    gate._git_exclude(str(tmp_path), "lefthook.yml")
    gate._git_exclude(str(tmp_path), "lefthook.yml")
    lines = [
        line.strip() for line in (tmp_path / ".git" / "info" / "exclude").read_text().splitlines()
    ]
    assert lines.count("lefthook.yml") == 1


def test_unbuilt_gate_fails_loud_even_with_the_stub_present(tmp_path, capsys, monkeypatch):
    # The boot hole (release#567) must stay closed: with NO .release/lefthook.yml
    # and only our hook-less stub at the root, the gate must FAIL LOUD, not
    # discover the stub and warn-and-pass an ungated commit (release#714).
    _git_init(tmp_path)
    (tmp_path / "lefthook.yml").write_text(gate._STUB_BODY)
    monkeypatch.chdir(tmp_path)
    # A lefthook on PATH so resolution gets past the "no runner" branch; the
    # config guard must fire before it's ever invoked. A pure /bin/sh fake (no
    # interpreter dependency) so it resolves even when we prepend our bindir.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "lefthook"
    fake.write_text("#!/bin/sh\necho 'lefthook version 0.0.0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("LEFTHOOK_VERSION", "0.0.0")
    rc = gate.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "GATE: FAILED (gate config not built)" in out
