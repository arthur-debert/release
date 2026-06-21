"""ghapp — create the thin GitHub App that gives a review agent its identity.

A review backend (``codex`` / ``agy``) posts comments AS itself, which needs a
bot identity: a name + avatar. The cheapest way to mint one is GitHub's
*app-manifest flow* — you POST a manifest to ``github.com/settings/apps/new``,
GitHub creates the app and redirects back with a one-time ``code``, and a single
unauthenticated ``POST /app-manifests/{code}/conversions`` exchanges that code
for the app's id + private key (pem).

This module builds the manifest, writes a self-submitting HTML form that kicks
off the flow, and — given the ``code`` the user copies out of the post-redirect
address bar — converts + saves the app's id and pem locally, OUTSIDE any repo
(under ``${XDG_CONFIG_HOME:-~/.config}/release-review/apps``, 0700). The app's
ONLY purpose is identity; all review logic stays in our scripts. Posting AS the
bot (JWT → installation token → API) is a separate, later task — this module
mints the identity, nothing more.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from .. import gh

#: A safe agent stem: lowercase letters, digits, hyphen, underscore only. The
#: agent name is interpolated into secrets-dir filenames (`<agent>.json` /
#: `<agent>.pem`), so anything that could escape the dir (`.`, `/`, `..`) is
#: rejected — `_safe_agent` is applied wherever `agent` becomes a path.
_SAFE_AGENT_RE = re.compile(r"^[a-z0-9_-]+$")

#: Default redirect target baked into the manifest. No server runs here: after
#: GitHub creates the app it redirects the browser to this URL with a ``?code=``
#: query param; the user copies that ``code`` out of the address bar by hand and
#: feeds it to ``register``. The host/port is arbitrary (nothing listens).
DEFAULT_REDIRECT_URL = "http://localhost:8765/callback"

#: EXAMPLE owners to install a freshly minted app on (done in the GitHub web UI).
#: An app only needs installing where the bot will actually post reviews — your
#: own account plus any orgs whose repos you review. These are the owners for
#: THIS ecosystem (context-specific example data), not a universal requirement.
INSTALL_OWNERS = ("arthur-debert", "lex-fmt", "phos-editor")


def app_config_dir() -> Path:
    """Return (creating it 0700 if needed) the secrets dir for review apps.

    ``${XDG_CONFIG_HOME:-~/.config}/release-review/apps``. This lives OUTSIDE
    any repo on purpose — it holds private keys. Created lazily, 0700.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    config = root / "release-review" / "apps"
    config.mkdir(parents=True, exist_ok=True)
    # Tighten the leaf dir (mkdir respects the umask, so set it explicitly).
    config.chmod(0o700)
    return config


def build_manifest(agent: str, app_name: str, *, redirect_url: str) -> dict:
    """Build the GitHub App manifest for ``agent`` named ``app_name``.

    Minimal identity-only app: PR write + contents read (so a future posting
    task can comment), no events, webhook off. ``agent`` is accepted for symmetry
    / future use; the manifest content is driven by ``app_name`` + ``redirect_url``.

    ``hook_attributes`` carries a placeholder webhook ``url``: GitHub's manifest
    validator REQUIRES a ``url`` inside ``hook_attributes`` whenever the object is
    present (omitting only ``url`` is rejected with the misleading
    ``"url" wasn't supplied`` error, which refers to the webhook url, not the
    top-level homepage url). Since ``default_events`` is empty, no webhooks are
    ever delivered, so the placeholder is inert.
    """
    return {
        "name": app_name,
        "url": "https://github.com/arthur-debert/release",
        "public": True,
        "redirect_url": redirect_url,
        "default_permissions": {"pull_requests": "write", "contents": "read"},
        "default_events": [],
        "hook_attributes": {"url": "https://example.com/release-review/webhook"},
        "setup_on_update": False,
    }


def write_manifest_form(
    agent: str,
    app_name: str,
    *,
    out_path: Path,
    redirect_url: str,
) -> Path:
    """Write a self-submitting HTML form that starts the app-manifest flow.

    The form POSTs the JSON-encoded manifest (HTML-attribute-escaped) to the
    PERSONAL account endpoint ``https://github.com/settings/apps/new?state=…``.
    It auto-submits on load; if that's blocked, visible fallback text plus a
    "Create the app" button let the user submit manually. Returns ``out_path``.
    """
    # ``agent`` is interpolated into the form's ``state`` and the register
    # command line, and callers derive the output filename from it
    # (``<config>/{agent}-manifest.html``) — validate it before any of that so
    # ``agent="../evil"`` fails loud rather than escaping the secrets dir.
    agent = _safe_agent(agent)
    manifest = build_manifest(agent, app_name, redirect_url=redirect_url)
    state = f"release-review-{agent}"
    manifest_attr = html.escape(json.dumps(manifest), quote=True)
    action = f"https://github.com/settings/apps/new?state={html.escape(state, quote=True)}"
    name_text = html.escape(app_name)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Create GitHub App: {name_text}</title>
