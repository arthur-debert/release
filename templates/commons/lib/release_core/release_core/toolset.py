"""toolset — the pinned gate-toolset versions + the reconcile-to-pin provisioner.

This is the Python single source for the lint/format toolset the hardened gate
(``lefthook.yml``) needs. It is the wheel-carried port of the shell duo
``bin-internal/provision-gate-toolset.sh`` (the CI provisioner) +
``templates/commons/bin/gate-tool-versions.sh`` (the shell pins): both are
reached by ``release-core gate --provision`` (WS5/I, #762).

WHY EVERY tool is pinned (release#531): "one gate, run everywhere" was true for
the CONFIG (lefthook.yml) but FALSE for the tool binaries it invokes — a floating
brew/apt/npm binary could give the SAME gate a different verdict on a different
box. The fix: pin ALL of them and RECONCILE to the pin (a present-but-wrong
version is reinstalled at the pin, not silently accepted).

DUAL-SOURCE (the migration window, WS5): the pins live here AND in the shell
``gate-tool-versions.sh``; ``setup-dev-env.sh`` §0 still runs the shell
provisioning this phase (redundant + safe). A test asserts the two pin sets are
byte-identical so they cannot drift while both exist. Phase 3 (WS8) removes the
shell duo and this becomes the only source.

The pre-wheel timing worry the shell file documents dissolves here: provisioning
runs POST-pull (the wheel is already installed when ``gate --provision`` runs),
so a Python source is reachable exactly when it is needed.
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

# ── The pins — single source (mirror of gate-tool-versions.sh) ───────────────
#
# Each is overridable via the matching env var (the documented per-env knob), so
# CI / a dev can pin a different version without editing this file — exactly the
# shell ``${VAR:-default}`` contract. ``pin(name, default)`` reads the env first.
#
# The test ``test_core_toolset_pins_in_sync`` asserts these defaults equal the
# ``gate-tool-versions.sh`` literals, so the dual source cannot diverge.

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
    else the default literal. Mirrors the shell ``${NAME:-default}``."""
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
        raise ProvisionError(f"npm install -g {' '.join(pkgs)} failed")


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
        raise ProvisionError(f"pip install {' '.join(specs)} failed")


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
    dest = bin_dir or os.path.join(os.path.expanduser("~"), ".local", "bin")
    if not _have("curl") or not _have("bash"):
        msg = "curl/bash not found — cannot download actionlint"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    os.makedirs(dest, exist_ok=True)
    _log(f"download actionlint {actionlint_version()} -> {dest}")
    url = "https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash"
    # curl | bash -s -- <version> <dir> — the official pinned installer.
    script = proc.run(["curl", "-sSfL", url], check=False)
    if script.returncode != 0:
        if best_effort:
            _log("WARNING: actionlint downloader fetch failed")
            return
        raise ProvisionError("actionlint downloader fetch failed")
    res = proc.run(
        ["bash", "-s", "--", actionlint_version(), dest],
        input=script.stdout,
        check=False,
        capture_output=not best_effort,
    )
    if res.returncode != 0 and not best_effort:
        raise ProvisionError(f"actionlint install to {dest} failed")


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
    dest = (
        bin_dir
        or os.environ.get("YQ_INSTALL_DIR")
        or os.path.join(os.path.expanduser("~"), ".local", "bin")
    )
    yq_os, arch = _resolve_os_arch()
    if yq_os is None or arch is None:
        msg = f"unsupported OS/arch ({platform.system()}/{platform.machine()}) for yq"
        if best_effort:
            _log(f"WARNING: {msg}")
            return
        raise ProvisionError(msg)
    os.makedirs(dest, exist_ok=True)
    url = f"https://github.com/mikefarah/yq/releases/download/v{yq_version()}/yq_{yq_os}_{arch}"
    _log(f"download yq {yq_version()} -> {dest}")
    fd, tmp = tempfile.mkstemp(prefix="yq.", dir=dest)
    os.close(fd)
    ok = False
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — fixed https GH-release URL
        if os.path.getsize(tmp) > 0:
            os.chmod(tmp, 0o755)
            os.replace(tmp, os.path.join(dest, "yq"))
            ok = True
    except OSError:
        ok = False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
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
    steps = (provision_npm, provision_pip)
    for step in steps:
        step(best_effort=best_effort)
    provision_actionlint(best_effort=best_effort, bin_dir=bin_dir)
    provision_yq(best_effort=best_effort, bin_dir=bin_dir)
    _log("done.")
    return 0
