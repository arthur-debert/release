"""Module entry: ``python -m release_core.review <agent> …`` runs the facade.

A thin ``python -m`` entry point, deliberately separate from the Click
``release-core`` CLI (which exposes the same facade as ``release-core review
run`` via :mod:`release_core.cli.review`).
"""

from __future__ import annotations

import sys

from .facade import run_review

if __name__ == "__main__":
    sys.exit(run_review(sys.argv[1:]))
