"""Tests for GitHub App *installation* auth (``release_core.review.ghauth``).

No test touches the network: the stdlib HTTP seams (``_api_get`` / ``_api_post``)
are monkeypatched. ``make_app_jwt`` is exercised for REAL — a throwaway RSA
keypair is generated in-test, ``ghapp`` is pointed at it, and the produced JWT is
verified with the public key. PyJWT (with crypto) is expected in the test env;
if it (or cryptography) is genuinely absent the keypair generation / verify is
skipped and only the lazy-import error path is asserted.
"""

from __future__ import annotations

import json

import pytest
from release_core.review import ghapp, ghauth, post

try:  # the real-signing path needs both PyJWT and a crypto backend
    import jwt as _jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    _HAVE_JWT = True
except ImportError:  # pragma: no cover - env without the review-app extra
    _HAVE_JWT = False


@pytest.fixture
def app_meta(tmp_path, monkeypatch):
    """Point ghapp's config dir at tmp_path and seed a fake codex app + pem.

    Generates a throwaway RSA keypair so make_app_jwt can really sign. Returns
    ``(app_id, public_key_pem)`` for the verify side of the JWT assertion.
    """
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    app_id = 4111233
    public_pem = None
    if _HAVE_JWT:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_pem = (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )
    else:  # a placeholder pem so the file exists; signing path is skipped
        private_pem = "-----BEGIN RSA PRIVATE KEY-----\nX\n-----END RSA PRIVATE KEY-----\n"

    config = ghapp.app_config_dir()
    (config / "codex.pem").write_text(private_pem, encoding="utf-8")
    (config / "codex.json").write_text(
        json.dumps(
            {
                "agent": "codex",
                "app_id": app_id,
                "slug": "adr-codex-review",
                "name": "adr-codex-review",
                "pem_path": str(config / "codex.pem"),
            }
        ),
        encoding="utf-8",
    )
    return app_id, public_pem


# --- make_app_jwt -------------------------------------------------------------


@pytest.mark.skipif(not _HAVE_JWT, reason="PyJWT[crypto] not installed")
def test_make_app_jwt_real_sign_and_verify(app_meta):
    app_id, public_pem = app_meta
    token = ghauth.make_app_jwt("codex")

    # Decode + verify the signature with the public key.
    claims = _jwt.decode(token, public_pem, algorithms=["RS256"])
    # iss is the app id as a string (PyJWT ≥2.10 requires a string iss).
    assert claims["iss"] == str(app_id)
    assert claims["exp"] > claims["iat"]
    # exp is within ~10 minutes of iat (GitHub's cap).
    assert claims["exp"] - claims["iat"] <= 600


def test_make_app_jwt_unconfigured_agent_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with pytest.raises(ghauth.ReviewAuthError):
        ghauth.make_app_jwt("nope")


def test_make_app_jwt_missing_pyjwt_raises_hint(app_meta, monkeypatch):
    """If PyJWT is unimportable, the lazy import raises ReviewAuthError w/ hint."""
    import builtins

    real_import = builtins.__import__

    def _no_jwt(name, *args, **kwargs):
        if name == "jwt":
            raise ImportError("no module named jwt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_jwt)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth.make_app_jwt("codex")
    assert "pyjwt[crypto]" in str(exc.value)


# --- installation_id ----------------------------------------------------------


def test_installation_id_parses_id(app_meta, monkeypatch):
    seen = {}

    def _fake_get(path, jwt_token):
        seen["path"] = path
        return {"id": 999}

    monkeypatch.setattr(ghauth, "_api_get", _fake_get)
    # Pass jwt explicitly so we don't need to sign.
    assert ghauth.installation_id("codex", "arthur-debert/release", jwt="JWT") == 999
    assert seen["path"] == "/repos/arthur-debert/release/installation"


def test_installation_id_404_raises_install_hint(app_meta, monkeypatch):
    def _fake_get(path, jwt_token):
        raise ghauth.ReviewAuthError("GitHub API GET /... failed (HTTP 404): not found")

    monkeypatch.setattr(ghauth, "_api_get", _fake_get)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth.installation_id("codex", "arthur-debert/release", jwt="JWT")
    msg = str(exc.value)
    assert "not installed" in msg
    # Uses the app slug in the install URL.
    assert "apps/adr-codex-review/installations/new" in msg


def test_installation_id_non_404_propagates(app_meta, monkeypatch):
    def _fake_get(path, jwt_token):
        raise ghauth.ReviewAuthError("GitHub API GET /... failed (HTTP 500): boom")

    monkeypatch.setattr(ghauth, "_api_get", _fake_get)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth.installation_id("codex", "arthur-debert/release", jwt="JWT")
    assert "HTTP 500" in str(exc.value)


# --- _api_request timeout -----------------------------------------------------


def test_api_request_timeout_raises_review_auth_error(monkeypatch):
    """A urlopen timeout becomes a ReviewAuthError, not a bare TimeoutError."""

    def _timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ghauth.urllib.request, "urlopen", _timeout)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth._api_get("/repos/x/y/installation", "JWT")
    msg = str(exc.value)
    assert "timed out" in msg
    assert str(ghauth._HTTP_TIMEOUT) in msg


