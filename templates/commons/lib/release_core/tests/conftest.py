"""Shared fixture loader for the prstate (PR state engine) tests.

Each JSON file under prstate_fixtures/ holds the raw `gh` payloads for one PR
scenario; `context` builds a PullContext from one exactly as `fetch.gather()`
would, minus the network.

prstate_fixtures/ is a registered EXTERNAL-SURFACE seam (these bytes are the gh
/ GitHub API payloads the engine consumes), so every fixture must carry a
`captured-from:` provenance marker — enforced by the shrink-only captured-fixture
lint (release_core.captured_fixtures; docs/dev/captured-fixture-provenance.md).
The live_*pr342.json files are captured from a real probe PR (#337) and carry
their marker; the synthetic lifecycle scenarios are grandfathered in
tests/captured-fixture-lint-baseline.yaml and drain as each is re-captured. A
NEW prstate fixture must be captured-with-provenance, not hand-shaped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from release_core.prstate.fetch import context_from_raw
from release_core.prstate.model import PullContext

FIXTURES = Path(__file__).parent / "prstate_fixtures"


def load_context(name: str) -> PullContext:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return context_from_raw(
        meta=data["meta"],
        reviews_json=data.get("reviews", []),
        thread_nodes=data.get("threads", []),
        reactions=data.get("reactions", []),
        issue_comments=data.get("issue_comments", []),
    )


@pytest.fixture
def context():
    """Return the loader so a test can pick its scenario: `context('name')`."""
    return load_context
