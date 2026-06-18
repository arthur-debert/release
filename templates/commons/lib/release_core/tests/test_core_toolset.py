"""toolset — the pins single source + the reconcile-to-pin provisioner (WS5/I, #762).

Covers:
  * the env-override knob (`${NAME:-default}` parity);
  * the reconcile predicate `version_matches` against a fake binary;
  * `provision()` HARD vs best-effort behavior with stubbed steps.

(WS8 #765 removed the shell `gate-tool-versions.sh` — toolset.py is now THE single
source of pins, so the former dual-source in-sync invariant no longer applies.)
"""

from __future__ import annotations

import os
import stat

import pytest
from release_core import toolset


def test_pin_env_override(monkeypatch):
    monkeypatch.setenv("RUFF_VERSION", "9.9.9")
    assert toolset.ruff_version() == "9.9.9"
    monkeypatch.delenv("RUFF_VERSION", raising=False)
    assert toolset.ruff_version() == toolset._PINS["RUFF_VERSION"]


def test_pin_blank_env_falls_back(monkeypatch):
    # A blank/whitespace override counts as unset — STRICTER than shell `:-`
    # (which only falls back for unset/empty; whitespace is "set"). pin() strips.
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


def test_prepend_path_puts_dest_first_and_dedups(monkeypatch):
    # The pinned binary's dir must end up FIRST on PATH (shadowing an earlier
    # floating copy) and not be duplicated if already present (release#755).
    monkeypatch.setenv("PATH", "/usr/bin:/opt/x/bin:/usr/local/bin")
    toolset._prepend_path("/opt/x/bin")
    import os as _os

    parts = _os.environ["PATH"].split(_os.pathsep)
    assert parts[0] == "/opt/x/bin"
    assert parts.count("/opt/x/bin") == 1


def test_default_bin_dir_honors_xdg(monkeypatch, tmp_path):
    # Must match install-release-core/arm-gate's ${XDG_BIN_HOME:-$HOME/.local/bin}
    # so the pinned binary installs where the boot prepends PATH.
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "custombin"))
    assert toolset._default_bin_dir() == str(tmp_path / "custombin")
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    assert toolset._default_bin_dir().endswith("/.local/bin")


def test_provision_converts_unexpected_oserror(monkeypatch):
    # An unexpected OSError (e.g. makedirs permission) from ANY step must not
    # escape as a raw traceback: HARD → ProvisionError; best-effort → warn + 0.
    def fs_boom(*, best_effort, **kw):
        raise OSError("Permission denied: /opt/bin")

    monkeypatch.setattr(toolset, "provision_npm", fs_boom)
    monkeypatch.setattr(toolset, "provision_pip", lambda **k: None)
    monkeypatch.setattr(toolset, "provision_actionlint", lambda **k: None)
    monkeypatch.setattr(toolset, "provision_yq", lambda **k: None)
    with pytest.raises(toolset.ProvisionError):
        toolset.provision(best_effort=False)
    assert toolset.provision(best_effort=True) == 0  # best-effort swallows + warns
