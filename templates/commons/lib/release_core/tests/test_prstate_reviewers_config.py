"""The required-reviewer SET + per-reviewer rerun policy is config, not code.

Proves the `reviewers:` config is data-driven: a shipped default
({copilot: rerun=False} — review-once), a per-repo `.release-sync.yaml` override
(map or list shorthand), per-reviewer `rerun` flags, the retired
`required_reviewers:` key failing LOUD, and unknown / non-requestable names
failing LOUD. The engine-side proof (a DIFFERENT set drives a DIFFERENT verdict)
lives in test_prstate_state.py::test_required_set_is_data_driven_*.
"""

from __future__ import annotations

import pytest
from release_core.prstate import reviewers_config
from release_core.prstate.reviewers_config import (
    DEFAULT_REVIEWERS,
    RequiredReviewersConfigError,
    resolve_required_names,
    resolve_reviewers,
    reviewer_rerun,
)


def test_default_is_copilot_only_review_once():
    # CodeRabbit is a phos-org pilot: the App is only installed there, so
    # requiring it by default would park every other repo at REVIEWS_PENDING.
    # rerun defaults False — review once (re-run is opt-in for everyone).
    assert DEFAULT_REVIEWERS == {"copilot": False}
    assert resolve_reviewers(None) == {"copilot": False}
    assert resolve_required_names(None) == ("copilot",)
    assert reviewer_rerun(None) == {"copilot": False}


def test_empty_override_falls_back_to_default():
    # `reviewers: {}` is "unset", never "disable all review gating".
    assert resolve_reviewers({}) == {"copilot": False}


def test_override_swaps_the_set_with_a_one_line_change():
    # A pilot repo opts into CodeRabbit (or any other set) — only config changed.
    parsed = reviewers_config._parse_override_value(
        {"copilot": {"rerun": False}, "coderabbit": {"rerun": False}}
    )
    assert resolve_required_names(parsed) == ("copilot", "coderabbit")
    assert resolve_reviewers(parsed) == {"copilot": False, "coderabbit": False}


def test_rerun_flags_are_per_reviewer():
    parsed = reviewers_config._parse_override_value(
        {"copilot": {"rerun": True}, "codex": {"rerun": False}}
    )
    assert reviewer_rerun(parsed) == {"copilot": True, "codex": False}


def test_rerun_defaults_false_when_options_absent():
    # `copilot:` with an empty/null options value means defaults — rerun=False.
    parsed = reviewers_config._parse_override_value({"copilot": None, "codex": {}})
    assert parsed == {"copilot": False, "codex": False}
    assert reviewer_rerun(parsed) == {"copilot": False, "codex": False}


# --- list shorthand ---------------------------------------------------------


def test_list_shorthand_means_all_required_rerun_false():
    parsed = reviewers_config._parse_override_value(["copilot", "codex", "agy"])
    assert parsed == {"copilot": False, "codex": False, "agy": False}
    assert resolve_required_names(parsed) == ("copilot", "codex", "agy")


def test_list_shorthand_rejects_non_string_entries():
    with pytest.raises(RequiredReviewersConfigError, match="list shorthand"):
        reviewers_config._parse_override_value(["copilot", 3])


def test_list_shorthand_rejects_duplicates():
    # A repeated reviewer in the list shorthand is always a typo, not two gates —
    # it must fail loud, not silently dedup (release#852).
    with pytest.raises(RequiredReviewersConfigError, match="duplicate"):
        reviewers_config._parse_override_value(["copilot", "copilot"])


# --- reviewer-name key normalization (release#852) --------------------------


def test_map_keys_are_canonicalized_to_adapter_names():
    # A `Copilot` key must key the rerun map by the canonical adapter name
    # (`copilot`, lowercase) — the same name the adapters read off the context
    # (`ctx.reviewer_rerun.get(adapter.name, ...)`). Without this, a `rerun: true`
    # keyed `Copilot` is never applied and head-strict silently degrades to
    # review-once.
    parsed = reviewers_config._parse_override_value({"Copilot": {"rerun": True}})
    assert parsed == {"copilot": True}
    assert resolve_required_names(parsed) == ("copilot",)
    assert reviewer_rerun(parsed)["copilot"] is True


def test_list_shorthand_keys_are_canonicalized():
    parsed = reviewers_config._parse_override_value(["Copilot", "CodeRabbit"])
    assert parsed == {"copilot": False, "coderabbit": False}


