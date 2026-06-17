"""yamlio — YAML read, the one thing the stdlib can't do.

Shells out to ``yq`` (a required external CLI in release) and parses its JSON
output with the stdlib. Two ``yq`` flavors exist in the wild and emit JSON
differently, so this seam detects which is on PATH and adapts the read path:

  * **mikefarah/yq v4** (the canonical one) — JSON via ``yq -o=json '.' FILE``.
  * **kislyuk python-yq** (a jq wrapper that squats ``/usr/bin/yq`` in some
    cloud images) — emits JSON *by default*: ``yq '.' FILE``; it rejects the
    mikefarah ``-o=json`` flag (the jq layer errors ``Unknown option -o=json``).

Adapting ``load``/``loads`` means a stray python-yq no longer crashes every
``yamlio.load`` caller. ``eval_all`` (the YAML→YAML deep-merge seam) is
mikefarah-only and raises a clear error when the wrong flavor is present rather
than surfacing a cryptic jq message.

PyYAML stays deliberately out (pyproject: the wheel is a no-compile, click-only
pull); this module is the single ``yq`` boundary — if that decision is ever
revisited, only this file changes.
"""

from __future__ import annotations

import json
import shutil

from . import proc


class YamlError(RuntimeError):
    """``yq`` is unavailable, the wrong flavor, or failed to parse the document."""


def _require_yq() -> None:
    if shutil.which("yq") is None:
        raise YamlError("`yq` CLI not found on PATH")


def classify_yq_version(version_output: str) -> str:
    """Classify a ``yq --version`` banner → ``'mikefarah'`` | ``'other'``.

    mikefarah prints ``yq (https://github.com/mikefarah/yq/) version v4.x``;
    kislyuk python-yq prints ``yq 3.4.3`` / ``yq 0.0.0``. Pure (no subprocess),
    so the classification is unit-tested without a real binary.
    """
    return "mikefarah" if "mikefarah" in version_output.lower() else "other"


def _yq_flavor() -> str:
    """The on-PATH ``yq`` flavor; raises ``YamlError`` if no ``yq`` at all."""
    _require_yq()
    result = proc.run(["yq", "--version"], check=False)
    if result.returncode != 0:
        return "other"
    banner = (result.stdout or "") + (result.stderr or "")
    return classify_yq_version(banner)


def _run_yq(args: list[str], *, input_text: str | None = None) -> str:
    _require_yq()
    result = proc.run(["yq", *args], input=input_text, check=False)
    if result.returncode != 0:
        raise YamlError(
            f"yq {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _json_args(filter_and_rest: list[str]) -> list[str]:
    """Args for JSON output: mikefarah needs ``-o=json``; kislyuk defaults to it."""
    if _yq_flavor() == "mikefarah":
        return ["-o=json", *filter_and_rest]
    return filter_and_rest


def load(path: str) -> object:
    """Parse the YAML file at ``path`` → Python object."""
    out = _run_yq(_json_args([".", path]))
    if not out.strip():
        return None
    return json.loads(out)


def loads(text: str) -> object:
    """Parse a YAML string → Python object."""
    out = _run_yq(_json_args(["."]), input_text=text)
    if not out.strip():
        return None
    return json.loads(out)


def eval_all(expr: str, files: list[str]) -> str:
    """``yq eval-all '<expr>' <files...>`` → raw YAML stdout (NOT JSON).

    The YAML→YAML deep-merge seam for release-sync's lefthook.yml composition
    (the bash piped fragments through ``yq eval-all '. as $i ireduce({}; . *+
    $i) | ... comments=""'`` to merge in order and strip comments). This is
    mikefarah-only syntax; raise clearly when a non-mikefarah ``yq`` is on PATH
    instead of emitting a cryptic jq error."""
    if _yq_flavor() != "mikefarah":
        raise YamlError(
            "yq eval-all requires mikefarah/yq v4 (the YAML-merge seam); a "
            "non-mikefarah `yq` (e.g. kislyuk python-yq) is on PATH — install "
            "mikefarah/yq v4 (see env/setup.sh)"
        )
    return _run_yq(["eval-all", expr, *files])
