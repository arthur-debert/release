"""app_cli — ``release-core review app …``: mint a review agent's GitHub App.

Three leaves drive the GitHub app-manifest flow that gives a review backend its
bot identity (see :mod:`release_core.review.ghapp`):

- ``manifest <agent> --name <app_name>`` — write a self-submitting HTML form
  that POSTs the manifest to ``github.com/settings/apps/new`` and print the
  next steps.
- ``register <agent> --code <code>`` — exchange the post-redirect ``code`` for
  the app and save its id + private key locally (outside any repo).
- ``list`` — show the configured agents.

Each leaf is an argparse ``main(argv) -> int`` so the click layer can wrap it
with ``wrap_verb`` exactly like every other ``release-core`` verb.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ghapp


def _print_post_create_reminders(agent: str) -> None:
    owners = ", ".join(ghapp.INSTALL_OWNERS)
    print(
        "\nAfter the app is created, in the GitHub web UI:\n"
        "  - upload an avatar for the app (its visible identity), and\n"
        "  - install the app on each owner where the bot will post reviews —\n"
        "    your account and any orgs whose repos you'll review\n"
        f"    (e.g. {owners}).",
    )


# --- manifest ---------------------------------------------------------------


def _manifest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-core review app manifest",
        description="Write the HTML form that starts the GitHub app-manifest flow.",
    )
    parser.add_argument("agent", help="Agent label (e.g. 'codex' or 'agy').")
    parser.add_argument(
        "--name",
        required=True,
        help="Display name for the GitHub App (the bot's visible identity).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Where to write the HTML form (default: <config>/<agent>-manifest.html).",
    )
    parser.add_argument(
        "--redirect-url",
        default=ghapp.DEFAULT_REDIRECT_URL,
        help="Manifest redirect_url (default: %(default)s — nothing listens; copy the code).",
    )
    return parser


def manifest_main(argv: list[str]) -> int:
    """Entry point for ``release-core review app manifest``."""
    args = _manifest_parser().parse_args(argv)

    if args.out is not None:
        out_path = Path(args.out)
    else:
        out_path = ghapp.app_config_dir() / f"{args.agent}-manifest.html"

    path = ghapp.write_manifest_form(
        args.agent,
        args.name,
        out_path=out_path,
        redirect_url=args.redirect_url,
    )

    print(f"Wrote the app-manifest form to: {path}")
    print("\nNext steps:")
    print("  1) open this file in a browser where you're logged into GitHub,")
    print("  2) click Create (the form also auto-submits on load),")
    print("  3) after the redirect, copy the `code` value from the address bar,")
    print(f"  4) run: release-core review app register {args.agent} --code <code>")
    _print_post_create_reminders(args.agent)
    return 0


# --- register ---------------------------------------------------------------


def _register_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-core review app register",
        description="Exchange a manifest code for the app and save its id + pem locally.",
    )
    parser.add_argument("agent", help="Agent label (e.g. 'codex' or 'agy').")
    parser.add_argument(
        "--code",
        required=True,
        help="The one-time code from the post-redirect address bar.",
    )
    return parser


def register_main(argv: list[str]) -> int:
    """Entry point for ``release-core review app register``."""
    args = _register_parser().parse_args(argv)

    try:
        meta = ghapp.register_from_code(args.agent, args.code)
    except Exception as exc:  # noqa: BLE001 — surface any failure as a clean nonzero
        print(f"Could not register app for {args.agent!r}: {exc}")
        return 1

    print(f"Saved GitHub App for agent {meta['agent']!r}:")
    print(f"  app id: {meta['app_id']}")
    print(f"  slug:   {meta['slug']}")
    print(f"  pem:    {meta['pem_path']}")
    _print_post_create_reminders(args.agent)
    return 0


# --- list -------------------------------------------------------------------


def _list_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="release-core review app list",
        description="List the configured review-agent GitHub Apps.",
    )


def list_main(argv: list[str]) -> int:
    """Entry point for ``release-core review app list``."""
    _list_parser().parse_args(argv)

    apps = ghapp.list_apps()
    if not apps:
        print("No review apps configured.")
        return 0

    for meta in apps:
        agent = meta.get("agent", "?")
        pem_path = meta.get("pem_path")
        pem_present = bool(pem_path) and Path(pem_path).exists()
        print(
            f"{agent}\tslug={meta.get('slug')}\tapp_id={meta.get('app_id')}"
            f"\tpem={'yes' if pem_present else 'no'}"
        )
    return 0