def test_map_keys_colliding_after_canonicalization_fail_loud():
    # `Copilot` + `copilot` are byte-distinct YAML keys (so YAML's own
    # duplicate-key rejection misses them) but canonicalize to one adapter — a
    # typo, never two gates. It must fail loud, not silently clobber.
    with pytest.raises(RequiredReviewersConfigError, match="duplicate"):
        reviewers_config._parse_override_value({"Copilot": {}, "copilot": {}})


# --- validation (loud) ------------------------------------------------------


def test_local_backends_are_requestable_and_can_be_required():
    # codex / agy are requestable local backends, so they are valid in the
    # required set (they post a real review the gate can read as done).
    assert resolve_required_names({"codex": False}) == ("codex",)
    assert resolve_required_names({"copilot": False, "agy": True}) == ("copilot", "agy")


def test_unknown_reviewer_name_fails_loud():
    with pytest.raises(RequiredReviewersConfigError, match="gpt5"):
        resolve_reviewers({"copilot": False, "gpt5": False})


def test_non_requestable_reviewer_cannot_be_required():
    # Gemini auto-triggers and has no request mechanism, so it can never satisfy
    # a required gate — configuring it required fails loud at parse time.
    with pytest.raises(RequiredReviewersConfigError, match="non-requestable"):
        resolve_reviewers({"copilot": False, "gemini": False})


def test_unknown_per_reviewer_option_fails_loud():
    with pytest.raises(RequiredReviewersConfigError, match="unknown option"):
        reviewers_config._parse_override_value({"copilot": {"reroll": True}})


def test_non_bool_rerun_fails_loud():
    with pytest.raises(RequiredReviewersConfigError, match="must be a boolean"):
        reviewers_config._parse_override_value({"copilot": {"rerun": "yes"}})


def test_wrong_typed_reviewers_value_fails_loud():
    with pytest.raises(RequiredReviewersConfigError, match="must be a map"):
        reviewers_config._parse_override_value("copilot")


def test_required_reviewers_maps_names_to_adapters_in_order():
    adapters = reviewers_config.required_reviewers(("coderabbit", "copilot"))
    assert [a.name for a in adapters] == ["coderabbit", "copilot"]


def test_required_reviewers_rejects_unknown_name():
    with pytest.raises(RequiredReviewersConfigError):
        reviewers_config.required_reviewers(("nope",))


# --- the override loader (the one YAML seam) --------------------------------


def test_load_override_absent_file_is_none(tmp_path):
    assert reviewers_config.load_override(str(tmp_path)) is None


def test_load_override_reads_the_map(tmp_path, monkeypatch):
    (tmp_path / ".release-sync.yaml").write_text(
        "capabilities:\n  - rust-quality\nreviewers:\n  coderabbit:\n    rerun: true\n"
    )
    # Patch the yq-backed sync-config loader so the test is hermetic (no external
    # `yq`); load_override still runs for real over the patched seam.
    from release_core import manifest

    monkeypatch.setattr(
        manifest, "load_sync_config", lambda d=None: {"reviewers": {"coderabbit": {"rerun": True}}}
    )
    assert reviewers_config.load_override(str(tmp_path)) == {"coderabbit": True}


def test_load_override_reads_the_list_shorthand(tmp_path, monkeypatch):
    (tmp_path / ".release-sync.yaml").write_text("reviewers:\n  - copilot\n  - codex\n")
    from release_core import manifest

    monkeypatch.setattr(
        manifest, "load_sync_config", lambda d=None: {"reviewers": ["copilot", "codex"]}
    )
    assert reviewers_config.load_override(str(tmp_path)) == {"copilot": False, "codex": False}


def test_load_override_rejects_a_wrong_typed_value(tmp_path, monkeypatch):
    (tmp_path / ".release-sync.yaml").write_text("reviewers: copilot\n")
    from release_core import manifest

    monkeypatch.setattr(manifest, "load_sync_config", lambda d=None: {"reviewers": "copilot"})
    with pytest.raises(RequiredReviewersConfigError, match="must be a map"):
        reviewers_config.load_override(str(tmp_path))


def test_load_override_retired_required_reviewers_key_fails_loud(tmp_path, monkeypatch):
    # NO BACKWARDS COMPAT: the old list key is gone; a stale config fails loud
    # with a migration message pointing at the `reviewers:` map.
    (tmp_path / ".release-sync.yaml").write_text("required_reviewers:\n  - copilot\n")
    from release_core import manifest

    monkeypatch.setattr(
        manifest, "load_sync_config", lambda d=None: {"required_reviewers": ["copilot"]}
    )
    with pytest.raises(RequiredReviewersConfigError, match="replaced by the `reviewers:` map"):
        reviewers_config.load_override(str(tmp_path))
