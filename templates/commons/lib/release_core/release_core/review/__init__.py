"""review — local code-review backends.

A generic, SINGLE-repo, single-PR review model: resolve a PR to its unified diff
(:func:`release_core.review.diff.resolve_pr`), build one shared prompt body from
review instructions + that diff, hand it to a pluggable agent backend (codex /
agy), parse the agent's JSON verdict, and post it back to the PR.

The phos-specific dual-repo (phos-core/phos-app) model is intentionally dropped
here — there is no `repository` enum. A backend may also be handed a diff as a
plain string directly, bypassing PR resolution.
"""

from __future__ import annotations
