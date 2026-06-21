"""Tests for the review-agent GitHub App tooling (``release_core.review.ghapp``).

No test touches the network: ``gh.rest`` is monkeypatched. Every write is
redirected under ``tmp_path`` by pointing ``XDG_CONFIG_HOME`` at it, so nothing
ever lands in the real ``~/.config``.
"""

from __future__ import annotations

import json
import stat

import pytest
from release_core import gh
from release_core.review import ghapp


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    """Point the app config dir under tmp_path; assert nothing escapes it."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    # Guard: if Path.home() were ever consulted, send it somewhere harmless too.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return xdg


def test_app_config_dir_created_0700(config_home):
    config = ghapp.app_config_dir()
    assert config == config_home / "release-review" / "apps"
    assert config.is_dir()
    mode = stat.S_IMODE(config.stat().st_mode)
    assert mode == 0o700


def test_build_manifest_shape():
    manifest = ghapp.build_manifest(
        "codex", "codex-review", redirect_url="http://localhost:8765/callback"
    )
    assert manifest["name"] == "codex-review"
    assert manifest["public"] is True
    assert manifest["redirect_url"] == "http://localhost:8765/callback"
    assert manifest["default_permissions"] == {
        "pull_requests": "write",
        "contents": "read",
    }
    assert manifest["default_events"] == []
    # GitHub's manifest validator requires a url inside hook_attributes whenever
    # the object is present; default_events:[] means no webhook is ever delivered.
    assert "url" in manifest["hook_attributes"]
    assert manifest["hook_attributes"]["url"].startswith("https://")
    assert manifest["setup_on_update"] is False
    assert manifest["url"] == "https://github.com/arthur-debert/release"


def test_write_manifest_form_structure(config_home, tmp_path):
    out = tmp_path / "codex-manifest.html"
    path = ghapp.write_manifest_form(
        "codex",
        "codex-review",
        out_path=out,
        redirect_url=ghapp.DEFAULT_REDIRECT_URL,
    )
    assert path == out
    doc = out.read_text(encoding="utf-8")

    # A form POSTing to the personal app-new endpoint, with the readable state.
    assert "<form" in doc
    assert 'method="post"' in doc
    assert "settings/apps/new" in doc
    assert "state=release-review-codex" in doc
    # A hidden manifest input + the app name visible in the page.
    assert 'name="manifest"' in doc
    assert "codex-review" in doc
    # Auto-submit script present.
    assert ".submit()" in doc


def test_write_manifest_form_embeds_valid_json(config_home, tmp_path):
    import html as html_mod
    import re

    out = tmp_path / "agy-manifest.html"
    ghapp.write_manifest_form(
        "agy",
        "agy-review",
        out_path=out,
        redirect_url=ghapp.DEFAULT_REDIRECT_URL,
    )
    doc = out.read_text(encoding="utf-8")

    m = re.search(r'name="manifest" value="([^"]*)"', doc)
    assert m, "hidden manifest input not found"
    unescaped = html_mod.unescape(m.group(1))
    manifest = json.loads(unescaped)  # must be valid JSON once unescaped
    assert manifest["name"] == "agy-review"
    assert manifest["default_permissions"]["pull_requests"] == "write"


def _fake_conversion(*, app_id=424242, slug="codex-review", name="codex-review"):
    return {
        "id": app_id,
        "slug": slug,
        "name": name,
        "pem": "-----BEGIN RSA PRIVATE KEY-----\nDUMMYKEYBODY\n-----END RSA PRIVATE KEY-----\n",
        "client_id": "Iv1.dummyclientid",
        "webhook_secret": "dummysecret",
        "owner": {"login": "arthur-debert"},
    }


def test_register_from_code_saves_pem_and_metadata(config_home, monkeypatch):
    captured = {}

    def _fake_rest(path, *, method=None, **kwargs):
        captured["path"] = path
        captured["method"] = method
        return _fake_conversion()

    monkeypatch.setattr(gh, "rest", _fake_rest, raising=True)

    meta = ghapp.register_from_code("codex", "THECODE")

    # Hit the unauthenticated conversion endpoint with the code in the path.
    assert captured["path"] == "/app-manifests/THECODE/conversions"
    assert captured["method"] == "POST"

    config = ghapp.app_config_dir()
    pem_path = config / "codex.pem"
    meta_path = config / "codex.json"

    # pem written, 0600, with the dummy body.
    assert pem_path.exists()
    assert "DUMMYKEYBODY" in pem_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(pem_path.stat().st_mode) == 0o600

    # metadata saved with the right ids, 0600.
    assert meta_path.exists()
    assert stat.S_IMODE(meta_path.stat().st_mode) == 0o600
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved["agent"] == "codex"
    assert saved["app_id"] == 424242
    assert saved["slug"] == "codex-review"
    assert saved["pem_path"] == str(pem_path)

    # ONLY the fields auth actually uses are persisted — no inert secrets land on
    # disk (client_id / webhook_secret are unused: default_events is []).
    assert set(saved) == {"agent", "app_id", "slug", "name", "pem_path"}
    assert "webhook_secret" not in saved
    assert "client_id" not in saved

    # Returned metadata mirrors the saved set: never the pem BODY, never secrets.
    assert "pem" not in meta
    assert "webhook_secret" not in meta
    assert "client_id" not in meta
    assert meta["app_id"] == 424242
    assert meta["pem_path"] == str(pem_path)


def test_register_then_load_and_list_roundtrip(config_home, monkeypatch):
    monkeypatch.setattr(gh, "rest", lambda *a, **k: _fake_conversion(), raising=True)
    ghapp.register_from_code("codex", "CODE")

    loaded = ghapp.load_app("codex")
    assert loaded is not None
    assert loaded["agent"] == "codex"
    assert loaded["app_id"] == 424242

    assert ghapp.load_app("nope") is None

    apps = ghapp.list_apps()
    assert [a["agent"] for a in apps] == ["codex"]


def test_register_raises_without_pem(config_home, monkeypatch):
    monkeypatch.setattr(gh, "rest", lambda *a, **k: {"id": 1, "slug": "x"}, raising=True)
    with pytest.raises(RuntimeError):
        ghapp.register_from_code("codex", "CODE")


def test_nothing_written_under_real_home(config_home, monkeypatch, tmp_path):
    """All writes stay under tmp_path; the real home is never touched."""
    real_home = tmp_path / "home"  # the monkeypatched HOME from the fixture
    monkeypatch.setattr(gh, "rest", lambda *a, **k: _fake_conversion(), raising=True)
    ghapp.register_from_code("codex", "CODE")
    ghapp.write_manifest_form(
        "codex",
        "codex-review",
        out_path=ghapp.app_config_dir() / "codex-manifest.html",
        redirect_url=ghapp.DEFAULT_REDIRECT_URL,
    )

    # Everything lives under XDG (tmp_path/xdg); nothing created a ~/.config tree.
    assert not (real_home / ".config").exists()
    written = list((config_home / "release-review" / "apps").iterdir())
    assert {p.name for p in written} >= {"codex.pem", "codex.json", "codex-manifest.html"}
