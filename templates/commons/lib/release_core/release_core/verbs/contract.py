"""contract — the consumer-contract manifest verbs (#584, epic #583 WS-A).

Subcommands (also reachable as `release-core admin contract <sub>`):

  dump    Regenerate docs/references/consumer-contract.yaml from sync.py's
          constants + predicates applied to this repo's templates/ tree, and
          write it in place. Byte-stable for an unchanged tree.
  check   Regenerate in memory and diff against the committed manifest.
          Exit 1 (with the diff) on drift — "run: release-core admin
          contract dump". This is the gate's freshness check.
  lint    The assumption lint: scan .github/workflows/*.yml,
          .github/actions/*/action.yml, and the workflow copies under
          templates/ for any JOB whose steps reference a managed path
          (manifest managed_path_prefixes + untracked_mirrors) without a
          prior materialize step in the same job (arm-gate / release-core
          init). Reports `file -> job -> step`; exit 1 on any violation not
          grandfathered by the shrink-only baseline
          (docs/references/consumer-contract-lint-baseline.yaml). The baseline
          was drained to zero and the file deleted in release#588 — a missing
          file is an empty baseline, and re-adding entries is never the fix.

Options:
  --root DIR   The release repo root (default: `git rev-parse --show-toplevel`).

Run from inside arthur-debert/release. The gate entries invoke this via the
WORKING TREE (bin-internal/{check,lint}-consumer-contract.sh set PYTHONPATH),
never the installed wheel — a PR changing sync.py classification must
regenerate/lint with the NEW code.

Exit codes:
  0  — ok
  1  — drift (check) / violations or stale baseline entries (lint) / error
  64 — bad usage
"""

from __future__ import annotations

import difflib
import os
import sys

from .. import contract, proc, yamlio
from ..cli import EXIT_USAGE

USAGE = __doc__ or ""


def _resolve_root(root_arg: str | None) -> str:
    if root_arg:
        root = root_arg
    else:
        try:
            root = proc.out(["git", "rev-parse", "--show-toplevel"]).strip()
        except proc.ProcError:
            root = os.getcwd()
    if not os.path.isdir(os.path.join(root, "templates", "commons")):
        raise contract.ContractError(
            f"contract: {root!r} is not the release repo root (no templates/commons) "
            "— run from inside arthur-debert/release or pass --root"
        )
    return root


def _manifest_path(root: str) -> str:
    return os.path.join(root, contract.MANIFEST_RELPATH)


def _dump(root: str) -> int:
    text = contract.manifest_text(root)
    path = _manifest_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"contract dump: wrote {contract.MANIFEST_RELPATH}")
    return 0


def _check(root: str) -> int:
    path = _manifest_path(root)
    if not os.path.isfile(path):
        print(
            f"contract check: {contract.MANIFEST_RELPATH} is missing — "
            "run: release-core admin contract dump",
            file=sys.stderr,
        )
        return 1
    with open(path, encoding="utf-8") as fh:
        committed = fh.read()
    expected = contract.manifest_text(root)
    if committed == expected:
        print(f"contract check: {contract.MANIFEST_RELPATH} is current")
        return 0
    diff = difflib.unified_diff(
        committed.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"committed/{contract.MANIFEST_RELPATH}",
        tofile=f"generated/{contract.MANIFEST_RELPATH}",
    )
    sys.stderr.writelines(diff)
    print(
        "\ncontract check: FAIL — the committed manifest has drifted from "
        "sync.py + templates/. A contract-changing PR regenerates it in the "
        "SAME PR: run `release-core admin contract dump` and commit the result.",
        file=sys.stderr,
    )
    return 1


def _lint(root: str) -> int:
    path = _manifest_path(root)
    if not os.path.isfile(path):
        print(
            f"contract lint: {contract.MANIFEST_RELPATH} is missing — "
            "run: release-core admin contract dump",
            file=sys.stderr,
        )
        return 1
    manifest = yamlio.load(path)
    if not isinstance(manifest, dict):
        print(f"contract lint: malformed {contract.MANIFEST_RELPATH}", file=sys.stderr)
        return 1

    violations = contract.lint_repo(root, manifest)
    baseline = contract.load_baseline(root)
    failing, grandfathered, stale = contract.apply_baseline(violations, baseline)

    for v in grandfathered:
        print(
            f"contract lint: baselined (release#588): {v.file} -> {v.job} -> {v.step} [{v.matched}]"
        )

    rc = 0
    for v in failing:
        print(
            f"contract lint: VIOLATION: {v.file} -> {v.job} -> {v.step}: references "
            f"managed path {v.matched!r} with no prior materialize step in the job "
            "(add the arm-gate composite / a `release-core init` step BEFORE it)",
            file=sys.stderr,
        )
        rc = 1
    for e in stale:
        print(
            f"contract lint: STALE baseline entry (no matching violation): "
            f"{e['file']} -> {e['job']} — delete it from "
            f"{contract.BASELINE_RELPATH} (the baseline only shrinks)",
            file=sys.stderr,
        )
        rc = 1
    if rc == 0:
        n = len(contract.ci_surface_files(root))
        print(f"contract lint: OK ({n} CI surface files swept, {len(grandfathered)} baselined)")
    return rc


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE.strip(), file=sys.stderr)
        return EXIT_USAGE
    if argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    sub = argv[0]
    root_arg: str | None = None
    rest = argv[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--root":
            if i + 1 >= len(rest):
                print("error: --root requires a value", file=sys.stderr)
                return EXIT_USAGE
            root_arg = rest[i + 1]
            i += 2
            continue
        if rest[i] in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        print(f"error: unknown argument: {rest[i]}", file=sys.stderr)
        return EXIT_USAGE

    handlers = {"dump": _dump, "check": _check, "lint": _lint}
    handler = handlers.get(sub)
    if handler is None:
        print(f"error: unknown subcommand: {sub} (expected dump | check | lint)", file=sys.stderr)
        return EXIT_USAGE

    try:
        return handler(_resolve_root(root_arg))
    except contract.ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except yamlio.YamlError as exc:
        print(f"contract: {exc}", file=sys.stderr)
        return 1


# The click leaves (`release-core admin contract dump|check|lint`) forward here.
def dump_main(argv: list[str]) -> int:
    return main(["dump", *argv])


def check_main(argv: list[str]) -> int:
    return main(["check", *argv])


def lint_main(argv: list[str]) -> int:
    return main(["lint", *argv])


if __name__ == "__main__":  # the bin-internal gate wrappers exec this module
    sys.exit(main(sys.argv[1:]))
