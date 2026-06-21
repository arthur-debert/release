"""review — local code-review backends.

A generic, SINGLE-repo, single-PR review model: build one shared prompt body
from review instructions + a unified diff, hand it to a pluggable agent backend
(codex / agy), and parse the agent's JSON verdict.

The phos-specific dual-repo (phos-core/phos-app) model is intentionally dropped
here — there is no `repository` enum and no git/PR logic. The diff arrives as a
plain string; a later phase computes it from a PR.
"""

from __future__ import annotations