</head>
<body>
<h1>Create the GitHub App &ldquo;{name_text}&rdquo;</h1>
<p>
  This page submits a GitHub <em>app manifest</em> to create the review-bot
  identity <strong>{name_text}</strong> on your personal account. It should
  submit automatically. If nothing happens, click the button below.
</p>
<form id="manifest-form" method="post" action="{action}">
  <input type="hidden" name="manifest" value="{manifest_attr}">
  <button type="submit">Create the app</button>
</form>
<p>
  After GitHub creates the app it redirects to
  <code>{html.escape(redirect_url)}</code> with a <code>?code=&lt;CODE&gt;</code>
  query parameter. No server is listening there &mdash; just copy the
  <code>code</code> value out of the browser&rsquo;s address bar and run:
</p>
<pre>release-core review app register {html.escape(agent)} --code &lt;CODE&gt;</pre>
<script>
  document.getElementById("manifest-form").submit();
</script>
</body>
</html>
"""
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def _safe_agent(agent: str) -> str:
    """Validate ``agent`` is a safe filename stem, returning it unchanged.

    The agent name is interpolated into filenames under the secrets dir, so a
    value like ``../evil`` or ``a/b`` could write outside it. Allow only
    ``[a-z0-9_-]+``; reject anything else (including ``.``, ``/``, ``..``).
    """
    if not _SAFE_AGENT_RE.match(agent):
        raise ValueError(
            f"invalid agent name {agent!r} — use only lowercase letters, digits, '-', '_'"
        )
    return agent


def _metadata_path(agent: str, config: Path | None = None) -> Path:
    return (config or app_config_dir()) / f"{_safe_agent(agent)}.json"


def app_pem_path(agent: str, config: Path | None = None) -> Path:
    """Path to ``agent``'s saved private key (may not exist yet)."""
    return (config or app_config_dir()) / f"{_safe_agent(agent)}.pem"


def register_from_code(agent: str, code: str) -> dict:
    """Exchange a manifest ``code`` for the app and save its id + pem locally.

    Calls the unauthenticated ``POST /app-manifests/{code}/conversions`` (the
    code IS the credential) through :func:`gh.rest`, writes the pem to
    ``<config>/<agent>.pem`` (0600) and a metadata JSON to
    ``<config>/<agent>.json`` (0600), and returns the saved metadata. The pem
    body is never included in the returned dict (only its path).
    """
    agent = _safe_agent(agent)
    resp = gh.rest(f"/app-manifests/{code}/conversions", method="POST")
    if not isinstance(resp, dict):
        raise RuntimeError(
            f"Unexpected response from app-manifest conversion for {agent!r}: {resp!r}"
        )

    config = app_config_dir()

    pem = resp.get("pem")
    if not pem:
        raise RuntimeError(f"Conversion response for {agent!r} carried no 'pem'")
    pem_path = app_pem_path(agent, config)
    pem_path.write_text(pem, encoding="utf-8")
    pem_path.chmod(0o600)

    # Persist ONLY the fields later auth actually uses: installation auth signs
    # an app JWT from app_id + the pem, and the slug/name are for the install
    # URL + display. ``client_id`` / ``webhook_secret`` are unused (manifest sets
    # ``default_events: []`` so no webhook is ever delivered), so they are not
    # written — no point persisting an inert secret to disk.
    metadata = {
        "agent": agent,
        "app_id": resp.get("id"),
        "slug": resp.get("slug"),
        "name": resp.get("name"),
        "pem_path": str(pem_path),
    }
    meta_path = _metadata_path(agent, config)
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    meta_path.chmod(0o600)

    return metadata


def load_app(agent: str) -> dict | None:
    """Load ``agent``'s saved metadata, or ``None`` if it isn't configured."""
    meta_path = _metadata_path(agent)
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def list_apps() -> list[dict]:
    """Return saved metadata for every configured review app (sorted by agent)."""
    config = app_config_dir()
    apps: list[dict] = []
    for meta_path in sorted(config.glob("*.json")):
        try:
            apps.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return apps
