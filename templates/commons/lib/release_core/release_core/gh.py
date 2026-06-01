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
    paginate: bool = False,
) -> object:
    """Call `gh api <path>` → parsed JSON (None on empty output). Raises GhError."""
    args = ["api"]
    if method:
        args += ["-X", method]
    if paginate:
        args.append("--paginate")
    for key, value in (fields or {}).items():
        args += ["-f", f"{key}={value}"]
    args.append(path)
    output = _gh(args)
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
