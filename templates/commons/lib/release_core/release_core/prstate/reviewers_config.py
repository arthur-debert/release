"""The required-reviewer SET + per-reviewer rerun policy — config, not code.

Which reviewers GATE Ready, and whether each re-reviews every push, is policy
that changes with reviewer pricing and availability, so it must be a one-line
config edit with no code change (release#622). This module is the single place
that resolves that config:

  * `DEFAULT_REVIEWERS` — the declarative default shipped for every consumer:
    Copilot only, review-once (rerun=False). CodeRabbit is a registered,
    requestable adapter being PILOTED on the phos-org repos (where the GitHub
    App is installed) — a pilot repo opts in via the override below; requiring
    it by default would gate every other repo on an app that is not installed
    there (the request edge silently drops, #613-style, and the PR parks at
    REVIEWS_PENDING forever).
  * a per-repo OVERRIDE — the optional `reviewers:` key in the consumer's
    existing `.release-sync.yaml` (the same file that already carries
    `capabilities:`). No NEW tracked consumer file.

The `reviewers:` value is a MAP from reviewer name to an options dict; the map
KEYS are the required reviewers (all must be DONE to flip Ready). The one
supported option is `rerun: bool` — whether the reviewer re-reviews every new
head (default **False**: review once). A LIST shorthand
(`reviewers: [copilot, codex]`) is also accepted, meaning all those required
with rerun=False.

NEW POLICY: re-run-on-push is per-reviewer and defaults OFF for EVERYONE. All
reviewers are token-billed now (and local agents cost a real model run each
time), so re-reviewing each push is explicit opt-in, not the default.

NO BACKWARDS COMPAT: the old `required_reviewers:` LIST key is gone. A
`.release-sync.yaml` still carrying it fails LOUD with a migration message.

Names map to adapters in the registry (#558); an unknown / non-requestable name
fails LOUD (`RequiredReviewersConfigError`) rather than silently dropping a
required gate.

`resolve_reviewers` takes the override as data (already parsed), keeping THIS
module pure and unit-testable; the thin `load_override` seam is the only thing
that touches YAML, mirroring `ghapi`/`yamlio` boundaries.
"""

from __future__ import annotations

import os

from .reviewers import REGISTRY, ReviewerAdapter, by_name

# The shipped default: Copilot required, review-once. Changing the required set
# (or a rerun flag) for ALL consumers is editing this one literal; a single
# consumer overrides it in its own `.release-sync.yaml` (the phos pilot repos
# add coderabbit there — see module docstring).
DEFAULT_REVIEWERS: dict[str, bool] = {"copilot": False}

# The override key + the file that carries it (the existing optional consumer
# override — see module docstring). Named here so the doc and the loader agree.
OVERRIDE_FILE = ".release-sync.yaml"
OVERRIDE_KEY = "reviewers"
# The retired key — present only to fail loud with a migration message.
_RETIRED_KEY = "required_reviewers"


class RequiredReviewersConfigError(RuntimeError):
    """The `reviewers:` config is invalid — any of: an unknown name, a
    non-requestable reviewer in the required set, a duplicate name, a wrong-typed
    value, an unknown per-reviewer option, or the retired `required_reviewers:`
    key. One error type for the whole config surface; the message says which."""


def resolve_reviewers(override: dict[str, bool] | None = None) -> dict[str, bool]:
    """The required reviewers + their rerun flags: the override if given+non-empty,
    else the default.

    Pure: the caller passes the already-parsed override map (or None). An empty
    map is treated as "unset" — a consumer cannot accidentally disable ALL
    review gating by writing `reviewers: {}`; that falls back to the default.
    (Removing review gating entirely is not a config the loop offers.)
    """
    resolved = dict(override) if override else dict(DEFAULT_REVIEWERS)
    _validate(tuple(resolved))
    return resolved


def resolve_required_names(override: dict[str, bool] | None = None) -> tuple[str, ...]:
    """The required-reviewer names (map keys), in config order."""
    return tuple(resolve_reviewers(override))


