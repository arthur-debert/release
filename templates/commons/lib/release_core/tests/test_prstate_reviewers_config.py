"""The required-reviewer SET is a config knob, not code (release#622).

Proves the set is data-driven: a shipped default ([copilot, coderabbit]), a
per-repo `.release-sync.yaml` override, and an unknown name that fails LOUD.
The engine-side proof (a DIFFERENT set drives a DIFFERENT verdict) lives in
test_prstate_state.py::test_required_set_is_data_driven_*.
"""

from __future__ import annotations

import pytest
from release_core.prstate import reviewers_config
from release_core.prstate.reviewers_config import (
    DEFAULT_REQUIRED,
    UnknownReviewerError,
    resolve_required_names,
)


def test_default_is_copilot_and_coderabbit():
    assert DEFAULT_REQUIRED == ("copilot", "coderabbit")
    assert resolve_required_names(None) == ("copilot", "coderabbit")


def test_empty_override_falls_back_to_default():
    # `required_reviewers: []` is "unset", never "disable all review gating".
    assert resolve_required_names([]) == ("copilot", "coderabbit")


def test_override_swaps_the_set_with_a_one_line_change():
    # A single consumer narrows to just CodeRabbit — only config changed.
    assert resolve_required_names(["coderabbit"]) == ("coderabbit",)


def test_override_can_reorder_or_add_within_the_catalog():
    # Re-ordering is config too; any name must map to a registered adapter.
    assert resolve_required_names(["coderabbit", "copilot"]) == ("coderabbit", "copilot")


def test_unknown_reviewer_name_fails_loud():
    with pytest.raises(UnknownReviewerError, match="gpt5"):
        resolve_required_names(["copilot", "gpt5"])


def test_required_reviewers_maps_names_to_adapters_in_order():
    adapters = reviewers_config.required_reviewers(("coderabbit", "copilot"))
    assert [a.name for a in adapters] == ["coderabbit", "copilot"]


def test_required_reviewers_rejects_unknown_name():
    with pytest.raises(UnknownReviewerError):
        reviewers_config.required_reviewers(("nope",))


# --- the override loader (the one YAML seam) --------------------------------


def test_load_override_absent_file_is_none(tmp_path):
    assert reviewers_config.load_override(str(tmp_path)) is None


def test_load_override_reads_the_key(tmp_path, monkeypatch):
    (tmp_path / ".release-sync.yaml").write_text(
        "capabilities:\n  - rust-quality\nrequired_reviewers:\n  - coderabbit\n"
    )
    # Stub the yq-backed loader so the test is hermetic (no external `yq`).
    monkeypatch.setattr(
        reviewers_config,
        "load_override",
        reviewers_config.load_override,  # keep real fn; patch manifest below
    )
    from release_core import manifest

    monkeypatch.setattr(
        manifest, "load_sync_config", lambda d=None: {"required_reviewers": ["coderabbit"]}
    )
    assert reviewers_config.load_override(str(tmp_path)) == ["coderabbit"]


def test_load_override_rejects_a_non_list(tmp_path, monkeypatch):
    (tmp_path / ".release-sync.yaml").write_text("required_reviewers: copilot\n")
    from release_core import manifest

    monkeypatch.setattr(
        manifest, "load_sync_config", lambda d=None: {"required_reviewers": "copilot"}
    )
    with pytest.raises(UnknownReviewerError, match="must be a list"):
        reviewers_config.load_override(str(tmp_path))
