"""gh — the single boundary to GitHub/git: shell out, parse JSON with stdlib.

Mirrors release_gh/ghapi.py conventions (they consolidate in a later phase;
duplication is tolerated in Phase 0). Why `gh` rather than a Python client:
it is already provisioned in every environment release runs in, handles auth +
pagination, and speaks GraphQL. Keeping the boundary here means every migrated
verb is pure data transformation over the returned dicts — **no `jq`**.
"""

from __future__ import annotations

import json
import shutil

from . import proc


class GhError(RuntimeError):
    """A `gh` invocation failed, or `gh` is unavailable."""


def _gh(args: list[str], *, input_text: str | None = None) -> str:
    if shutil.which("gh") is None:
        raise GhError("`gh` CLI not found on PATH")
    result = proc.run(["gh", *args], input=input_text, check=False)
    if result.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def rest(
    path: str,
    *,
    method: str | None = None,
    fields: dict[str, str] | None = None,
    body: object | None = None,
    paginate: bool = False,
) -> object:
    """Call `gh api <path>` → parsed JSON (None on empty output). Raises GhError.

    ``body``, when given, is serialized to JSON and piped to `gh api --input -`
    — the only way to send an arbitrary nested request body (e.g. a ruleset
    payload) that the flat `-f key=value` ``fields`` form cannot express.
    ``fields`` and ``body`` are mutually exclusive.
    """
    if fields and body is not None:
        raise GhError("rest(): pass either fields= or body=, not both")
    args = ["api"]
    if method:
        args += ["-X", method]
    if paginate:
        args.append("--paginate")
    for key, value in (fields or {}).items():
        args += ["-f", f"{key}={value}"]
    input_text = None
    if body is not None:
        args += ["--input", "-"]
        input_text = json.dumps(body)
    args.append(path)
    output = _gh(args, input_text=input_text)
    if not output.strip():
        return None
    if paginate:
        return _merge_paginated(output)
    return json.loads(output)


def _merge_paginated(output: str) -> list:
    """`gh api --paginate` concatenates one JSON array per page; flatten them."""
    merged: list = []
    decoder = json.JSONDecoder()
    text = output.strip()
    idx = 0
    while idx < len(text):
        obj, end = decoder.raw_decode(text, idx)
        merged.extend(obj if isinstance(obj, list) else [obj])
        idx = end
        while idx < len(text) and text[idx] in " \n\r\t":
            idx += 1
    return merged


def issue_list(
    repo: str,
    *,
    state: str = "open",
    label: str | None = None,
    limit: int = 200,
    json_fields: list[str] | None = None,
) -> list:
    """`gh issue list --json …` → parsed list of issue dicts. Raises GhError.

    A thin wrapper over the `gh issue list` porcelain (not the raw REST search
    API): it handles the label/state filters and `--json` field selection the
    fleet-inbox verb needs, while keeping the gh boundary the single chokepoint.
    """
    args = ["issue", "list", "--repo", repo, "--state", state, "--limit", str(limit)]
    if label:
        args += ["--label", label]
    args += ["--json", ",".join(json_fields or [])]
    output = _gh(args)
    if not output.strip():
        return []
    return json.loads(output)


def secret_set(name: str, value: str, *, repo: str) -> None:
    """`gh secret set <name> -R <repo>` reading the value from stdin. Raises GhError.

    A helper (not a plain REST PUT) because setting an Actions secret requires
    libsodium-sealing the value against the repo's public key — `gh secret set`
    does that encryption transparently; a raw `gh api` call cannot.
    """
    _gh(["secret", "set", name, "-R", repo], input_text=value)


def secret_list(repo: str) -> list[str]:
    """`gh secret list -R <repo>` → list of secret names. Raises GhError.

    Porcelain over the Actions-secrets surface, the read-side companion to
    :func:`secret_set` (it is paired with it to verify a set actually persisted).
    Output is the tab-separated `gh secret list` table; only the name column is
    returned.
    """
    output = _gh(["secret", "list", "-R", repo])
    names = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        names.append(line.split()[0])
    return names


def graphql(query: str, **variables: object) -> dict:
    """Run a GraphQL query/mutation; check payload['errors']; return the data dict."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        # Omit None entirely: an unprovided nullable GraphQL variable defaults
        # to null. Passing it through would send the literal string "None".
        if value is None:
            continue
        # -F type-infers ints/bools; -f forces a string (needed for ID! vars).
        flag = "-F" if isinstance(value, (int, bool)) else "-f"
        args += [flag, f"{key}={value}"]
    payload = json.loads(_gh(args))
    if payload.get("errors"):
        raise GhError(f"graphql errors: {payload['errors']}")
    return payload["data"]


def git(args: list[str], *, cwd: str | None = None, check: bool = True) -> str:
    """git porcelain via proc.out. e.g. ``git(['rev-parse', '--show-toplevel'])``."""
    return proc.out(["git", *args], cwd=cwd, check=check)


def repo_root(start: str | None = None) -> str:
    """``git rev-parse --show-toplevel``, resolved to a real path."""
    import os

    top = git(["rev-parse", "--show-toplevel"], cwd=start)
    return os.path.realpath(top)
