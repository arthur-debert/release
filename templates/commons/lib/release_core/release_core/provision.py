"""provision — the per-session dev-environment provisioning, dissolved into init.

WS5/E (#762): the provisioning the old ``setup-dev-env.sh`` did (gate-toolset
arming, git-hook wiring, dep caches, NSS cert import, submodule init, the per-repo
post-setup hook) moved INTO ``release-core init`` so the wheel carries it — one
definition, pulled. ``init`` calls :func:`run` after it installs the managed
tree; ``arm-gate``/CI reaches the same toolset arming through ``release-core gate
--provision``.

WS8 (#765): this is now the SOLE provisioner — the shell ``setup-dev-env.sh`` was
removed once the fleet converged onto the pull model (SessionStart now calls
``install-release-core`` directly → ``release-core init`` → here). Every step is
still IDEMPOTENT (safe to re-run every session) and best-effort.

ORDER (load-bearing): the TOOLSET is armed FIRST — the gate (and the hook we wire
next) needs it, and arming after would be circular. Then submodule content (BOTH
local + cloud — a fresh clone needs it for the gate/tests), then the git-hook
wiring. The ``--cloud``-only heavier cloud-snapshot steps follow: the tag fetch,
dep caches, the NSS cert import. The per-repo ``app-bin/post-setup-hook.sh`` runs
LAST (the consumer extension point), matching the old ``setup-dev-env.sh`` §4.

LOAD-BEARING vs OPTIONAL (the WS6 distinction applies here too): toolset arming +
hook wiring are load-bearing for the gate but stay BEST-EFFORT inside init —
init must never break the boot, and the next session self-heals. The cloud steps
(caches, cert import) are genuinely optional and degrade gracefully. Nothing here
raises; every step warns + continues.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from . import toolset


def _warn(msg: str) -> None:
    print(f"release-core init (provision): {msg}", file=sys.stderr)


def _have(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


def _run(cmd: list[str], *, cwd: str, **kw) -> int:
    """Run ``cmd`` best-effort, returning the exit code (or 1 on a spawn error).
    stdout/stderr inherit so install output stays visible (the shell's stance:
    never swallow the diagnostic)."""
    try:
        return subprocess.run(cmd, cwd=cwd, check=False, **kw).returncode  # noqa: S603
    except OSError as exc:
        _warn(f"could not run {' '.join(cmd)}: {exc}")
        return 1


# ── §0 — arm the gate toolset (FIRST; the gate + hook need it) ────────────────


def arm_toolset(repo_root: str) -> None:
    """Reconcile the gate toolset to its pins (BEST-EFFORT — see module docstring).

    Delegates to :func:`toolset.provision` so the SAME code arms the toolset here
    (init / SessionStart) and in CI (``arm-gate`` → ``gate --provision``). A
    transient failure WARNS (best_effort=True) rather than aborting the boot; the
    redundant shell §0 / the next session reconciles what slipped."""
    try:
        toolset.provision(best_effort=True)
    except Exception as exc:  # provisioner is best-effort here — never break init
        _warn(f"gate-toolset arming hit an error (continuing): {exc}")


# ── §0.0 — git submodule content (BOTH local + cloud) ────────────────────────


def init_submodules(repo_root: str) -> None:
    """``git submodule update --init --recursive`` when the repo has submodules.

    A FRESH clone has uninitialised submodules regardless of where it lands (the
    live-fire round clones consumers fresh; lex-fmt/lex carries a ``comms/``
    submodule the gate + tests need — release#706/#728). Guarded on
    ``.gitmodules`` so it's a no-op otherwise; idempotent (cheap when in sync)."""
    if not os.path.isfile(os.path.join(repo_root, ".gitmodules")):
        return
    if not _have("git"):
        return
    rc = _run(
        ["git", "submodule", "update", "--init", "--recursive", "--quiet"],
        cwd=repo_root,
    )
    if rc != 0:
        _warn(
            "git submodule update --init failed — submodule content may be missing "
            "(gate/tests that need it will fail)"
        )


# ── §0.2 — pre-commit hook wiring (BOTH local + cloud) ───────────────────────


def wire_hook(repo_root: str) -> None:
    """Wire ``.git/hooks/pre-commit`` for THIS repo (per-clone state; every fresh
    clone starts without it). Idempotent.

    Mirrors setup-dev-env.sh §0.2's branch logic:
      * a migrated consumer (``.release/lefthook.yml`` present, or no tracked root
        ``lefthook.yml``) → ``release-core gate --install-hook`` (the binary hook);
      * a root-``lefthook.yml`` repo (release-self / not-yet-migrated) →
        ``lefthook install``;
      * a hand-rolled ``app-bin/pre-commit`` → symlink it in.

    Runs AFTER the managed-tree install (init has already written ``.release/``),
    so the binary hook has a gate to run (release#567)."""
    has_release_cfg = os.path.isfile(os.path.join(repo_root, ".release", "lefthook.yml"))
    has_root_cfg = os.path.isfile(os.path.join(repo_root, "lefthook.yml"))

    # Migrated-consumer path: the gate lives only in .release/ — wire the binary
    # hook via the gate verb itself (it points lefthook at .release/lefthook.yml
    # and unsets any stale core.hooksPath). Also the no-config arm (release#567):
    # wire it anyway when there's no root config, so commits hit the fail-loud
    # unbuilt-config error rather than running ungated with no hook.
    if _have("release-core") and (has_release_cfg or not has_root_cfg):
        rc = _run(["release-core", "gate", "--install-hook"], cwd=repo_root)
        if rc != 0:
            _warn("release-core gate --install-hook failed — pre-commit hook NOT wired")
        return

    # Root-lefthook.yml branch — release's own repo (hand-authored root gate).
    lefthook = _resolve_lefthook(repo_root)
    if has_root_cfg and lefthook:
        rc = _run([lefthook, "install"], cwd=repo_root)
        if rc != 0:
            _warn("lefthook install failed — pre-commit hook NOT wired")
        return

    # Hand-rolled app-bin/pre-commit (zed-lex / tree-sitter-lex pattern).
    app_hook = os.path.join(repo_root, "app-bin", "pre-commit")
    if os.access(app_hook, os.X_OK):
        _symlink_app_hook(repo_root, app_hook)


def _resolve_lefthook(repo_root: str) -> str | None:
    """The lefthook to wire with: a node consumer's ``node_modules/.bin/lefthook``
    (installed via a ``prepare`` script) first — ``command -v`` misses it — else a
    PATH lefthook. Mirrors setup-dev-env.sh §0.2."""
    nm = os.path.join(repo_root, "node_modules", ".bin", "lefthook")
    if os.access(nm, os.X_OK):
        return nm
    from shutil import which

    return which("lefthook")


def _symlink_app_hook(repo_root: str, app_hook: str) -> None:
    """Symlink a hand-rolled ``app-bin/pre-commit`` into the repo's hooks dir,
    resolving the dir via git plumbing (correct under a worktree, where ``.git``
    is a file). Best-effort with visible diagnostics."""
    try:
        hooks_dir = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not hooks_dir:
            hooks_dir = subprocess.run(
                ["git", "rev-parse", "--git-path", "hooks"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
    except OSError as exc:
        _warn(f"could not resolve hooks dir: {exc} — pre-commit hook NOT wired")
        return
    if not hooks_dir:
        return
    hooks_dir = hooks_dir if os.path.isabs(hooks_dir) else os.path.join(repo_root, hooks_dir)
    try:
        os.makedirs(hooks_dir, exist_ok=True)
    except OSError as exc:
        _warn(f'failed to mkdir -p "{hooks_dir}" — pre-commit hook NOT wired: {exc}')
        return
    dest = os.path.join(hooks_dir, "pre-commit")
    try:
        if os.path.islink(dest) or os.path.exists(dest):
            os.remove(dest)
        os.symlink(app_hook, dest)
    except OSError as exc:
        _warn(f"failed to symlink app-bin/pre-commit — pre-commit hook NOT wired: {exc}")


# ── Cloud-only steps (gated by --cloud) ──────────────────────────────────────


def fetch_tags(repo_root: str) -> None:
    """Restore release tags on a shallow cloud clone (one round-trip). §1."""
    if not _have("git"):
        return
    _run(["git", "fetch", "--tags", "--quiet", "origin"], cwd=repo_root)


def dep_caches(repo_root: str) -> None:
    """Warm the project dep cache for the detected stack — §2 of the old
    setup-dev-env.sh.

    Per-stack, idempotent (cheap when warm). Best-effort: a registry hiccup must
    not abort. Covers the shell's stack detection (Rust / Go / Node / Ruby /
    Python). WS8 (#765) folded in the Python venv + ~/.local/bin symlink pass the
    shell §2 owned (:func:`_python_deps`), so a cloud session's venv-installed CLIs
    are reachable on the agent's bare PATH with the shell gone."""
    j = os.path.join
    if os.path.isfile(j(repo_root, "Cargo.toml")) and _have("cargo"):
        _run(["cargo", "fetch", "--locked", "--quiet"], cwd=repo_root)
    if os.path.isfile(j(repo_root, "go.mod")) and _have("go"):
        _run(["go", "mod", "download"], cwd=repo_root)
    if os.path.isfile(j(repo_root, "package.json")):
        _node_deps(repo_root)
    if os.path.isfile(j(repo_root, "Gemfile")) and _have("bundle"):
        _run(["bundle", "install", "--quiet"], cwd=repo_root)
    _python_deps(repo_root)


def _node_deps(repo_root: str) -> None:
    j = os.path.join
    if os.path.isfile(j(repo_root, "package-lock.json")) and _have("npm"):
        if _run(["npm", "ci"], cwd=repo_root) != 0:
            _run(["npm", "install"], cwd=repo_root)
    elif os.path.isfile(j(repo_root, "yarn.lock")) and _have("yarn"):
        if _run(["yarn", "install", "--frozen-lockfile"], cwd=repo_root) != 0:
            _run(["yarn", "install"], cwd=repo_root)
    elif os.path.isfile(j(repo_root, "pnpm-lock.yaml")) and _have("pnpm"):
        if _run(["pnpm", "install", "--frozen-lockfile"], cwd=repo_root) != 0:
            _run(["pnpm", "install"], cwd=repo_root)
    elif _have("npm") and (
        _run(["npm", "install", "--no-audit", "--no-fund", "--no-package-lock"], cwd=repo_root) != 0
    ):
        # No committed lockfile (tree-sitter-lex gitignores it): install dev-only
        # tooling without generating a lockfile. Fall back to a plain no-lock install.
        _run(["npm", "install", "--no-package-lock"], cwd=repo_root)


def _python_deps(repo_root: str) -> None:
    """Create/refresh a project ``.venv`` and install deps, then symlink the
    venv's console-scripts onto ~/.local/bin — the WS8 port of setup-dev-env.sh §2's
    Python block.

    Runs unconditionally (pip install is idempotent + sub-second when warm), gated
    on a conventional manifest + python3. The ~/.local/bin symlink pass is what
    makes a venv-installed CLI (mkdocs etc.) resolvable on the cloud agent's bare
    PATH — the Bash tool's non-interactive shells don't see ${repo}/.venv/bin, so
    without the symlinks a test that shells out to a venv CLI gets
    FileNotFoundError. Best-effort throughout (a failure warns, never aborts)."""
    j = os.path.join
    has_manifest = any(
        os.path.isfile(j(repo_root, m)) for m in ("pyproject.toml", "requirements.txt", "setup.py")
    )
    if not has_manifest or not _have("python3"):
        return
    venv = j(repo_root, ".venv")
    venv_pip = j(venv, "bin", "pip")
    if not os.access(venv_pip, os.X_OK) and (
        _run(["python3", "-m", "venv", ".venv"], cwd=repo_root) != 0
    ):
        _warn("python3 -m venv .venv failed — pip installs will be skipped")
    if not os.access(venv_pip, os.X_OK):
        return
    _run([venv_pip, "install", "--upgrade", "pip", "--quiet"], cwd=repo_root)
    if os.path.isfile(j(repo_root, "pyproject.toml")):
        if _run([venv_pip, "install", "-e", ".[dev]", "--quiet"], cwd=repo_root) != 0:
            _warn("editable install (.[dev]) failed — tests may not run (see pip output)")
    elif os.path.isfile(j(repo_root, "requirements.txt")):
        if _run([venv_pip, "install", "-r", "requirements.txt", "--quiet"], cwd=repo_root) != 0:
            _warn("requirements install failed — tests may not run")
    elif os.path.isfile(j(repo_root, "setup.py")) and (
        _run([venv_pip, "install", "-e", ".", "--quiet"], cwd=repo_root) != 0
    ):
        _warn("editable install failed — tests may not run")
    _symlink_venv_scripts(repo_root)


def _symlink_venv_scripts(repo_root: str) -> None:
    """Symlink every executable in ``.venv/bin`` (except the python/pip/activate
    family) into ~/.local/bin so the cloud agent's bare PATH finds venv CLIs.
    Idempotent (``ln -sf`` overwrites a stale symlink). Best-effort."""
    venv_bin = os.path.join(repo_root, ".venv", "bin")
    if not os.path.isdir(venv_bin):
        return
    local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    try:
        os.makedirs(local_bin, exist_ok=True)
    except OSError as exc:
        _warn(f"could not create {local_bin}: {exc}")
        return
    skip_prefixes = ("python", "pip", "activate", "easy_install", "wheel")
    for name in os.listdir(venv_bin):
        src = os.path.join(venv_bin, name)
        if not (os.path.isfile(src) and os.access(src, os.X_OK)):
            continue
        if any(name == p or name.startswith(p) for p in skip_prefixes):
            continue
        dest = os.path.join(local_bin, name)
        try:
            if os.path.islink(dest):
                # Only refresh OUR OWN symlink — one that already resolves into this
                # repo's .venv/bin. A symlink pointing ANYWHERE else is user-managed
                # (or another project's venv); leave it and skip, never clobber it.
                if os.path.dirname(os.path.realpath(dest)) == os.path.realpath(venv_bin):
                    os.remove(dest)  # idempotent refresh of our stale/current link
                else:
                    _warn(
                        f"{dest} is a symlink outside our .venv/bin; leaving it, not linking {name}"
                    )
                    continue
            elif os.path.exists(dest):
                # A real (non-symlink) file is a user/agent-installed binary or a
                # pinned gate-toolset executable sharing a name — NEVER clobber it.
                _warn(f"{dest} is a real file (not our symlink); leaving it, not linking {name}")
                continue
            os.symlink(src, dest)
        except OSError:
            continue  # best-effort: one permission hiccup must not abort the rest


def unprovisionable_stacks(repo_root: str) -> list[str]:
    """Stacks the repo NEEDS (by manifest) but whose toolchain is ABSENT here, so
    :func:`dep_caches` can't install them and a gate can't run faithfully against
    the repo's own project checks. Empty == fully provisionable in this env.

    A caller (e.g. ``release-core admin repos verify``) uses this to SKIP-with-
    reason a repo it cannot provision, instead of running the gate on a dep-less
    tree and blaming the consumer for the environment's missing toolchain."""
    j = os.path.join
    missing: list[str] = []
    if os.path.isfile(j(repo_root, "Cargo.toml")) and not _have("cargo"):
        missing.append("cargo")
    if os.path.isfile(j(repo_root, "go.mod")) and not _have("go"):
        missing.append("go")
    if os.path.isfile(j(repo_root, "Gemfile")) and not _have("bundle"):
        missing.append("bundle")
    if os.path.isfile(j(repo_root, "package.json")):
        # Mirror _node_deps EXACTLY (same lockfile→tool precedence) so the probe
        # never disagrees with what dep_caches will actually run: a package-lock
        # repo needs npm specifically (pnpm is NOT a substitute), and the
        # no-lockfile fallback also uses npm.
        if os.path.isfile(j(repo_root, "package-lock.json")):
            if not _have("npm"):
                missing.append("npm")
        elif os.path.isfile(j(repo_root, "yarn.lock")):
            if not _have("yarn"):
                missing.append("yarn")
        elif os.path.isfile(j(repo_root, "pnpm-lock.yaml")):
            if not _have("pnpm"):
                missing.append("pnpm")
        elif not _have("npm"):
            missing.append("npm")
    return missing


def import_nss_cert(repo_root: str) -> None:
    """Import the cloud sandbox-egress CA into Chromium's NSS DB — the WS8 (#765)
    Python port of setup-dev-env.sh §2.5.

    Cloud sessions route HTTPS through an "Anthropic sandbox-egress…CA" proxy that
    re-signs every leaf cert. Chromium on Linux reads its own NSS DB at
    ~/.pki/nssdb, NOT the OpenSSL bundle — without the CA imported there, every
    HTTPS resource an Electron/Playwright test loads is rejected with
    ERR_CERT_AUTHORITY_INVALID.

    GENUINELY OPTIONAL (degrades gracefully): a non-Linux host, a missing
    certutil/openssl, or no matching cert is a clean no-op. Idempotent —
    ``certutil -L -n <nick>`` short-circuits a present cert. Two cert layouts are
    probed: (A) the CA concatenated into /etc/ssl/certs/ca-certificates.crt, and
    (B) standalone /etc/ssl/certs/swp-ca-*.pem files (2026-05+)."""
    import platform
    import re

    if platform.system() != "Linux" or not _have("certutil") or not _have("openssl"):
        return

    tmp = tempfile.mkdtemp(prefix="ca-import.")
    try:
        pems: list[str] = []
        # Layout A: split the system bundle into per-cert PEMs IF it carries an
        # Anthropic CA (cheap gate avoids the split on a non-cloud box).
        bundle = "/etc/ssl/certs/ca-certificates.crt"
        if os.path.isfile(bundle):
            try:
                with open(bundle, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                text = ""
            if "Anthropic" in text:
                blocks = re.findall(
                    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                    text,
                    re.DOTALL,
                )
                for i, block in enumerate(blocks):
                    p = os.path.join(tmp, f"bundle_{i}.pem")
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(block + "\n")
                    pems.append(p)
        # Layout B: standalone swp-ca-*.pem files.
        ssl_dir = "/etc/ssl/certs"
        if os.path.isdir(ssl_dir):
            for name in os.listdir(ssl_dir):
                if name.startswith("swp-ca-") and name.endswith(".pem"):
                    pems.append(os.path.join(ssl_dir, name))
        if not pems:
            return

        nssdb = os.path.join(os.path.expanduser("~"), ".pki", "nssdb")
        os.makedirs(nssdb, exist_ok=True)
        if not os.path.isfile(os.path.join(nssdb, "cert9.db")):
            _run(["certutil", "-d", f"sql:{nssdb}", "-N", "--empty-password"], cwd=repo_root)

        for pem in pems:
            try:
                subj = subprocess.run(
                    ["openssl", "x509", "-in", pem, "-noout", "-subject"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
            except OSError:
                continue
            if "Anthropic" not in subj or "sandbox-egress" not in subj:
                continue
            m = re.search(r"CN *= *([^,/\n]+)", subj)
            if not m:
                continue
            nick = m.group(1).strip()
            # Idempotent: skip the import when the nick is already present.
            present = subprocess.run(
                ["certutil", "-d", f"sql:{nssdb}", "-L", "-n", nick],
                capture_output=True,
                text=True,
                check=False,
            )
            if present.returncode == 0:
                continue
            _run(
                ["certutil", "-d", f"sql:{nssdb}", "-A", "-t", "C,,", "-n", nick, "-i", pem],
                cwd=repo_root,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── §4 — the per-repo post-setup hook (LAST) ─────────────────────────────────


def post_setup_hook(repo_root: str) -> None:
    """Run ``app-bin/post-setup-hook.sh`` if present — the consumer extension
    point (Xvfb daemon, pinned-binary fetch, extra rustup targets). LAST, matching
    setup-dev-env.sh §4."""
    hook = os.path.join(repo_root, "app-bin", "post-setup-hook.sh")
    if not os.path.isfile(hook):
        return
    if not os.access(hook, os.X_OK):
        _warn(f"{hook} exists but is not executable; skipping")
        return
    _run([hook], cwd=repo_root)


# ── The orchestrator ─────────────────────────────────────────────────────────


def _safe(step, repo_root: str) -> None:
    """Run one provisioning step, swallowing ANY exception (warn + continue).

    The individual steps are already best-effort internally, but ``run()`` wraps
    each call here too — defense in depth — so the "never raises" contract holds
    regardless of a step's internals or a FUTURE edit. init must not break the
    boot over a provisioning hiccup."""
    try:
        step(repo_root)
    except Exception as exc:  # noqa: BLE001 — deliberate best-effort boundary
        _warn(f"provision step {step.__name__} failed (continuing): {exc}")


def run(repo_root: str, *, cloud: bool = False) -> None:
    """Provision the dev env for ``repo_root`` — the init-side entry point.

    Order (load-bearing): toolset FIRST (the gate/hook need it), then submodule
    content, then the git-hook wiring; the ``--cloud`` steps (tag fetch, dep
    caches, cert import) only when ``cloud=True``; the per-repo post-setup hook
    LAST. Every step is best-effort + idempotent (WS8: this is the SOLE
    provisioner — see module docstring). Never raises — each step is wrapped in
    :func:`_safe`, so init never breaks the boot over a provisioning hiccup."""
    _safe(arm_toolset, repo_root)
    _safe(init_submodules, repo_root)
    _safe(wire_hook, repo_root)
    if cloud:
        _safe(fetch_tags, repo_root)
        _safe(dep_caches, repo_root)
        _safe(import_nss_cert, repo_root)
    _safe(post_setup_hook, repo_root)
