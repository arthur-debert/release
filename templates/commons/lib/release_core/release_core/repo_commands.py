"""repo_commands — detect a repo's REAL test/build/run commands from its
manifests, component by component.

The premise (release#507): a repo is NOT one monolithic Kind with one hardcoded
command per verb. It is a *composition of components*, each with its own real
toolchain — a tauri app is a ``node`` component (frontend) AND a ``rust``
component (``src-tauri/``); any repo can also carry a ``docs`` (mkdocs)
component regardless of its primary Kind. The Kind tells you WHERE to look; the
repo's own manifest tells you WHAT the command actually is.

This module is the single source both ``release-core how-to`` (renders what each
verb resolves to) and the runnable verbs (``test-unit`` / ``test-e2e`` /
``test-all`` / ``build`` / ``run``) read, so neither can assert a command the
repo doesn't have.

The dispatch shape is deliberately ASYMMETRIC (release#507, confirmed design):

* ``test-unit`` **fans out** over every component's unit suite (node ``test:unit``
  /``test`` + ``cargo test`` at the root or under ``src-tauri/`` + ``make test``),
  cheapest-first so a fast vitest failure beats a slow rust compile. This is the
  one verb where "components" genuinely multiplies.
* ``test-e2e`` runs the node ``test:e2e`` suite — kept SEPARATE from unit because
  it is slow and needs a built binary (CI runs it as its own job). ``test-all``
  is unit then e2e.
* ``build`` / ``run`` resolve to the **single app-root command** — ``tauri build``
  (whose own ``beforeBuildCommand`` builds + embeds the frontend), the node
  ``build`` script (which chains tsc→bundler→packager), or ``cargo build``.
  The sub-components are built THROUGH that one command, never fanned out — a
  naive ``cargo build`` + ``npm build`` would double-build and fight the
  toolchain's own orchestration.
* ``docs`` (mkdocs) is an orthogonal component surfaced wherever ``mkdocs.yml``
  exists, independent of the primary Kind.

Package-manager resolution mirrors the per-Kind ``bin/`` wrappers it graduates:
the lockfile is the canonical signal (``pnpm-lock.yaml``→pnpm, ``yarn.lock``→yarn,
else npm). Everything here is filesystem-only — no network, no subprocess beyond
what the caller runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Cmd:
    """One runnable command + how to show it.

    ``argv`` is the exec form (never a shell string — no quoting hazards).
    ``cwd`` is relative to the repo root (e.g. ``src-tauri`` for the tauri rust
    suite); ``None`` means the root. ``label`` is the component name shown in the
    fan-out header (``node`` / ``rust`` / ``docs``). ``display`` is the
    human-readable one-liner ``how-to`` prints.
    """

    argv: list[str]
    display: str
    label: str = ""
    cwd: str | None = None


# --- package manager ------------------------------------------------------


def detect_pm(root: str) -> str:
    """npm / pnpm / yarn from the lockfile — the canonical signal a consumer
    commits exactly one of (mirrors every ``bin/`` wrapper's ``detect_pm``)."""
    if os.path.isfile(os.path.join(root, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.isfile(os.path.join(root, "yarn.lock")):
        return "yarn"
    return "npm"


def _scripts(root: str) -> dict[str, str]:
    """``package.json`` ``scripts`` map, or ``{}`` if absent/garbled — a missing
    or broken manifest must never crash (the lexed#144 lesson)."""
    path = os.path.join(root, "package.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _node_run(pm: str, script: str, extra: list[str] | None = None) -> Cmd:
    """Build the ``<pm> run <script>`` invocation, with the ``--`` separator npm
    needs to forward args (pnpm/yarn forward natively).

    ``extra`` carries runner flags we must inject — notably ``--run`` to take
    vitest out of its default watch mode, which would otherwise hang a
    non-interactive ``release-core test-unit`` forever (the tauri/electron
    ``check-tests`` wrappers special-cased exactly this)."""
    extra = extra or []
    # Only npm needs an explicit `--` to forward args to the underlying tool.
    # pnpm/yarn forward directly, and a literal `--` LEAKS into the tool's argv
    # (e.g. `vitest -- --run` makes `--run` a positional filter → vitest stays in
    # watch mode and hangs). Mirrors electron-app/bin/build's pm branching.
    if not extra:
        argv = [pm, "run", script]
    elif pm == "npm":
        argv = [pm, "run", script, "--", *extra]
    else:
        argv = [pm, "run", script, *extra]
    display = " ".join([pm, "run", script, *extra])
    return Cmd(argv=argv, display=display, label="node")


def _vitest_extra(body: str) -> list[str]:
    """``--run`` when a node script invokes vitest in its default (watch) form,
    so the suite runs once and exits. Empty otherwise."""
    if "vitest" in body and "vitest run" not in body and "--run" not in body:
        return ["--run"]
    return []


# --- component detectors --------------------------------------------------


def _rust_dirs(root: str) -> list[str]:
    """Directories holding a rust suite, in cheap-first fan-out order. The root
    crate first, then ``src-tauri/`` (the tauri rust facet) — both are real,
    independent ``cargo test`` targets."""
    dirs: list[str] = []
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        dirs.append(".")
    if os.path.isfile(os.path.join(root, "src-tauri", "Cargo.toml")):
        dirs.append("src-tauri")
    return dirs


def _cargo_test(cwd: str) -> Cmd:
    """``cargo test --all-features`` for one crate dir. ``--all-features``
    matches what the clippy hook lints; the exec layer upgrades to
    ``cargo nextest run`` when it is installed (the portfolio canonical runner),
    so the display stays the stable plain-cargo form."""
    where = "" if cwd == "." else f" (in {cwd})"
    return Cmd(
        argv=["cargo", "test", "--all-features"],
        display=f"cargo test --all-features{where}",
        label="rust",
        cwd=None if cwd == "." else cwd,
    )


def _make_test_target(root: str) -> bool:
    """True iff the ``Makefile`` declares a ``test:`` target (a generic
    convention, valid for any repo carrying one)."""
    path = os.path.join(root, "Makefile")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("test:") or line.startswith("test ::"):
                    return True
    except OSError:
        return False
    return False


# nvim-plugin layout markers — a `tests/` dir only means "run busted" when this
# is actually a Neovim plugin, not a generic repo that happens to have tests/.
_NVIM_LAYOUT_DIRS = ("lua", "plugin", "ftplugin", "ftdetect", "autoload", "queries")


def _is_nvim_layout(root: str) -> bool:
    return any(os.path.isdir(os.path.join(root, d)) for d in _NVIM_LAYOUT_DIRS)


def _umbrella_test(root: str) -> Cmd | None:
    """The script-runner umbrella test entry. ``None`` when nothing applies.

    For nvim plugins this mirrors ``templates/nvim-plugin/bin/check``'s real
    precedence — ``app-bin/test-all`` (the canonical fleet runner, bats-driven
    Neovim) → ``busted tests/``. The busted path is gated to an actual nvim
    layout (a ``lua``/``plugin``/… dir) so a generic ``tests/`` dir in some other
    Kind isn't misclassified as a busted suite. A ``Makefile`` ``test:`` target
    is an explicitly GENERIC fallback (not part of the nvim wrapper), last.

    (Earlier this guessed a bare ``make test`` for nvim — copied from a stale
    hint that no fleet plugin uses.)"""
    test_all = os.path.join(root, "app-bin", "test-all")
    if os.path.isfile(test_all) and os.access(test_all, os.X_OK):
        return Cmd(argv=["app-bin/test-all"], display="app-bin/test-all", label="nvim")
    if _is_nvim_layout(root) and os.path.isdir(os.path.join(root, "tests")):
        return Cmd(argv=["busted", "tests"], display="busted tests", label="nvim")
    if _make_test_target(root):
        return Cmd(argv=["make", "test"], display="make test", label="make")
    return None


def _mkdocs_config(root: str) -> str | None:
    """The mkdocs config path (root or ``docs/``), or ``None``. Mirrors
    ``docs-site/bin/check``'s discovery."""
    for rel in ("mkdocs.yml", os.path.join("docs", "mkdocs.yml")):
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return None


# --- the verb-facing resolvers --------------------------------------------


def unit_commands(root: str) -> list[Cmd]:
    """Every component's UNIT suite, cheap-first: node (fast) → rust (compiles) →
    make. Each entry is real (read from the manifest); an absent suite simply
    contributes nothing (skip-with-notice is the caller's job)."""
    cmds: list[Cmd] = []
    scripts = _scripts(root)
    if scripts:
        pm = detect_pm(root)
        # Prefer an explicit unit alias; fall back to a bare `test` (which in
        # this portfolio is unit-ish — vitest/jest, never the e2e suite).
        unit = "test:unit" if "test:unit" in scripts else ("test" if "test" in scripts else None)
        if unit:
            cmds.append(_node_run(pm, unit, _vitest_extra(scripts[unit])))
    for d in _rust_dirs(root):
        cmds.append(_cargo_test(d))
    umbrella = _umbrella_test(root)
    if umbrella:
        cmds.append(umbrella)
    return cmds


def e2e_commands(root: str) -> list[Cmd]:
    """The node ``test:e2e`` suite (build-dependent, kept out of unit). Empty
    when there is none."""
    scripts = _scripts(root)
    if "test:e2e" in scripts:
        return [_node_run(detect_pm(root), "test:e2e")]
    return []


def build_command(root: str) -> Cmd | None:
    """The SINGLE app-root build command (never a fan-out):

    * tauri (``src-tauri/Cargo.toml`` + ``package.json``) → ``<pm> tauri build``
      (npm needs ``npx --no-install`` — ``npm tauri build`` is invalid), whose
      ``beforeBuildCommand`` builds + embeds the frontend.
    * node app with a ``build`` script → ``<pm> run build`` (chains the whole
      tsc→bundle→package pipeline the consumer wired).
    * rust → ``cargo build --release``.
    * go → ``go build ./...``.
    """
    has_pkg = os.path.isfile(os.path.join(root, "package.json"))
    is_tauri = has_pkg and os.path.isfile(os.path.join(root, "src-tauri", "Cargo.toml"))
    if is_tauri:
        pm = detect_pm(root)
        if pm == "npm":
            return Cmd(
                argv=["npx", "--no-install", "tauri", "build"],
                display="npx --no-install tauri build",
                label="node",
            )
        return Cmd(argv=[pm, "tauri", "build"], display=f"{pm} tauri build", label="node")
    if has_pkg and "build" in _scripts(root):
        pm = detect_pm(root)
        return Cmd(argv=[pm, "run", "build"], display=f"{pm} run build", label="node")
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return Cmd(
            argv=["cargo", "build", "--release"],
            display="cargo build --release",
            label="rust",
        )
    if os.path.isfile(os.path.join(root, "go.mod")):
        return Cmd(argv=["go", "build", "./..."], display="go build ./...", label="go")
    return None


# Node "run" scripts in preference order — the first that exists wins.
_RUN_SCRIPTS = ("dev", "start", "watch")


def run_command(root: str) -> Cmd | None:
    """The SINGLE app-root run/dev command: a node ``dev``/``start``/``watch``
    script, or ``cargo run`` / ``go run .``. ``None`` when nothing is runnable."""
    scripts = _scripts(root)
    if scripts:
        for name in _RUN_SCRIPTS:
            if name in scripts:
                pm = detect_pm(root)
                return Cmd(argv=[pm, "run", name], display=f"{pm} run {name}", label="node")
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        return Cmd(argv=["cargo", "run", "--"], display="cargo run -- <args>", label="rust")
    if os.path.isfile(os.path.join(root, "go.mod")):
        return Cmd(argv=["go", "run", "."], display="go run . <args>", label="go")
    return None


@dataclass
class DocsCommands:
    build: Cmd
    serve: Cmd


def docs_commands(root: str) -> DocsCommands | None:
    """The orthogonal mkdocs facet, wherever ``mkdocs.yml`` lives — surfaced on
    ANY Kind, not just docs-site (release#507). ``None`` when absent."""
    cfg = _mkdocs_config(root)
    if cfg is None:
        return None
    return DocsCommands(
        build=Cmd(
            argv=["mkdocs", "build", "--strict", "--config-file", cfg],
            display=f"mkdocs build --strict --config-file {cfg}",
            label="docs",
        ),
        serve=Cmd(
            argv=["mkdocs", "serve", "--config-file", cfg],
            display=f"mkdocs serve --config-file {cfg}",
            label="docs",
        ),
    )


@dataclass
class RepoCommands:
    """Everything the verbs + how-to need for one repo, resolved once."""

    unit: list[Cmd] = field(default_factory=list)
    e2e: list[Cmd] = field(default_factory=list)
    build: Cmd | None = None
    run: Cmd | None = None
    docs: DocsCommands | None = None
    deps: str | None = None


def resolve(root: str) -> RepoCommands:
    """Resolve every component's real commands for ``root`` in one pass."""
    deps = "npm install" if os.path.isfile(os.path.join(root, "package.json")) else None
    if deps:
        pm = detect_pm(root)
        deps = f"{pm} install"
    return RepoCommands(
        unit=unit_commands(root),
        e2e=e2e_commands(root),
        build=build_command(root),
        run=run_command(root),
        docs=docs_commands(root),
        deps=deps,
    )
