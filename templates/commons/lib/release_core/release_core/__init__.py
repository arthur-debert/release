"""release_core — shared stdlib-only primitives for release's Python verbs.

The migration substrate: one place for the
subprocess/gh/git boundary, the CLI arg harness, YAML I/O, semver math, and
Kind/manifest detection that the per-verb modules build on. The package ships
to consumers as a pip wheel (install-release-core at SessionStart); its
console-scripts (changelog, semver, …) land on PATH from that install.

Curated re-exports: the names a verb module reaches for most.
"""

from __future__ import annotations

import importlib.metadata

from . import cli, gh, manifest, proc, version, yamlio
from ._version import DEV_VERSION
from .cli import EXIT_OK, EXIT_USAGE, Opt, parse
from .gh import GhError
from .proc import ProcError, out, run


def _resolve_version() -> str:
    """The single runtime version source for release_core (release#758).

    Returns the INSTALLED wheel's version (stamped from the release tag at build
    time — see release_core/_version.py + pyproject `[tool.hatch.version]`). When
    running from a source/editable checkout that was never `pip install`-ed —
    e.g. bin/release-core's PYTHONPATH shim, the test suite from source — the
    package metadata is absent, so fall back to the dev sentinel. Never the old
    hardcoded literal: `release-core --version` and both `__version__` attrs must
    reflect the real release line so pip / provenance can compare versions.

    Resolved via the ``importlib.metadata`` module attribute (not a bound
    import) so it reflects the live package set."""
    try:
        return importlib.metadata.version("release-core")
    except importlib.metadata.PackageNotFoundError:
        return DEV_VERSION


__version__ = _resolve_version()

__all__ = [
    "EXIT_OK",
    "EXIT_USAGE",
    "GhError",
    "Opt",
    "ProcError",
    "cli",
    "gh",
    "manifest",
    "out",
    "parse",
    "proc",
    "run",
    "version",
    "yamlio",
]
