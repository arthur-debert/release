"""Boot a consumer clone the way a real session would — fail-loud (release#578).

`orc probe` evaluates the orientation a REAL session gets, and a real session
starts with the consumer's SessionStart boot chain (`bin/setup-dev-env.sh` →
`install-release-core` → `release-core init`). SDK-launched sessions never
fire that hook, so probes were evaluating stale, unbooted trees (epic #583,
root cause C: an instrument must verify its own preconditions).

The fix is an EXPLICIT boot, not SDK hook plumbing: `boot_clone()` runs the
clone's own boot script (same chain, same order as a real session), then
boot-ASSERTS that the managed sync actually applied, and returns a
:class:`BootReport` recording what was booted. Deterministic and fail-loud —
any boot failure invalidates the probe by design.

The asserts check INSTALLED STATE, never commits: an already-converged
clone legitimately commits nothing, so "no sync commit appeared" is not a
failure (the commit subject is recorded informationally only).

Pure stdlib — no Claude Agent SDK import — so it unit-tests under the same
SDK-less pytest job as the rest of the workspace.
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BOOT_SCRIPT = "bin/setup-dev-env.sh"

# Provenance marker written by `release-core init` into the `.release/` temp dir
# (ADR-0002; see release_core/sync.py SOURCE_MARKER). Last non-comment
# line is the source sha/ref the managed files were installed from.
SOURCE_MARKER = ".release/.release-sync-source"

# The WS7 managed-mirrors block sentinel that init rewrites into the clone's
# git info/exclude (see _EXCLUDE_BEGIN in release_core/verbs/init.py).
EXCLUDE_SENTINEL = "# >>> release-core managed mirrors (rewritten by every init) >>>"

# Subject prefix of the auto-commit `release-core init` creates when the
# managed sync changes tracked files (the full subject appends the source
# label — see _commit_subject in release_core/verbs/init.py).
SYNC_COMMIT_SUBJECT = "chore(release): sync managed tree"


class BootError(RuntimeError):
    """A fatal boot condition — the probe must die loudly, not run unbooted."""


def _read_text(path: Path, what: str) -> str:
    """Read a boot artifact, mapping read failures to the BootError contract."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise BootError(f"could not read {what} at {path}: {e}") from e


@dataclass(frozen=True)
class BootReport:
    """What a probe boot actually booted (recorded, then asserted)."""

    repo_path: str
    source_ref: str  # provenance sha/ref parsed from .release/.release-sync-source
    core_version: str  # `release-core --version` (self-reports 0.0.1 — #580, recorded anyway)
    sync_commit: str | None  # subject of the sync commit created by THIS boot, if any


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        # Keep the failure mode deterministic: cmd_probe catches BootError only.
        raise BootError(
            f"git {' '.join(args)} failed in {repo} (exit {e.returncode}): "
            f"{(e.stderr or e.stdout or '').strip()}"
        ) from e


def _run_boot_script(repo: Path) -> None:
    """Run the clone's own boot script, streaming its output to our stderr."""
    proc = subprocess.Popen(
        ["bash", BOOT_SCRIPT],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # Boot output is arbitrary tool noise — undecodable bytes must not
        # escape as UnicodeDecodeError past the BootError-only contract.
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout is None:  # can't happen with stdout=PIPE; explicit for -O runs
        raise BootError(f"{BOOT_SCRIPT}: could not capture boot output stream.")
    for line in proc.stdout:
        print(f"[boot] {line}", end="", file=sys.stderr)
    rc = proc.wait()
    if rc != 0:
        raise BootError(f"{BOOT_SCRIPT} exited {rc} — probe boot failed; probe invalidated.")


def _parse_source_ref(repo: Path) -> str:
    """Boot-assert (a): the provenance marker exists and names a source ref."""
    marker = repo / SOURCE_MARKER
    if not marker.is_file():
        raise BootError(
            f"boot-assert (a) failed: {SOURCE_MARKER} missing after boot — "
            "release-core init did not install the `.release/` temp dir."
        )
    lines = [
        ln.strip()
        for ln in _read_text(marker, "provenance marker").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        raise BootError(f"boot-assert (a) failed: {SOURCE_MARKER} has no source sha/ref line.")
    return lines[-1]


def _assert_exclude_sentinel(repo: Path) -> None:
    """Boot-assert (b): init rewrote the managed-mirrors block into info/exclude."""
    exclude_rel = _git(repo, "rev-parse", "--git-path", "info/exclude")
    exclude = (repo / exclude_rel).resolve()
    if (
        not exclude.is_file()
        or EXCLUDE_SENTINEL not in _read_text(exclude, "git info/exclude").splitlines()
    ):
        raise BootError(
            f"boot-assert (b) failed: managed-mirrors sentinel missing from {exclude} — "
            "release-core init did not rewrite the WS7 exclude block."
        )


def _core_version() -> str:
    """Informational: what `release-core --version` self-reports (0.0.1 today — #580)."""
    try:
        out = subprocess.run(
            ["release-core", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or out.stderr.strip() or f"(exit {out.returncode})"
    except FileNotFoundError:
        return "(release-core not on PATH)"


def boot_clone(repo_path: str) -> BootReport:
    """Boot `repo_path` via its own `bin/setup-dev-env.sh`, assert, report.

    Raises :class:`BootError` (fail-loud, no fallback) when the boot script is
    missing, exits non-zero, or either boot-assert fails. On success prints a
    delimited boot report to stderr and returns it.
    """
    repo = Path(repo_path).expanduser().resolve()
    script = repo / BOOT_SCRIPT
    if not script.is_file():
        raise BootError(
            f"{BOOT_SCRIPT} not found in {repo} — probe targets consumer clones, "
            "which carry their own boot script. No fallback boot path; probe invalidated."
        )

    head_before = _git(repo, "rev-parse", "HEAD")
    _run_boot_script(repo)

    # Boot-asserts: installed state only — never commits (a converged
    # clone commits nothing, and that is success).
    source_ref = _parse_source_ref(repo)
    _assert_exclude_sentinel(repo)

    # Informational only: did THIS boot create a sync commit? Scan the whole
    # range this boot created (a post-setup hook may commit after init, so the
    # sync commit need not be HEAD), constrained to init's own commit subject
    # so an unrelated commit can't masquerade as one.
    boot_subjects = _git(repo, "log", "--format=%s", f"{head_before}..HEAD").splitlines()
    sync_commit = next((s for s in boot_subjects if s.startswith(SYNC_COMMIT_SUBJECT)), None)

    report = BootReport(
        repo_path=str(repo),
        source_ref=source_ref,
        core_version=_core_version(),
        sync_commit=sync_commit,
    )
    print(
        "\n".join(
            [
                "===== orc probe boot report =====",
                f"clone:          {report.repo_path}",
                f"provenance ref: {report.source_ref}",
                f"release-core:   {report.core_version}  "
                "(wheel self-reports 0.0.1 — release#580; recorded as-is)",
                "sync commit:    "
                + (report.sync_commit or "(none — clone already converged; not a failure)"),
                "===== end boot report =====",
            ]
        ),
        file=sys.stderr,
    )
    return report
