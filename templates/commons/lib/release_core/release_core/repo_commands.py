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
the lockfile is the one signal (``pnpm-lock.yaml``→pnpm, ``yarn.lock``→yarn,
else npm). Detection is network-free; the one subprocess is the CI
``check-command`` fallback (:func:`_ci_check_command`), which reads the
consumer's workflow YAML through ``release_core.yamlio`` (a ``yq`` shell-out)
only when the manifest probes find no unit suite — never on the common path.
"""

from __future__ import annotations

import glob
import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field

from . import yamlio


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
    """npm / pnpm / yarn from the lockfile — the one signal a consumer
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
    ``cargo nextest run`` when it is installed (the portfolio's shared runner),
    so the display stays the stable plain-cargo form."""
    where = "" if cwd == "." else f" (in {cwd})"
    return Cmd(
        argv=["cargo", "test", "--all-features"],
        display=f"cargo test --all-features{where}",
        label="rust",
        cwd=None if cwd == "." else cwd,
    )


def _cargo_llvm_cov(cwd: str) -> Cmd:
    """``cargo llvm-cov --all-features`` for one crate dir — its default text
    report ends with the per-file coverage table (the "least-covered modules"
    view release#568 wants). ``--all-features`` matches :func:`_cargo_test`.
    The exec layer (tasks.coverage) hard-errors with an install hint when
    cargo-llvm-cov is missing — never a silent skip."""
    where = "" if cwd == "." else f" (in {cwd})"
    return Cmd(
        argv=["cargo", "llvm-cov", "--all-features"],
        display=f"cargo llvm-cov --all-features{where}",
        label="rust",
        cwd=None if cwd == "." else cwd,
    )


def _go_cover_cmds() -> list[Cmd]:
    """The go coverage pair: ``go test -coverprofile`` then ``go tool cover
    -func`` over the profile (the per-function summary). The profile lands in
    the system tmpdir, never the working tree; the random suffix keeps the
    path unpredictable (no symlink pre-creation) and concurrent runs from
    clobbering each other — both legs share the one path. (Not ``mkstemp``:
    detection must stay side-effect-free — ``how-to`` renders these commands
    without running them.)"""
    token = secrets.token_hex(8)
    profile = os.path.join(tempfile.gettempdir(), f"release-core-coverage-{token}.out")
    return [
        Cmd(
            argv=["go", "test", f"-coverprofile={profile}", "./..."],
            display=f"go test -coverprofile={profile} ./...",
            label="go",
        ),
        Cmd(
            argv=["go", "tool", "cover", f"-func={profile}"],
            display=f"go tool cover -func={profile}",
            label="go",
        ),
    ]


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
    precedence — ``app-bin/test-all`` (the shared fleet runner, bats-driven
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


# --- CI caller fallback ---------------------------------------------------

# The reusable-workflow path prefix a consumer's CI caller `uses:` to invoke
# one of release's shared category workflows (e.g.
# `arthur-debert/release/.github/workflows/nvim-plugin.yml@v2`). When a consumer
# wires its real suite through that caller's `check-command:` input (rather than
# a manifest script), the manifest probes find nothing — this fallback recovers
# the command from the workflow so how-to/test-unit don't report "(none wired)".
_RELEASE_WF_PREFIX = "arthur-debert/release/.github/workflows/"


def _ci_check_command(root: str) -> str | None:
    """The first non-empty ``with.check-command`` from a release reusable-workflow
    caller in ``.github/workflows/*.yml``, or ``None``.

    A pure FALLBACK for repos that wire their suite through the shared caller's
    ``check-command:`` input instead of a manifest script. Robust to a missing
    dir / non-dict workflow / garbled YAML / no matching job — never raises."""
    wf_dir = os.path.join(root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return None
    paths = sorted(glob.glob(os.path.join(wf_dir, "*.yml")))
    paths += sorted(glob.glob(os.path.join(wf_dir, "*.yaml")))
    for path in paths:
        try:
            doc = yamlio.load(path)
        except (yamlio.YamlError, ValueError, OSError):
            # YamlError: yq missing/parse-failed. ValueError: yq emitted
            # non-JSON stdout (json.loads). OSError: the file vanished. In every
            # case skip this workflow — the fallback must never raise.
            continue
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses")
            if not isinstance(uses, str) or _RELEASE_WF_PREFIX not in uses:
                continue
            with_block = job.get("with")
            if not isinstance(with_block, dict):
                continue
            cc = with_block.get("check-command")
            if isinstance(cc, str) and cc.strip():
                return cc
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
    # Fallback ONLY when no manifest/script probe found a suite: a consumer may
    # wire its real, CI-green suite through the shared reusable-workflow
    # caller's `check-command:` input (e.g. lex-fmt/nvim's test/run_tests.sh).
    # Never added alongside a manifest command — that would double-run.
    if not cmds:
        cc = _ci_check_command(root)
        if cc:
            cmds.append(Cmd(argv=["bash", "-c", cc], display=cc, label="ci"))
    return cmds


def e2e_commands(root: str) -> list[Cmd]:
    """The node ``test:e2e`` suite (build-dependent, kept out of unit). Empty
    when there is none."""
    scripts = _scripts(root)
    if "test:e2e" in scripts:
        return [_node_run(detect_pm(root), "test:e2e")]
    return []


def coverage_commands(root: str) -> list[Cmd]:
    """Every component's COVERAGE command, cheap-first (mirrors the
    ``unit_commands`` fan-out): node (the test runner with ``--coverage``) →
    rust (``cargo llvm-cov``, root + ``src-tauri/``) → go (``go test
    -coverprofile`` + ``go tool cover -func``).

    Node prefers a dedicated ``coverage``/``test:coverage`` script when the repo
    wired one; otherwise it reuses the unit script and forwards ``--coverage``
    (vitest and jest both take it; the vitest ``--run`` injection applies so the
    suite exits instead of watching). Empty means NO component has a
    coverage-capable toolchain — the verb errors loudly on that (coverage was
    asked for; there is nothing to "skip", per the hard-gate philosophy)."""
    cmds: list[Cmd] = []
    scripts = _scripts(root)
    if scripts:
        pm = detect_pm(root)
        named = next((s for s in ("coverage", "test:coverage") if s in scripts), None)
        if named:
            cmds.append(_node_run(pm, named, _vitest_extra(scripts[named])))
        else:
            unit = "test:unit" if "test:unit" in scripts else "test" if "test" in scripts else None
            if unit:
                extra = [*_vitest_extra(scripts[unit]), "--coverage"]
                cmds.append(_node_run(pm, unit, extra))
    for d in _rust_dirs(root):
        cmds.append(_cargo_llvm_cov(d))
    if os.path.isfile(os.path.join(root, "go.mod")):
        cmds.extend(_go_cover_cmds())
    return cmds


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


# --- preflight: friendly-fail a runnable verb on a bare checkout ----------
#
# On a FRESH clone (no `pnpm install`, no wasm build, no `tree-sitter generate`)
# the detected command is correct but its PREREQUISITES are absent, so exec'ing
# it produces a raw, undiscoverable error: `vitest: command not found` (deps),
# a vitest module-load failure (`wasm/…/pkg/` missing), or `parser.c not found`
# (grammar not generated). These are not the consumer's gate failing — they are
# "you ran the suite on a bare tree". Detect each from the filesystem (never run
# anything) and return ONE actionable remediation line so the verb can fail
# FRIENDLY with a clean exit, mirroring the `cargo-llvm-cov is not installed`
# install-hint UX in tasks.coverage. Detection + message only — we never
# auto-install deps or auto-build (the "load-bearing gotcha": a mechanical tool
# must not pretend to be the consumer's CI).


def _is_node_run(cmd: Cmd, root: str) -> bool:
    """True iff this command runs through the node package manager — its head is
    the detected pm (``pnpm``/``yarn``/``npm``, covering ``<pm> run …`` AND direct
    invocations like ``pnpm tauri build``) or ``npx``. Any of these needs
    ``node_modules`` installed."""
    if not cmd.argv:
        return False
    head = cmd.argv[0]
    return head == detect_pm(root) or head in ("npm", "pnpm", "yarn", "npx")


def _wasm_crate_dirs_missing_pkg(root: str) -> list[str]:
    """Rust→wasm crate dirs under ``wasm/`` (a ``Cargo.toml`` present) whose
    built ``pkg/`` output is absent — the wasm-pack build hasn't run. Empty when
    there is no ``wasm/`` tree or every crate is already built."""
    wasm_root = os.path.join(root, "wasm")
    if not os.path.isdir(wasm_root):
        return []
    missing: list[str] = []
    try:
        names = sorted(os.listdir(wasm_root))
    except OSError:
        return []
    for name in names:
        crate = os.path.join(wasm_root, name)
        if not os.path.isdir(crate) or not os.path.isfile(os.path.join(crate, "Cargo.toml")):
            continue
        if not os.path.isdir(os.path.join(crate, "pkg")):
            missing.append(os.path.join("wasm", name))
    return missing


def _tree_sitter_parser_missing(root: str) -> bool:
    """True iff this is a tree-sitter grammar (``grammar.js`` at the root) whose
    generated ``src/parser.c`` is absent — ``tree-sitter generate`` hasn't run."""
    return os.path.isfile(os.path.join(root, "grammar.js")) and not os.path.isfile(
        os.path.join(root, "src", "parser.c")
    )


def preflight(cmd: Cmd, root: str) -> str | None:
    """The single remediation line for a missing prerequisite of ``cmd`` on a
    bare checkout, or ``None`` when the tree is ready to run.

    Order is most-actionable-first: an uninstalled toolchain (deps) shadows the
    build-artifact checks, because installing deps is the precondition for the
    build anyway. The build-artifact checks (wasm / tree-sitter) are
    message-only: they point at the build, they never run it."""
    # 1. Toolchain: a node command with no node_modules → the inner tool
    #    (vitest / svelte-check / …) resolves to "command not found".
    if _is_node_run(cmd, root) and not os.path.isdir(os.path.join(root, "node_modules")):
        pm = detect_pm(root)
        return f"dependencies not installed — run `{pm} install` first"
    # 2. Build artifact: wasm crates not built (vitest load-failures from
    #    `wasm/*/pkg/` missing). Only a node-driven suite imports the built
    #    `pkg/`, so gate this on the command — a `cargo test`/`cargo build` in a
    #    repo that merely *has* a `wasm/` dir doesn't depend on its `pkg/`.
    if _is_node_run(cmd, root):
        missing = _wasm_crate_dirs_missing_pkg(root)
        if missing:
            return (
                "wasm packages not built — run the wasm build first "
                f"(missing pkg/ in: {', '.join(missing)})"
            )
    # 3. Build artifact: tree-sitter parser not generated (`parser.c not found`).
    if _tree_sitter_parser_missing(root):
        return "tree-sitter parser not generated — run `tree-sitter generate` first"
    return None


@dataclass
class RepoCommands:
    """Everything the verbs + how-to need for one repo, resolved once."""

    unit: list[Cmd] = field(default_factory=list)
    e2e: list[Cmd] = field(default_factory=list)
    coverage: list[Cmd] = field(default_factory=list)
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
        coverage=coverage_commands(root),
        build=build_command(root),
        run=run_command(root),
        docs=docs_commands(root),
        deps=deps,
    )
