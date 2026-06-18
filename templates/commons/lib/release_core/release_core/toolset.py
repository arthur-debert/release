"""toolset — the pinned gate-toolset versions + the reconcile-to-pin provisioner.

This is THE single source for the lint/format toolset the hardened gate
(``lefthook.yml``) needs — the pins AND the reconcile-to-pin provisioner. It is
reached by ``release-core gate --provision`` (WS5/I, #762), called identically by
CI (``arm-gate``) and the local boot (``release-core init`` → provisioning).

WHY EVERY tool is pinned (release#531): "one gate, run everywhere" was true for
the CONFIG (lefthook.yml) but FALSE for the tool binaries it invokes — a floating
brew/apt/npm binary could give the SAME gate a different verdict on a different
box. The fix: pin ALL of them and RECONCILE to the pin (a present-but-wrong
version is reinstalled at the pin, not silently accepted).

WS8 (#765): this is now the ONLY source. The shell duo it ported —
``bin-internal/provision-gate-toolset.sh`` (the CI provisioner) +
``templates/commons/bin/gate-tool-versions.sh`` (the shell pins) — was removed
once the fleet converged, so there is no dual-source to keep in sync.

Provisioning runs POST-pull (the wheel is already installed when
``gate --provision`` runs), so this Python source is reachable exactly when it is
needed — the pre-wheel timing worry the old shell file documented is moot.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request

from . import proc

# ── The pins — THE single source (WS8 #765) ──────────────────────────────────
#
# Each is overridable via the matching env var (the documented per-env knob), so
# CI / a dev can pin a different version without editing this file — exactly the
# old shell ``${VAR:-default}`` contract. ``pin(name)`` reads the env first.
# (The shell ``gate-tool-versions.sh`` this mirrored was removed once the fleet
# converged, so these literals are now the only pins — no dual-source to keep in
# sync.)

_PINS: dict[str, str] = {
    "RUFF_VERSION": "0.15.12",
    "ACTIONLINT_VERSION": "1.7.7",
    "YAMLLINT_VERSION": "1.38.0",
    "LEFTHOOK_VERSION": "2.1.9",
    "PRETTIER_VERSION": "3.8.4",
    "MARKDOWNLINT_CLI_VERSION": "0.48.0",
    # shellcheck rides the pinned-pip path via the shellcheck-py wheel (bundles
    # the real binary): SHELLCHECK_VERSION is what the BINARY reports;
    # SHELLCHECK_PY_VERSION is the pip package that delivers it.
    "SHELLCHECK_VERSION": "0.11.0",
    "SHELLCHECK_PY_VERSION": "0.11.0.1",
    # yq (mikefarah/Go) — release_core.yamlio's reader + the init lefthook merge.
    "YQ_VERSION": "4.44.3",
}


def pin(name: str) -> str:
    """The pinned version for ``name`` — the env override (the per-env knob),
    else the default literal. Close to the shell ``${NAME:-default}`` but STRICTER:
    the value is ``.strip()``-ed, so a whitespace-only override counts as unset
    (shell ``:-`` falls back only for unset/empty, treating whitespace as set)."""
    val = os.environ.get(name, "").strip()
    return val or _PINS[name]


# Convenience accessors (read at call time so an env override is honored).
def ruff_version() -> str:
    return pin("RUFF_VERSION")


def actionlint_version() -> str:
    return pin("ACTIONLINT_VERSION")


def yamllint_version() -> str:
    return pin("YAMLLINT_VERSION")


def lefthook_version() -> str:
    return pin("LEFTHOOK_VERSION")


def prettier_version() -> str:
    return pin("PRETTIER_VERSION")


def markdownlint_cli_version() -> str:
    return pin("MARKDOWNLINT_CLI_VERSION")


def shellcheck_version() -> str:
    return pin("SHELLCHECK_VERSION")


def shellcheck_py_version() -> str:
    return pin("SHELLCHECK_PY_VERSION")


def yq_version() -> str:
    return pin("YQ_VERSION")


# ── The reconcile helper — the gate_version_matches port ─────────────────────

_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")


def _reported_version(binary: str) -> str | None:
    """The version a tool reports — the FIRST dotted token of ``--version``,
    mirroring ``gate_version_matches`` in gate-tool-versions.sh. None if the tool
    is absent or prints nothing matchable."""
    exe = shutil.which(binary)
    if exe is None:
        return None
    try:
        res = proc.run([exe, "--version"], check=False)
    except OSError:
        return None
    m = _VERSION_RE.search(res.stdout or "")
    return m.group(0) if m else None


def version_matches(binary: str, wanted: str) -> bool:
    """True iff ``binary`` is on PATH AND its reported version equals ``wanted``.
    The reconcile predicate: a floating/absent binary reports a miss and gets
    (re)installed at the pin — the install-if-missing hole closer (release#531)."""
    return _reported_version(binary) == wanted


# ── The provisioner — the port of provision-gate-toolset.sh ──────────────────


class ProvisionError(RuntimeError):
    """A gate-toolset provisioning step failed irrecoverably (a required tool
    could not be reconciled to its pin). The gate is HARD — this is the
    Python equivalent of the shell ``exit 1`` paths."""


def _log(msg: str) -> None:
    print(f"gate --provision: {msg}", file=sys.stderr)


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _with_output(msg: str, res) -> str:
    """Append a failed command's captured stdout/stderr to ``msg``.

    HARD mode (``best_effort=False``, which CI/arm-gate uses) runs with
    ``capture_output=True``, so the underlying tool's output is in ``res`` —
    surfacing it makes a CI provisioning failure diagnosable (network, PEP 668,
    404, resolver, …) instead of a bare ``… failed``."""
    parts = [msg]
    for label in ("stdout", "stderr"):
        text = (getattr(res, label, None) or "").strip()
        if text:
            parts.append(f"{label}:\n{text}")
    return "\n".join(parts)


def _prepend_path(d: str) -> None:
    """Ensure ``d`` is FIRST on this process's PATH so a freshly-installed pinned
    binary in ``d`` shadows any earlier floating copy — for both the post-install
    ``version_matches()`` re-check AND the gate run in this process. Without it an
    earlier-PATH unpinned tool (e.g. a kislyuk python-yq squatting /usr/bin/yq —
    release#755) keeps winning ``shutil.which``: a FALSE hard-mode failure even
    though the pinned binary was installed, plus the gate running the wrong tool."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p != d]
    os.environ["PATH"] = os.pathsep.join([d, *parts])


def _default_bin_dir() -> str:
    """Where a pinned binary is installed by default — MUST match the dir the
    boot/CI prepend to PATH: ``${XDG_BIN_HOME:-$HOME/.local/bin}`` (see
    install-release-core + arm-gate). Honoring ``XDG_BIN_HOME`` keeps the install
    target and the PATH dir in sync, so the pinned binary is discoverable by
    later steps (lefthook etc.) even when XDG_BIN_HOME is customized."""
    return os.environ.get("XDG_BIN_HOME") or os.path.join(os.path.expanduser("~"), ".local", "bin")


def provision_npm(*, best_effort: bool) -> None:
    """Reconcile the npm globals (lefthook + prettier + markdownlint) to their
    pins. ``markdownlint-cli`` is the package; ``markdownlint`` the binary."""
    pkgs: list[str] = []
    if not version_matches("lefthook", lefthook_version()):
        pkgs.append(f"lefthook@{lefthook_version()}")
    if not version_matches("prettier", prettier_version()):
        pkgs.append(f"prettier@{prettier_version()}")
    if not version_matches("markdownlint", markdownlint_cli_version()):
        pkgs.append(f"markdownlint-cli@{markdownlint_cli_version()}")
    if not pkgs:
        return
    if not _have("npm"):
        msg = f"npm not found — cannot install {' '.join(pkgs)}"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    _log(f"npm install -g {' '.join(pkgs)}")
    res = proc.run(["npm", "install", "-g", *pkgs], check=False, capture_output=not best_effort)
    if res.returncode != 0 and not best_effort:
        raise ProvisionError(_with_output(f"npm install -g {' '.join(pkgs)} failed", res))


def provision_pip(*, best_effort: bool) -> None:
    """Reconcile the pip tools (ruff + yamllint + shellcheck via shellcheck-py)
    to their pins. One pinned install reconciles all three whenever any drifts.

    Modern Debian/Ubuntu (PEP 668) reject a global pip install without
    ``--break-system-packages``; try that first, fall back to a plain install
    (older distros / venvs reject the flag) — mirrors setup-dev-env.sh §0."""
    need = (
        not version_matches("ruff", ruff_version())
        or not version_matches("yamllint", yamllint_version())
        or not version_matches("shellcheck", shellcheck_version())
    )
    if not need:
        return
    pip = _pip_cmd()
    if pip is None:
        msg = "pip not found — cannot install ruff/yamllint/shellcheck"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    specs = [
        f"ruff=={ruff_version()}",
        f"yamllint=={yamllint_version()}",
        f"shellcheck-py=={shellcheck_py_version()}",
    ]
    _log(f"pip install {' '.join(specs)}")
    # --break-system-packages first (PEP 668 distros), then plain.
    res = proc.run(
        [*pip, "install", "--break-system-packages", *specs],
        check=False,
        capture_output=not best_effort,
    )
    if res.returncode != 0:
        res = proc.run([*pip, "install", *specs], check=False, capture_output=not best_effort)
    if res.returncode != 0 and not best_effort:
        raise ProvisionError(_with_output(f"pip install {' '.join(specs)} failed", res))


def _pip_cmd() -> list[str] | None:
    """Resolve a pip invocation: prefer ``pip``/``pip3`` on PATH, else
    ``<this-python> -m pip``. Returns None only when no pip is reachable."""
    for name in ("pip", "pip3"):
        if _have(name):
            return [name]
    # Fall back to the running interpreter's pip (the tool venv always has one).
    try:
        proc.run([sys.executable, "-m", "pip", "--version"], check=True)
        return [sys.executable, "-m", "pip"]
    except (OSError, proc.ProcError):
        return None


def _resolve_os_arch() -> tuple[str | None, str | None]:
    """Map this host to the (os, arch) tokens mikefarah yq + actionlint use."""
    system = platform.system()
    yq_os = {"Linux": "linux", "Darwin": "darwin"}.get(system)
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = None
    return yq_os, arch


def provision_actionlint(*, best_effort: bool, bin_dir: str | None = None) -> None:
    """Reconcile actionlint to its pin via rhysd's official downloader.

    No standard pip/apt package (apt has none; brew floats), so the downloader is
    the one cross-OS pinned source. Installs into ``bin_dir`` (default
    ~/.local/bin, first on PATH + no sudo) so the pinned binary shadows any
    floating brew/apt actionlint."""
    if version_matches("actionlint", actionlint_version()):
        return
    dest = bin_dir or _default_bin_dir()
    if not _have("curl") or not _have("bash"):
        msg = "curl/bash not found — cannot download actionlint"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    os.makedirs(dest, exist_ok=True)
    _prepend_path(dest)  # the pinned binary must shadow any earlier-PATH actionlint
    _log(f"download actionlint {actionlint_version()} -> {dest}")
    url = "https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash"
    # curl | bash -s -- <version> <dir> — the official pinned installer.
    script = proc.run(["curl", "-sSfL", url], check=False, capture_output=True)
    if script.returncode != 0:
        if best_effort:
            _log(_with_output("WARNING: actionlint downloader fetch failed", script))
            return
        raise ProvisionError(_with_output("actionlint downloader fetch failed", script))
    res = proc.run(
        ["bash", "-s", "--", actionlint_version(), dest],
        input=script.stdout,
        check=False,
        capture_output=not best_effort,
    )
    if res.returncode != 0 and not best_effort:
        raise ProvisionError(_with_output(f"actionlint install to {dest} failed", res))


def provision_yq(*, best_effort: bool, bin_dir: str | None = None) -> None:
    """Reconcile mikefarah yq to its pin: download the raw GH-release binary.

    release_core.yamlio shells out to mikefarah's ``yq -o=json`` / ``yq
    eval-all``; a kislyuk python-yq (jq wrapper) squatting /usr/bin/yq has
    neither and hard-fails the gate (release#755). Download to a temp file and
    install only if NON-EMPTY, so a failed/short download never leaves a
    truncated yq on PATH. ``YQ_INSTALL_DIR`` (default ~/.local/bin) is the test
    seam — tests point it at a sandbox so the real PATH is never touched."""
    if version_matches("yq", yq_version()):
        return
    dest = bin_dir or os.environ.get("YQ_INSTALL_DIR") or _default_bin_dir()
    yq_os, arch = _resolve_os_arch()
    if yq_os is None or arch is None:
        msg = f"unsupported OS/arch ({platform.system()}/{platform.machine()}) for yq"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    os.makedirs(dest, exist_ok=True)
    _prepend_path(dest)  # the pinned yq must shadow an earlier-PATH yq (release#755)
    url = f"https://github.com/mikefarah/yq/releases/download/v{yq_version()}/yq_{yq_os}_{arch}"
    _log(f"download yq {yq_version()} -> {dest}")
    fd, tmp = tempfile.mkstemp(prefix="yq.", dir=dest)
    os.close(fd)
    ok = False
    err: str | None = None
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — fixed https GH-release URL
        if os.path.getsize(tmp) > 0:
            os.chmod(tmp, 0o755)
            os.replace(tmp, os.path.join(dest, "yq"))
            ok = True
        else:
            err = "downloaded file was empty"
    except OSError as exc:
        err = str(exc)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not ok and best_effort:
        # Best-effort must WARN (not silently swallow) — matches the rest of the
        # module so a still-missing yq is diagnosable from the boot log.
        reason = err or "unusable binary"
        _log(f"WARNING: yq {yq_version()} download/install failed ({reason}): {url}")
    # Hard re-check: the download is best-effort, so confirm the outcome.
    if not version_matches("yq", yq_version()) and not best_effort:
        raise ProvisionError(
            f"yq is still not at {yq_version()} after the install attempt "
            f"(download failed or empty: {url})"
        )
    if ok:
        _log(f"installed yq {yq_version()}")


def provision(*, best_effort: bool = False, bin_dir: str | None = None) -> int:
    """Reconcile the WHOLE gate toolset to its pins — the port of
    ``provision-gate-toolset.sh``. Returns 0 on success.

    ``best_effort=True`` (the SessionStart / init path) reconciles what it can and
    WARNS on a failure rather than aborting — a transient registry hiccup must not
    block the session. ``best_effort=False`` (the CI / arm-gate path) is the HARD
    gate: a tool that can't be reconciled raises :class:`ProvisionError`, surfaced
    by the caller as a non-zero exit.

    golangci-lint is intentionally NOT provisioned here — it is Go-repo-only and
    setup-dev-env.sh still installs it conditionally (it is not part of the
    common UNION the shell provisioner reconciles)."""
    _run_step(provision_npm, best_effort=best_effort)
    _run_step(provision_pip, best_effort=best_effort)
    _run_step(provision_actionlint, best_effort=best_effort, bin_dir=bin_dir)
    _run_step(provision_yq, best_effort=best_effort, bin_dir=bin_dir)
    _log("done.")
    return 0


def _run_step(step, *, best_effort: bool, **kw) -> None:
    """Run one ``provision_*`` step with a consistent error boundary.

    A :class:`ProvisionError` is the step's own DELIBERATE hard-fail — it
    propagates so the caller (``gate --provision``) turns it into a clean exit +
    message. Any OTHER exception (an unexpected ``OSError`` from a
    ``makedirs``/``mkstemp``/spawn, a permission/FS hiccup, …) is converted HERE so
    it can never escape as a raw traceback: HARD mode → ``ProvisionError`` (clean);
    best-effort → warn + continue. One boundary covers every step, current and
    future."""
    try:
        step(best_effort=best_effort, **kw)
    except ProvisionError:
        raise
    except Exception as exc:  # noqa: BLE001 — the FS/spawn error boundary
        msg = f"{step.__name__} failed unexpectedly: {exc!r}"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg) from exc