def reviewer_rerun(override: dict[str, bool] | None = None) -> dict[str, bool]:
    """The per-reviewer rerun flags (name -> bool), defaulting False for any
    required reviewer that doesn't specify one."""
    return dict(resolve_reviewers(override))


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
        raise RequiredReviewersConfigError(
            f"unknown required reviewer(s) {unknown} in {OVERRIDE_FILE} "
            f"`{OVERRIDE_KEY}` — known adapters: {sorted(known)}"
        )
    not_requestable = [n for n in names if n.lower() not in requestable]
    if not_requestable:
        raise RequiredReviewersConfigError(
            f"non-requestable reviewer(s) {not_requestable} cannot be required "
            f"in {OVERRIDE_FILE} `{OVERRIDE_KEY}`: a reviewer with no request "
            f"mechanism can never satisfy the gate — requestable adapters: "
            f"{sorted(requestable)}"
        )
    duplicates = sorted({n for n in lowered if lowered.count(n) > 1})
    if duplicates:
        raise RequiredReviewersConfigError(
            f"duplicate required reviewer(s) {duplicates} in {OVERRIDE_FILE} "
            f"`{OVERRIDE_KEY}` — list each reviewer once"
        )


def _parse_override_value(value: object) -> dict[str, bool]:
    """Parse the raw `reviewers:` YAML value into a name -> rerun map.

    Accepts two shapes:
      * a MAP  `{copilot: {rerun: false}, codex: {rerun: true}}` — keys are the
        required reviewers, each value an options dict (only `rerun: bool`).
      * a LIST `[copilot, codex]` — ergonomic shorthand: all required, rerun=False.

    Wrong-typed values / unknown options fail LOUD. Reviewer-name keys are
    normalized to their canonical adapter name (lowercase) so the resulting map
    is keyed the SAME way the adapters read it (`ctx.reviewer_rerun.get(adapter
    .name, ...)`); a `Copilot` key therefore APPLIES its rerun flag instead of
    silently degrading to review-once (release#852). Two differently-cased keys
    that canonicalize to the same adapter (`Copilot` + `copilot`) collide → a
    loud duplicate error, the same as a repeated list entry."""
    if isinstance(value, list):
        if not all(isinstance(x, str) for x in value):
            raise RequiredReviewersConfigError(
                f"{OVERRIDE_FILE} `{OVERRIDE_KEY}` list shorthand must be a list of reviewer names"
            )
        canon = [_canonical_name(name) for name in value]
        _reject_duplicate_names(canon)
        return {name: False for name in canon}

    if isinstance(value, dict):
        out: dict[str, bool] = {}
        for name, opts in value.items():
            if not isinstance(name, str):
                raise RequiredReviewersConfigError(
                    f"{OVERRIDE_FILE} `{OVERRIDE_KEY}` keys must be reviewer names"
                )
            key = _canonical_name(name)
            # The duplicate guard catches differently-cased keys that collide to
            # one adapter (e.g. `Copilot` + `copilot`) — YAML allows that, but it
            # is always a typo, never two distinct gates. (`_reject_duplicate_names`
            # reports the full collision set; here we fail on first overwrite so
            # the per-reviewer options are never silently clobbered.)
            if key in out:
                _reject_duplicate_names([*out, key])
            out[key] = _parse_options(name, opts)
        return out

    raise RequiredReviewersConfigError(
        f"{OVERRIDE_FILE} `{OVERRIDE_KEY}` must be a map of reviewer -> "
        "{{rerun: bool}} (or a list of reviewer names for rerun=false)"
    )


def _canonical_name(name: str) -> str:
    """The canonical adapter name for `name` (the `--reviewer` selector / map key).

    Resolves through the SAME registry lookup the required-set validation uses
    (`by_name`, which lowercases), so a key keys the rerun map by `adapter.name`
    (lowercase) — exactly what the adapters read off the context. An unknown
    name has no adapter to canonicalize to; it is passed through lowercased so
    `_validate` raises the precise unknown-reviewer error (with the known set)
    rather than this seam swallowing it."""
    adapter = by_name(name)
    return adapter.name if adapter is not None else name.lower()