def test_api_request_post_timeout_raises_review_auth_error(monkeypatch):
    """The POST seam (step 3) normalizes a timeout the same way as GET."""

    def _timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ghauth.urllib.request, "urlopen", _timeout)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth._api_post("/app/installations/1/access_tokens", "JWT")
    assert "timed out" in str(exc.value)


def test_api_request_urlerror_wrapping_timeout_normalized(monkeypatch):
    """A timeout surfacing as URLError(reason=TimeoutError) is normalized too.

    socket.timeout is an alias of TimeoutError since Py3.10, so a urllib timeout
    that arrives wrapped in URLError still maps to the timeout message.
    """

    def _urlerror(*a, **k):
        raise ghauth.urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(ghauth.urllib.request, "urlopen", _urlerror)
    with pytest.raises(ghauth.ReviewAuthError) as exc:
        ghauth._api_get("/repos/x/y/installation", "JWT")
    assert "timed out" in str(exc.value)


# --- installation_token -------------------------------------------------------


def test_installation_token_orchestrates(app_meta, monkeypatch):
    posted = {}

    monkeypatch.setattr(ghauth, "make_app_jwt", lambda agent: "JWT")
    monkeypatch.setattr(ghauth, "installation_id", lambda agent, repo, *, jwt=None: 777)

    def _fake_post(path, jwt_token):
        posted["path"] = path
        posted["jwt"] = jwt_token
        return {"token": "ghs_fake", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(ghauth, "_api_post", _fake_post)

    token = ghauth.installation_token("codex", "arthur-debert/release")
    assert token == "ghs_fake"
    assert posted["path"] == "/app/installations/777/access_tokens"
    assert posted["jwt"] == "JWT"


def test_installation_token_no_token_raises(app_meta, monkeypatch):
    monkeypatch.setattr(ghauth, "make_app_jwt", lambda agent: "JWT")
    monkeypatch.setattr(ghauth, "installation_id", lambda agent, repo, *, jwt=None: 1)
    monkeypatch.setattr(ghauth, "_api_post", lambda path, jwt_token: {"message": "bad"})
    with pytest.raises(ghauth.ReviewAuthError):
        ghauth.installation_token("codex", "arthur-debert/release")


# --- post_review(as_app=...) --------------------------------------------------

from release_core import gh  # noqa: E402 - imported after the ghauth tests for grouping
from release_core.review.diff import PRContext  # noqa: E402

SAMPLE_DIFF = """\
diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 import os
+import sys
 print("hi")
"""

SAMPLE_REVIEW = {
    "summary": {"status": "COMMENT", "overall_feedback": "looks fine"},
    "comments": [],
}


def _ctx() -> PRContext:
    return PRContext(
        number=42,
        repo="arthur-debert/release",
        head_sha="headsha123",
        base_ref="main",
        base_sha="basesha000",
        diff=SAMPLE_DIFF,
        changed_files=["a.py"],
        workdir="/tmp/checkout",
    )


def test_post_review_as_app_dry_run_mints_no_token(app_meta, monkeypatch, capsys):
    """Dry-run + as_app: NEVER calls ghauth (no network), still renders payload."""

    def _boom(*a, **k):
        raise AssertionError("dry-run must not mint a token")

    monkeypatch.setattr(ghauth, "installation_token", _boom)
    # gh.rest must also not be called on dry-run.
    monkeypatch.setattr(gh, "rest", _boom)

    out = post.post_review(SAMPLE_REVIEW, _ctx(), agent_name="codex", dry_run=True, as_app=True)
    assert out["event"] == "COMMENT"
    printed = capsys.readouterr().out
    # Renders the payload and notes the bot author (slug from metadata).
    assert "looks fine" in printed
    assert "adr-codex-review[bot]" in printed


def test_post_review_as_app_passes_token_to_gh_rest(app_meta, monkeypatch):
    monkeypatch.setattr(ghauth, "installation_token", lambda agent, repo: "ghs_fake")

    captured = {}

    def _fake_rest(path, *, method=None, body=None, token=None, **kwargs):
        captured["path"] = path
        captured["method"] = method
        captured["token"] = token
        captured["body"] = body
        return {"html_url": "https://example/r/1"}

    monkeypatch.setattr(gh, "rest", _fake_rest)

    post.post_review(SAMPLE_REVIEW, _ctx(), agent_name="codex", as_app=True)
    assert captured["token"] == "ghs_fake"
    assert captured["path"] == "/repos/arthur-debert/release/pulls/42/reviews"
    assert captured["method"] == "POST"


def test_post_review_user_auth_passes_no_token(app_meta, monkeypatch):
    """Default (as_app=False): token stays None — exactly the pre-existing path."""
    captured = {}

    def _fake_rest(path, *, method=None, body=None, token=None, **kwargs):
        captured["token"] = token
        return {"html_url": "x"}

    monkeypatch.setattr(gh, "rest", _fake_rest)
    # ghauth must not be touched.
    monkeypatch.setattr(
        ghauth,
        "installation_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not mint")),
    )

    post.post_review(SAMPLE_REVIEW, _ctx(), agent_name="codex")
    assert captured["token"] is None
