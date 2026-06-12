"""The required-reviewer SET — a config knob, not code.

Which reviewers GATE Ready is a policy that changes with reviewer pricing and
availability, so it must be a one-line config edit with no code change
(release#622). This module is the single place that resolves the required SET:

  * `DEFAULT_REQUIRED` — the declarative default shipped for every consumer:
    Copilot + CodeRabbit, both required (parallel-required, not fallback).
  * a per-repo OVERRIDE — the optional `required_reviewers:` key in the
    consumer's existing `.release-sync.yaml` (the same file that already carries
    `capabilities:`). No NEW tracked consumer file: a repo that wants a
    different set (just `[coderabbit]`, or a third reviewer) lists it there and
    NOTHING in the engine changes.

Names map to adapters in the registry (#558); an unknown name fails LOUD
(`UnknownReviewerError`) rather than silently dropping a required gate.

`resolve_required_names` takes the override list as data (already parsed),
keeping THIS module pure and unit-testable; the thin `load_override` seam is
the only thing that touches YAML, mirroring `ghapi`/`yamlio` boundaries.
"""

from __future__ import annotations

import os

from .reviewers import REGISTRY, ReviewerAdapter, by_name

# The shipped default: both required, in order. Changing the required set for
# ALL consumers is editing this one line; a single consumer overrides it in its
# own `.release-sync.yaml`.
DEFAULT_REQUIRED: tuple[str, ...] = ("copilot", "coderabbit")

# The override key + the file that carries it (the existing optional consumer
# override — see module docstring). Named here so the doc and the loader agree.
OVERRIDE_FILE = ".release-sync.yaml"
OVERRIDE_KEY = "required_reviewers"


class UnknownReviewerError(RuntimeError):
    """A configured required-reviewer name has no adapter in the registry."""


def resolve_required_names(override: list[str] | None = None) -> tuple[str, ...]:
    """The required-reviewer names: the override if given+non-empty, else default.

    Pure: the caller passes the already-parsed override list (or None). An empty
    list is treated as "unset" — a consumer cannot accidentally disable ALL
    review gating by writing `required_reviewers: []`; that falls back to the
    default. (Removing review gating entirely is not a config the loop offers.)
    """
    names = tuple(override) if override else DEFAULT_REQUIRED
    _validate(names)
    return names


def _validate(names: tuple[str, ...]) -> None:
    """A required set is valid only if every name is a REQUESTABLE adapter and
    no name repeats.

    Requestable is load-bearing: a reviewer with no request mechanism (Gemini)
    can never satisfy a required gate — the engine would forever advise
    "request gemini" while `pr review request` only no-ops. Rejecting it here,
    at parse time, turns that silent dead-end into a loud config error. A
    duplicate name is also rejected — a repeated gate is always a typo, never
    intent."""
    requestable = {r.name for r in REGISTRY if r.requestable}
    known = {r.name for r in REGISTRY}
    lowered = [n.lower() for n in names]

    unknown = [n for n in names if n.lower() not in known]
    if unknown:
        raise UnknownReviewerError(
            f"unknown required reviewer(s) {unknown} in {OVERRIDE_FILE} "
            f"`{OVERRIDE_KEY}` — known adapters: {sorted(known)}"
        )
    not_requestable = [n for n in names if n.lower() not in requestable]
    if not_requestable:
        raise UnknownReviewerError(
            f"non-requestable reviewer(s) {not_requestable} cannot be required "
            f"in {OVERRIDE_FILE} `{OVERRIDE_KEY}`: a reviewer with no request "
            f"mechanism can never satisfy the gate — requestable adapters: "
            f"{sorted(requestable)}"
        )
    duplicates = sorted({n for n in lowered if lowered.count(n) > 1})
    if duplicates:
        raise UnknownReviewerError(
            f"duplicate required reviewer(s) {duplicates} in {OVERRIDE_FILE} "
            f"`{OVERRIDE_KEY}` — list each reviewer once"
        )


def load_override(root: str | None = None) -> list[str] | None:
    """Read `required_reviewers:` from the consumer's `.release-sync.yaml`.

    Returns the list, or None when the file/key is absent. The ONE YAML seam in
    this module (via `manifest.load_sync_config`); everything else is pure data.
    """
    # Imported lazily so the pure engine doesn't pull the yq-backed yamlio path
    # unless an override actually needs reading.
    from .. import manifest

    d = root if root is not None else "."
    if not os.path.isfile(os.path.join(d, OVERRIDE_FILE)):
        return None
    cfg = manifest.load_sync_config(d)
    value = cfg.get(OVERRIDE_KEY)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise UnknownReviewerError(
            f"{OVERRIDE_FILE} `{OVERRIDE_KEY}` must be a list of reviewer names"
        )
    return value


def required_reviewers(names: tuple[str, ...]) -> list[ReviewerAdapter]:
    """Map required names → their registry adapters, preserving config order.

    `_validate` guarantees every name resolves, so `by_name` never returns None
    here; the explicit guard turns any future registry/validation drift into a
    loud error instead of a None leaking to callers (keeps the return type a
    clean `list[ReviewerAdapter]`)."""
    _validate(names)
    adapters: list[ReviewerAdapter] = []
    for n in names:
        adapter = by_name(n)
        if adapter is None:  # unreachable post-_validate — fail loud if it isn't
            raise UnknownReviewerError(f"required reviewer {n!r} has no adapter after validation")
        adapters.append(adapter)
    return adapters