def _reject_duplicate_names(names: list[str]) -> None:
    """Fail LOUD on any reviewer name that appears more than once (post-canon).

    Both config shapes run this on canonicalized names: the list shorthand can
    repeat a name outright (`[copilot, copilot]`), and the map can collide two
    differently-cased keys onto one adapter (`Copilot` + `copilot`). YAML's own
    duplicate-key rejection only covers byte-identical map keys, so the collision
    case slips past it — this is the one check that catches both."""
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise RequiredReviewersConfigError(
            f"duplicate required reviewer(s) {duplicates} in {OVERRIDE_FILE} "
            f"`{OVERRIDE_KEY}` — list each reviewer once "
            "(names are matched case-insensitively, so `Copilot` and `copilot` collide)"
        )


def _parse_options(name: str, opts: object) -> bool:
    """Parse one reviewer's options dict into its rerun flag (default False).

    A `null`/empty value (`copilot:` with nothing under it) means defaults —
    rerun=False. Anything other than a dict, an unknown option, or a non-bool
    `rerun` fails loud."""
    if opts is None:
        return False
    if not isinstance(opts, dict):
        raise RequiredReviewersConfigError(
            f"{OVERRIDE_FILE} `{OVERRIDE_KEY}.{name}` must be an options map "
            "(e.g. {{rerun: true}}) or empty for defaults"
        )
    unknown = sorted(k for k in opts if k != "rerun")
    if unknown:
        raise RequiredReviewersConfigError(
            f"{OVERRIDE_FILE} `{OVERRIDE_KEY}.{name}` has unknown option(s) "
            f"{unknown} — the only supported option is `rerun: bool`"
        )
    rerun = opts.get("rerun", False)
    if not isinstance(rerun, bool):
        raise RequiredReviewersConfigError(
            f"{OVERRIDE_FILE} `{OVERRIDE_KEY}.{name}.rerun` must be a boolean"
        )
    return rerun


def load_override(root: str | None = None) -> dict[str, bool] | None:
    """Read `reviewers:` from the consumer's `.release-sync.yaml`.

    Returns the parsed name -> rerun map, or None when the file/key is absent.
    The ONE YAML seam in this module (via `manifest.load_sync_config`);
    everything else is pure data. Fails LOUD if the retired `required_reviewers:`
    key is still present (no backwards compat)."""
    # Imported lazily so the pure engine doesn't pull the yq-backed yamlio path
    # unless an override actually needs reading.
    from .. import manifest

    d = root if root is not None else "."
    if not os.path.isfile(os.path.join(d, OVERRIDE_FILE)):
        return None
    cfg = manifest.load_sync_config(d)
    if _RETIRED_KEY in cfg:
        raise RequiredReviewersConfigError(
            f"{OVERRIDE_FILE} `{_RETIRED_KEY}` is replaced by the `{OVERRIDE_KEY}:` "
            "map — e.g. `reviewers: {copilot: {rerun: false}}` (or the list "
            "shorthand `reviewers: [copilot]`); rerun defaults to false (review once)"
        )
    value = cfg.get(OVERRIDE_KEY)
    if value is None:
        return None
    return _parse_override_value(value)


def required_reviewers(names: tuple[str, ...]) -> list[ReviewerAdapter]:
    """Map required names → their registry adapters, preserving config order.

    `_validate` guarantees every name resolves, so `by_name` never returns None
    here; the explicit guard turns any future registry/validation mismatch into a
    loud error instead of a None leaking to callers (keeps the return type a
    clean `list[ReviewerAdapter]`)."""
    _validate(names)
    adapters: list[ReviewerAdapter] = []
    for n in names:
        adapter = by_name(n)
        if adapter is None:  # unreachable post-_validate — fail loud if it isn't
            raise RequiredReviewersConfigError(
                f"required reviewer {n!r} has no adapter after validation"
            )
        adapters.append(adapter)
    return adapters
