"""base — the review-backend interface.

A `Backend` wraps one agent CLI (codex, agy). The interface separates three
concerns so `--dry-run` is honest:

  * ``preflight()`` probes that the agent binary is reachable and raises a
    clear, actionable :class:`BackendUnavailable` if not (it never auto-starts
    anything);
  * ``build_command()`` returns a pure description of what *would* run — argv,
    stdin, and any temp files (by placeholder path) — which is exactly what
    ``--dry-run`` prints;
  * ``run()`` actually executes it: writes the temp files, invokes the CLI via
    the shared ``proc`` helper, parses stdout via ``extract_json``, and cleans
    up the temp files in a ``finally`` (mirroring the phos scripts).
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod


class BackendUnavailable(RuntimeError):
    """The backend's agent binary is not reachable — message tells the user how
    to remediate (install / start the agent). Raised by ``preflight``."""


class Backend(ABC):
    """Abstract review backend. One concrete subclass per agent CLI."""

    #: Short backend identifier, e.g. ``"codex"`` / ``"agy"``.
    name: str = ""

    #: Name of the agent binary that must be on PATH for this backend to run.
    binary: str = ""

    def preflight(self) -> None:
        """Verify the agent binary is reachable; raise :class:`BackendUnavailable`
        with an actionable message otherwise. Does NOT auto-start anything."""
        if shutil.which(self.binary) is None:
            raise BackendUnavailable(
                f"The '{self.name}' review backend requires the '{self.binary}' "
                f"CLI on your PATH, but it was not found. Install it (and start "
                f"its backend if it needs one), then re-run."
            )

    @abstractmethod
    def build_command(self, prompt: str, schema: dict) -> dict:
        """Describe — without executing — exactly what would run.

        Returns ``{"argv": [...], "stdin": <str|None>, "files": {path: contents}}``
        where ``files`` are any temp files that would be written (shown by a
        placeholder path). This is what ``--dry-run`` prints.
        """
        raise NotImplementedError

    @abstractmethod
    def run(self, prompt: str, schema: dict, *, cwd: str | None = None) -> dict:
        """Execute the backend for real and return the parsed review dict.

        Writes any temp files, invokes the CLI (in ``cwd`` if given, so the
        read-only agent can inspect the checkout's files), parses stdout via
        ``extract_json``, and removes the temp files in a ``finally``.
        """
        raise NotImplementedError
