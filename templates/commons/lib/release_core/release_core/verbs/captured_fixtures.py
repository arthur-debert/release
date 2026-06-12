"""captured-fixtures — the external-surface fixture provenance lint verb (#623).

This is release's OWN dev gate over release_core's tests/ — like lint-skills it
has NO consumer-facing CLI surface (it is not wired into the `release-core admin`
tree and not synced to consumers). The gate invokes it via the module
entrypoint, never via a click command:

  python3 -m release_core.verbs.captured_fixtures lint
  # in practice through the working-tree wrapper:
  bin-internal/lint-captured-fixtures.sh

Subcommand:

  lint    Sweep the registered external-surface seams (prstate gh-API payload
          dir + the classify / apply_ruleset inline-constant test files) for
          fixtures lacking the `captured-from:` provenance marker. Reports
          `seam/path` per offender; exit 1 on any offender not grandfathered
          by the shrink-only baseline
          (tests/captured-fixture-lint-baseline.yaml), and on any STALE baseline
          entry (a fixture that since got a marker — delete the entry, the
          baseline only shrinks).

The wrapper runs the WORKING-TREE release_core via PYTHONPATH, never the
installed wheel, so a PR adding a fixture lints against the PR's own seam
registry. See docs/dev/captured-fixture-provenance.md for the convention.

Exit codes:
  0  — ok
  1  — unmarked new fixture / stale baseline entry / error
  64 — bad usage
"""

from __future__ import annotations

import sys

from .. import captured_fixtures as cf
from ..cli import EXIT_USAGE

USAGE = __doc__ or ""


def _lint() -> int:
    try:
        offenders = cf.scan()
        baseline = cf.load_baseline()
    except cf.CapturedFixtureError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    failing, grandfathered, stale = cf.apply_baseline(offenders, baseline)

    for o in grandfathered:
        print(f"captured-fixtures: baselined: {o.key}  [{o.surface}]")

    rc = 0
    for o in failing:
        print(
            f"captured-fixtures: VIOLATION: {o.key} represents an external surface "
            f"({o.surface}) but carries no {cf.MARKER!r} provenance marker. "
            "Capture it from the real producer and add the marker — a top-level "
            f'"{cf.MARKER}" key for JSON fixtures, or a `{cf.MARKER}:` comment '
            "for inline/YAML seams (see docs/dev/captured-fixture-provenance.md). "
            "Adding a baseline entry is NOT the fix.",
            file=sys.stderr,
        )
        rc = 1
    for e in stale:
        print(
            f"captured-fixtures: STALE baseline entry (now marked): {e['key']} — "
            f"delete it from {cf.BASELINE_RELPATH} (the baseline only shrinks).",
            file=sys.stderr,
        )
        rc = 1

    if rc == 0:
        n_seams = len(cf.SEAMS) + len(cf.INLINE_SEAMS)
        print(
            f"captured-fixtures: OK ({n_seams} seams swept, "
            f"{len(grandfathered)} baselined, 0 new unmarked)"
        )
    return rc


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE.strip(), file=sys.stderr)
        return EXIT_USAGE
    if argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0
    sub = argv[0]
    rest = argv[1:]
    for a in rest:
        if a in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        print(f"error: unknown argument: {a}", file=sys.stderr)
        return EXIT_USAGE
    if sub != "lint":
        print(f"error: unknown subcommand: {sub} (expected lint)", file=sys.stderr)
        return EXIT_USAGE
    return _lint()


def lint_main(argv: list[str]) -> int:
    return main(["lint", *argv])


if __name__ == "__main__":  # the bin-internal gate wrapper execs this module
    sys.exit(main(sys.argv[1:]))
