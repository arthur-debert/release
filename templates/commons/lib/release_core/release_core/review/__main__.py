"""Module entry: ``python -m release_core.review <agent> …`` runs the facade.

This is the Phase 1.2 entry point. It is deliberately separate from the Click
``release-core`` CLI — wiring the review into that tree is a later phase.
"""

from __future__ import annotations

import sys

from .facade import run_review

if __name__ == "__main__":
    sys.exit(run_review(sys.argv[1:]))
