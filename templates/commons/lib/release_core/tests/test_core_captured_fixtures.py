"""captured_fixtures — the external-surface fixture provenance lint (#623).

Covers the design core (the marker predicate + the shrink-only ratchet) and the
load-bearing acceptance criterion: a deliberately unmarked NEW fixture fails the
gate. The tests run the engine against a synthetic seam tree (not the real
repo), so they pin behaviour independent of how the live baseline drains.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from release_core import captured_fixtures as cf
from release_core.verbs import captured_fixtures as cf_verb

# ── the marker predicate ─────────────────────────────────────────────────────


def test_marker_token_is_machine_readable():
    # The convention's contract: the token a fixture must carry.
    assert cf.MARKER == "captured-from"


def test_has_marker_json_requires_top_level_key():
    # JSON fixtures: a top-level "captured-from" key with a non-empty value.
    assert cf._has_marker('{"captured-from": "gh pr view #1 (2026-06-12)"}', is_json=True)
    assert not cf._has_marker('{"meta": {"number": 1}}', is_json=True)
    # A prose mention buried in a note value is NOT provenance (the false
    # positive the substring check would have allowed — Copilot #626 thread).
    assert not cf._has_marker(
        '{"_note": "this is captured-from a hand-shape, not real"}', is_json=True
    )
    assert not cf._has_marker('{"captured-from": ""}', is_json=True)  # empty value
    assert not cf._has_marker("not even json", is_json=True)


def test_has_marker_non_json_requires_colon_form():
    # Inline test files / YAML: the documented `captured-from:` comment form.
    assert cf._has_marker("# captured-from: lefthook 2.1.9 piped log\n", is_json=False)
    assert not cf._has_marker("# hand-shaped scenario\n", is_json=False)
    # The bare word without the colon is a stray mention, not the marker form.
    assert not cf._has_marker("# this was captured from a real log\n", is_json=False)


# ── scan over a synthetic seam tree ──────────────────────────────────────────


@pytest.fixture
def seam_tree(tmp_path, monkeypatch):
    """A throwaway lib root with one file-fixture seam dir + one inline seam,
    wired into the registry via monkeypatch so the engine sweeps it instead of
    the real repo."""
    fixtures = tmp_path / "tests" / "fx"
    fixtures.mkdir(parents=True)
    (fixtures / "marked.json").write_text(
        json.dumps({"captured-from": "gh api graphql #1 (2026-06-12)", "x": 1})
    )
    (fixtures / "unmarked.json").write_text(json.dumps({"x": 2}))
    (fixtures / "ignore.txt").write_text("not a json fixture")

    inline = tmp_path / "tests" / "test_inline.py"
    inline.write_text("# captured-from: a real log\n_X = '...'\n")

    monkeypatch.setattr(
        cf,
        "SEAMS",
        (cf.Seam(name="demo", fixture_dir="tests/fx", suffixes=(".json",), surface="demo gh API"),),
    )
    monkeypatch.setattr(
        cf, "INLINE_SEAMS", (("demo_inline", "tests/test_inline.py", "demo lefthook log"),)
    )
    return str(tmp_path)


def test_scan_flags_only_unmarked_json_fixtures(seam_tree):
    offenders = cf.scan(lib_root=seam_tree)
    keys = {o.key for o in offenders}
    # marked.json carries the marker; ignore.txt is not a .json fixture; the
    # inline seam file carries its marker comment — so only unmarked.json fails.
    assert keys == {"demo/tests/fx/unmarked.json"}


def test_offender_keys_are_posix_across_platforms(seam_tree):
    # The seam/relpath key is POSIX-joined (never OS-native), so a fixture's key
    # matches a `/`-authored baseline entry even on Windows. No backslashes.
    offenders = cf.scan(lib_root=seam_tree)
    for o in offenders:
        assert "\\" not in o.key
        assert "\\" not in o.path


def test_scan_flags_unmarked_inline_seam(seam_tree):
    # Strip the marker from the inline seam → it becomes an offender. Write under
    # seam_tree (the lib root the scan runs against), not a separate tmp_path.
    (Path(seam_tree) / "tests" / "test_inline.py").write_text("_X = '...'\n")
    offenders = cf.scan(lib_root=seam_tree)
    keys = {o.key for o in offenders}
    assert "demo_inline/tests/test_inline.py" in keys


def test_scan_raises_on_missing_seam_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cf,
        "SEAMS",
        (cf.Seam(name="gone", fixture_dir="tests/nope", suffixes=(".json",), surface="x"),),
    )
    monkeypatch.setattr(cf, "INLINE_SEAMS", ())
    with pytest.raises(cf.CapturedFixtureError, match="registered seam dir missing"):
        cf.scan(lib_root=str(tmp_path))


def test_scan_wraps_fixture_read_errors(seam_tree, monkeypatch):
    # A fixture read failure (e.g. non-UTF-8 bytes) surfaces as the lint's own
    # CapturedFixtureError, not a raw OSError / UnicodeDecodeError traceback.
    def boom(_fpath, _encoding=None, **_kw):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(cf.CapturedFixtureError, match="cannot read fixture"):
        cf.scan(lib_root=seam_tree)


# ── the shrink-only ratchet ──────────────────────────────────────────────────


def _off(key):
    seam, _, path = key.partition("/")
    return cf.Offender(seam=seam, path=path, surface="x")


def test_apply_baseline_grandfathers_listed_fails_new():
    offenders = [_off("s/a.json"), _off("s/b.json")]
    baseline = [{"key": "s/a.json", "reason": "known"}]
    failing, grandfathered, stale = cf.apply_baseline(offenders, baseline)
    assert [o.key for o in failing] == ["s/b.json"]  # NEW unmarked → fails
    assert [o.key for o in grandfathered] == ["s/a.json"]  # listed → grandfathered
    assert stale == []


def test_apply_baseline_flags_stale_entry():
    # A baseline entry with no matching offender = the fixture got its marker;
    # the baseline only shrinks, so the stale entry must be deleted.
    offenders: list[cf.Offender] = []
    baseline = [{"key": "s/gone.json", "reason": "drained"}]
    failing, grandfathered, stale = cf.apply_baseline(offenders, baseline)
    assert failing == []
    assert grandfathered == []
    assert [e["key"] for e in stale] == ["s/gone.json"]


def test_load_baseline_wraps_yaml_errors(tmp_path, monkeypatch):
    # A yq failure (yamlio.YamlError) or non-JSON emission (ValueError) must
    # surface as CapturedFixtureError, not bubble as an unhandled traceback —
    # so the verb's single except yields a clean gate message.
    (tmp_path / cf.BASELINE_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / cf.BASELINE_RELPATH).write_text("known_unmarked_fixtures: []\n")

    def boom(_path):
        raise cf.yamlio.YamlError("yq parse failure")

    monkeypatch.setattr(cf.yamlio, "load", boom)
    with pytest.raises(cf.CapturedFixtureError, match="cannot read baseline"):
        cf.load_baseline(lib_root=str(tmp_path))


# ── the verb (gate entrypoint) ───────────────────────────────────────────────


def test_verb_lint_passes_clean_tree(seam_tree, monkeypatch, capsys):
    # Baseline covers the one offender → lint exits 0.
    monkeypatch.setattr(cf, "_lib_root", lambda start=None: seam_tree)
    monkeypatch.setattr(
        cf, "load_baseline", lambda lib_root=None: [{"key": "demo/tests/fx/unmarked.json"}]
    )
    rc = cf_verb.main(["lint"])
    assert rc == 0
    assert "0 new unmarked" in capsys.readouterr().out


def test_verb_lint_fails_on_new_unmarked_fixture(seam_tree, monkeypatch, capsys):
    # The acceptance criterion: drop a NEW unmarked fixture into the seam → the
    # gate (verb) fails. Empty baseline so nothing is grandfathered. The fixture
    # goes under seam_tree (the lib root the scan runs against).
    (Path(seam_tree) / "tests" / "fx" / "brand_new.json").write_text(json.dumps({"surprise": 1}))
    monkeypatch.setattr(cf, "_lib_root", lambda start=None: seam_tree)
    monkeypatch.setattr(cf, "load_baseline", lambda lib_root=None: [])
    rc = cf_verb.main(["lint"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "VIOLATION" in err
    assert "brand_new.json" in err


def test_verb_lint_fails_on_stale_baseline_entry(seam_tree, monkeypatch, capsys):
    monkeypatch.setattr(cf, "_lib_root", lambda start=None: seam_tree)
    monkeypatch.setattr(
        cf,
        "load_baseline",
        lambda lib_root=None: [
            {"key": "demo/tests/fx/unmarked.json"},
            {"key": "demo/tests/fx/already_marked.json"},  # no matching offender
        ],
    )
    rc = cf_verb.main(["lint"])
    assert rc == 1
    assert "STALE baseline entry" in capsys.readouterr().err


def test_verb_rejects_unknown_subcommand(capsys):
    assert cf_verb.main(["bogus"]) == cf_verb.EXIT_USAGE
