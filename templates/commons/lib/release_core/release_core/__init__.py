"""release_core — shared stdlib-only primitives for release's Python verbs.

The migration substrate: one place for the
subprocess/gh/git boundary, the CLI arg harness, YAML I/O, semver math, and
Kind/manifest detection that the per-verb modules build on. The package ships
to consumers as a pip wheel (install-release-core at SessionStart); its
console-scripts (changelog, semver, …) land on PATH from that install.

Curated re-exports: the names a verb module reaches for most.
"""

from __future__ import annotations

from . import cli, gh, manifest, proc, version, yamlio
from .cli import EXIT_OK, EXIT_USAGE, Opt, parse
from .gh import GhError
from .proc import ProcError, out, run

__version__ = "0.0.1"

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
