"""toolset — the pins single source + the reconcile-to-pin provisioner (WS5/I, #762).

Covers:
  * the DUAL-SOURCE in-sync invariant: the Python `toolset.py` pins == the shell
    `gate-tool-versions.sh` literals (so the two can't drift while both exist
    through the migration window);
  * the env-override knob (`${NAME:-default}` parity);
  * the reconcile predicate `version_matches` against a fake binary;
  * `provision()` HARD vs best-effort behavior with stubbed steps.
"""

from __future__ import annotations

import os
import re
import stat

import pytest
from release_core import toolset

# Repo paths: this test lives at
# templates/commons/lib/release_core/tests/, so the shell pins file is four
# levels up + bin/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
_SHELL_PINS = os.path.join(_REPO_ROOT, "templates", "commons", "bin", "gate-tool-versions.sh")


def _parse_shell_pins(text: str) -> dict[str, str]:
    """Pull every `NAME="${NAME:-X}"` pin out of the shell file → {NAME: X}."""
    pat = re.compile(r'^([A-Z_]+)="\$\{\1:-([0-9][0-9A-Za-z.]*)\}"', re.M)
    return {m.group(1): m.group(2) for m in pat.finditer(text)}


def test_dual_source_pins_in_sync():
    """The Python pins must byte-match the shell pins — the anti-drift guard for
    the migration window (both sources exist until WS8)."""
    assert os.path.isfile(_SHELL_PINS), f"shell pins file missing: {_SHELL_PINS}"
    with open(_SHELL_PINS, encoding="utf-8") as fh:
        shell = _parse_shell_pins(fh.read())
    # Every Python pin appears in the shell file at the same value, and vice versa
    # (the shell file also defines no EXTRA pins the Python source lacks).
    assert shell == toolset._PINS, (
        f"pin drift between toolset.py and gate-tool-versions.sh:\n"
        f"  python: {toolset._PINS}\n  shell:  {shell}"
    )


def test_pin_env_override(monkeypatch):
    monkeypatch.setenv("RUFF_VERSION", "9.9.9")
    assert toolset.ruff_version() == "9.9.9"
    monkeypatch.delenv("RUFF_VERSION", raising=False)
    assert toolset.ruff_version() == toolset._PINS["RUFF_VERSION"]


def test_pin_blank_env_falls_back(monkeypatch):
    # A blank/whitespace override counts as unset (matches the shell `:-`).
    monkeypatch.setenv("LEFTHOOK_VERSION", "  ")
    assert toolset.lefthook_version() == toolset._PINS["LEFTHOOK_VERSION"]


def _fake_tool(tmp_path, name: str, version_line: str) -> str:
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_version_matches_true(tmp_path, monkeypatch):
    _fake_tool(tmp_path, "ruff", "ruff 0.15.12")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert toolset.version_matches("ruff", "0.15.12") is True


def test_version_matches_floating_is_a_miss(tmp_path, monkeypatch):
    _fake_tool(tmp_path, "ruff", "ruff 0.16.0")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert toolset.version_matches("ruff", "0.15.12") is False


def test_version_matches_absent_is_a_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir
    assert toolset.version_matches("ruff", "0.15.12") is False


def test_provision_hard_raises_when_a_step_fails(monkeypatch):
    """best_effort=False: a step that raises ProvisionError propagates (the gate
    is HARD — a tool that can't be reconciled is a non-zero exit)."""

    def boom(*, best_effort, **kw):
        raise toolset.ProvisionError("npm missing")

    monkeypatch.setattr(toolset, "provision_npm", boom)
    monkeypatch.setattr(toolset, "provision_pip", lambda **k: None)
    monkeypatch.setattr(toolset, "provision_actionlint", lambda **k: None)
    monkeypatch.setattr(toolset, "provision_yq", lambda **k: None)
    with pytest.raises(toolset.ProvisionError):
        toolset.provision(best_effort=False)


def test_provision_all_present_is_a_clean_noop(monkeypatch):
    """Every tool already at its pin → each step is a no-op → provision returns 0."""
    monkeypatch.setattr(toolset, "version_matches", lambda *a, **k: True)
    # No npm/pip/curl needed when nothing is missing.
    assert toolset.provision(best_effort=False) == 0
