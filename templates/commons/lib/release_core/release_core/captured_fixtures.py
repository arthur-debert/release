"""captured_fixtures — the external-surface fixture provenance lint (#623).

The recurring defect (three green-suite/wrong-world bugs in 24h, #619/#620/#612):
a mock or fixture encodes the AUTHOR'S BELIEF about an external surface — a
lefthook log, a gh/GitHub-API JSON payload, workflow YAML read via the contents
API, git command output — and then the suite verifies the production code
against that belief. The suite is self-consistently *wrong*: every test passes
while the real surface differs. #619 is the worked example — `classify.py` read
the lefthook summary with a `🥊` regex (a tty-mode guess); captured (piped) logs
emit `✗`, and the fixture was invented to match the regex, so 1,300 tests were
green while every live npm-artifact FAIL classified as unexpected.

The cure is a forcing function, not prose discipline (prose loses to momentum):
any fixture representing an external surface must carry a machine-readable
provenance marker — a ``captured-from:`` line naming the producing command or
source plus a date — so "where did these bytes come from" always has an answer
in the file. This module is the lint that enforces it.

Identification heuristic (the design core — START NARROW): we do NOT guess
"is this an external surface?" repo-wide. We name the known seam sources
explicitly in ``SEAMS``/``INLINE_SEAMS`` below — the dirs and inline-constant
locations that feed captured external payloads into the engine under test. Every
fixture under a registered seam must carry the marker; a new unmarked one fails
the lint.

The ratchet is SHRINK-ONLY, modelled byte-for-byte on the consumer-contract
lint (``contract.apply_baseline``): today's unmarked offenders are grandfathered
in ``BASELINE_RELPATH``; a NEW unmarked fixture fails; a baseline entry that no
longer matches any offender ALSO fails ("stale — delete it"). The baseline only
ever shrinks — adding an entry is never the fix for a new finding (capture the
fixture for real instead).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import yamlio


class CapturedFixtureError(Exception):
    """A hard error reading the seam tree or the baseline."""


# The machine-readable provenance marker. A fixture representing an external
# surface MUST contain this token, followed by the producing command/source +
# a date — whether as a JSON ``"captured-from"`` key, a YAML/`# ` comment, or a
# marker comment above an inline test constant. The lint only checks PRESENCE
# (mechanical, like the consumer-contract lint); a human reviewer judges whether
# the named source is real capture vs a hand-shape.
MARKER = "captured-from"

# Relative to the release_core package dir: this file's parent is the
# `release_core/` package; its parent is the lib root that holds both the
# package and its `tests/`.
_LIB_ROOT_FROM_PKG = os.pardir


@dataclass(frozen=True)
class Seam:
    """One registered external-surface seam backed by a dir of fixture files.

    ``fixture_dir``  — path (relative to the release_core lib root) to a dir of
                       fixture files fed to the engine as captured external
                       payloads. Every file matching ``suffixes`` must carry the
                       marker.
    ``suffixes``     — file extensions that count as fixtures in ``fixture_dir``.
    ``surface``      — human label for the external producer (messages/docs).
    """

    name: str
    fixture_dir: str
    suffixes: tuple[str, ...]
    surface: str


# The registered file-fixture seams — the NARROW, named set (#623): start with
# the modules where a captured-vs-believed mismatch already bit us. The prstate
# dir holds gh-API PR payloads fed straight to the state engine.
SEAMS: tuple[Seam, ...] = (
    Seam(
        name="prstate",
        fixture_dir="tests/prstate_fixtures",
        suffixes=(".json",),
        surface="gh / GitHub API PR payloads (graphql meta + REST reviews/threads)",
    ),
)

# Inline-constant seams: a test module that embeds a captured external surface as
# a Python string constant. The convention requires a `captured-from:` marker
# comment near the constant; the lint asserts the marker token appears in the
# file. (Mechanical presence check — same shape as the file seam.)
INLINE_SEAMS: tuple[tuple[str, str, str], ...] = (
    # (name, test-file path relative to lib root, surface label)
    (
        "classify",
        "tests/test_core_classify.py",
        "pinned lefthook (2.1.9) piped summary block — _GATE_LOG (#619)",
    ),
    (
        "apply_ruleset",
        "tests/test_core_apply_ruleset.py",
        "workflow YAML as read via the GitHub contents API",
    ),
)

BASELINE_RELPATH = "tests/captured-fixture-lint-baseline.yaml"


@dataclass(frozen=True)
class Offender:
    """A fixture lacking the provenance marker. ``key`` is the stable
    ``seam/relpath`` identity matched against the baseline."""

    seam: str
    path: str  # relpath from the lib root, for messages
    surface: str

    @property
    def key(self) -> str:
        return f"{self.seam}/{self.path}"


def _lib_root(start: str | None = None) -> str:
    """The release_core lib root (the dir holding both `release_core/` and
    `tests/`). ``start`` defaults to this package dir."""
    pkg = start or os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(pkg, _LIB_ROOT_FROM_PKG))


def _has_marker(text: str, *, is_json: bool) -> bool:
    """Whether ``text`` carries the provenance marker.

    A bare substring search would false-positive — a hand-shaped JSON fixture
    could mention the token in a prose ``_note`` value without actually
    declaring provenance. So the check is structural per content type:

      JSON fixtures  — parse and require a TOP-LEVEL ``"captured-from"`` key
                       (a non-empty string value). A token buried in a `_note`
                       does not count.
      everything else (inline test files, YAML) — require the marker *form*
                       ``captured-from:`` (token immediately followed by a
                       colon), the documented comment shape, not a stray
                       mention of the bare word.
    """
    if is_json:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        val = data.get(MARKER)
        return isinstance(val, str) and val.strip() != ""
    return f"{MARKER}:" in text


def scan(lib_root: str | None = None) -> list[Offender]:
    """Sweep every registered seam; return the fixtures missing the marker.

    Deterministic order (seam registration order, then sorted path) so the
    baseline diff is stable.
    """
    root = lib_root or _lib_root()
    offenders: list[Offender] = []

    for seam in SEAMS:
        d = os.path.join(root, seam.fixture_dir)
        if not os.path.isdir(d):
            raise CapturedFixtureError(
                f"captured-fixtures: registered seam dir missing: {seam.fixture_dir} "
                f"(seam {seam.name!r}) — fix SEAMS in captured_fixtures.py"
            )
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(seam.suffixes):
                continue
            fpath = os.path.join(d, fname)
            with open(fpath, encoding="utf-8") as fh:
                text = fh.read()
            if not _has_marker(text, is_json=fname.endswith(".json")):
                rel = os.path.join(seam.fixture_dir, fname)
                offenders.append(Offender(seam=seam.name, path=rel, surface=seam.surface))

    for name, relpath, surface in INLINE_SEAMS:
        fpath = os.path.join(root, relpath)
        if not os.path.isfile(fpath):
            raise CapturedFixtureError(
                f"captured-fixtures: registered inline seam file missing: {relpath} "
                f"(seam {name!r}) — fix INLINE_SEAMS in captured_fixtures.py"
            )
        with open(fpath, encoding="utf-8") as fh:
            text = fh.read()
        if not _has_marker(text, is_json=False):
            offenders.append(Offender(seam=name, path=relpath, surface=surface))

    return offenders


def load_baseline(lib_root: str | None = None) -> list[dict]:
    """The hand-maintained shrink-only baseline (``known_unmarked_fixtures``:
    a list of ``{key, reason}``). A missing file is an empty baseline."""
    root = lib_root or _lib_root()
    path = os.path.join(root, BASELINE_RELPATH)
    if not os.path.isfile(path):
        return []
    # yamlio.load shells out to yq: a missing yq / parse failure raises
    # yamlio.YamlError, and a non-JSON yq emission raises ValueError. Surface
    # both as CapturedFixtureError so the verb's single except yields a clean,
    # actionable gate message instead of an unhandled traceback.
    try:
        data = yamlio.load(path)
    except (yamlio.YamlError, ValueError) as exc:
        raise CapturedFixtureError(
            f"captured-fixtures: cannot read baseline {BASELINE_RELPATH}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise CapturedFixtureError(f"captured-fixtures: malformed baseline {BASELINE_RELPATH}")
    entries = data.get("known_unmarked_fixtures") or []
    if not isinstance(entries, list):
        raise CapturedFixtureError(f"captured-fixtures: malformed baseline {BASELINE_RELPATH}")
    for e in entries:
        if not isinstance(e, dict) or "key" not in e:
            raise CapturedFixtureError(
                f"captured-fixtures: baseline entry missing key: {e!r} ({BASELINE_RELPATH})"
            )
    return entries


def apply_baseline(
    offenders: list[Offender], baseline: list[dict]
) -> tuple[list[Offender], list[Offender], list[dict]]:
    """Partition the sweep against the baseline — the SHRINK-ONLY ratchet,
    identical in shape to ``contract.apply_baseline``.

    Returns ``(failing, grandfathered, stale_entries)``:
      failing        — offenders NOT in the baseline (lint fails);
      grandfathered  — offenders covered by an entry (reported, non-failing);
      stale_entries  — baseline entries matching NO offender (lint fails too:
                       the fixture got a marker, so delete the entry — the
                       baseline only ever shrinks).
    """
    keyed = {str(e["key"]): e for e in baseline}
    failing: list[Offender] = []
    grandfathered: list[Offender] = []
    hit: set[str] = set()
    for o in offenders:
        if o.key in keyed:
            grandfathered.append(o)
            hit.add(o.key)
        else:
            failing.append(o)
    stale = [e for key, e in keyed.items() if key not in hit]
    return failing, grandfathered, stale
