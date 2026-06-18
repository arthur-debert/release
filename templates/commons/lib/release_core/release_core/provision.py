"""provision — the per-session dev-environment provisioning, dissolved into init.

WS5/E (#762): the provisioning ``setup-dev-env.sh`` runs (gate-toolset arming,
git-hook wiring, dep caches, NSS cert import, submodule init, the per-repo
post-setup hook) moves INTO ``release-core init`` so the wheel carries it — one
definition, pulled. ``init`` calls :func:`run` after it installs the managed
tree; ``arm-gate``/CI reaches the same toolset arming through ``release-core gate
--provision``.

ADDITIVE through the migration window (the #1 safety rule): this phase
``setup-dev-env.sh`` ALSO still runs these steps (redundant + SAFE — there is
never a window where neither provisions). So every step here is IDEMPOTENT and
will not fight the shell: it reconciles to a pin / installs-if-missing / wires a
hook, never mutating a tree the shell already converged. Removal of the shell is
WS8 (Phase 3).

ORDER (load-bearing): the TOOLSET is armed FIRST — the gate (and the hook we wire
next) needs it, and arming after would be circular. Then submodule content (BOTH
local + cloud — a fresh clone needs it for the gate/tests), then the git-hook
wiring. The ``--cloud``-only heavier cloud-snapshot steps follow: the tag fetch,
dep caches, the NSS cert import. The per-repo ``app-bin/post-setup-hook.sh`` runs
LAST (the consumer extension point), matching ``setup-dev-env.sh`` §4.

LOAD-BEARING vs OPTIONAL (the WS6 distinction applies here too): toolset arming +
hook wiring are load-bearing for the gate but stay BEST-EFFORT inside init —
init must never break the boot, and the redundant shell run / next session
self-heals. The cloud steps (caches, cert import) are genuinely optional and
degrade gracefully. Nothing here raises; every step warns + continues.
"""

from __future__ import annotations

import os
import subprocess
import sys

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
    """Warm the project dep cache for the detected stack — §2 of setup-dev-env.sh.

    Per-stack, idempotent (cheap when warm). Best-effort: a registry hiccup must
    not abort. Mirrors the shell's stack detection (Rust / Go / Node / Ruby /
    Python). The Python venv + ~/.local/bin symlink pass is intentionally NOT
    re-implemented here — it is cloud-snapshot machinery the shell still owns this
    phase, and replicating the venv-bin symlink loop would risk fighting it. Phase
    3 (WS8) consolidates the remainder."""
    j = os.path.join
    if os.path.isfile(j(repo_root, "Cargo.toml")) and _have("cargo"):
        _run(["cargo", "fetch", "--locked", "--quiet"], cwd=repo_root)
    if os.path.isfile(j(repo_root, "go.mod")) and _have("go"):
        _run(["go", "mod", "download"], cwd=repo_root)
    if os.path.isfile(j(repo_root, "package.json")):
        _node_deps(repo_root)
    if os.path.isfile(j(repo_root, "Gemfile")) and _have("bundle"):
        _run(["bundle", "install", "--quiet"], cwd=repo_root)


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
        # Which node tool THIS repo needs, by its committed lockfile (mirrors
        # _node_deps); no lockfile → any npm/pnpm can install dev tooling.
        if os.path.isfile(j(repo_root, "pnpm-lock.yaml")):
            if not _have("pnpm"):
                missing.append("pnpm")
        elif os.path.isfile(j(repo_root, "yarn.lock")):
            if not _have("yarn"):
                missing.append("yarn")
        elif not (_have("npm") or _have("pnpm")):
            missing.append("npm")
    return missing


def import_nss_cert(repo_root: str) -> None:
    """Import the cloud sandbox-egress CA into Chromium's NSS DB — §2.5.

    GENUINELY OPTIONAL (degrades gracefully): only Electron/Playwright HTTPS in a
    cloud session needs it. A non-Linux host, a missing certutil/openssl, or no
    matching cert is a clean no-op. Idempotent — ``certutil -L -n <nick>``
    short-circuits a present cert. Delegates to the still-shipped shell §2.5 logic
    by NOT re-implementing the cert-splitting awk loop in Python: this phase the
    shell setup-dev-env.sh §2.5 still runs (additive), so init only needs to not
    REGRESS it. Kept as a documented no-op here to mark the seam for WS8, where
    the shell goes away and this gains the full import.
    """
    # Optional + still shell-provided this phase — intentional no-op (see docstring).
    return


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
    LAST. Every step is best-effort + idempotent (the shell runs them too this
    phase — see module docstring). Never raises — each step is wrapped in
    :func:`_safe`, so init never breaks the boot over a provisioning hiccup."""
    _safe(arm_toolset, repo_root)
    _safe(init_submodules, repo_root)
    _safe(wire_hook, repo_root)
    if cloud:
        _safe(fetch_tags, repo_root)
        _safe(dep_caches, repo_root)
        _safe(import_nss_cert, repo_root)
    _safe(post_setup_hook, repo_root)
