"""gate — run THE pre-commit quality gate over this repo, once.

``release-core gate`` is the single, Kind-agnostic entry point for "run the
checks" that an agent or human reaches for every loop. It runs the SAME
``lefthook run pre-commit`` gate CI runs — the unified-gate principle
(release#348, CLAUDE.md "The gate is ONE definition, run everywhere").

SCOPE — the gate is the fast lint/format/static-analysis set (fmt, clippy,
eslint, markdownlint, …). It deliberately does NOT run the test suite: tests are
a separate, slower CI check, not a pre-commit hook. So a green gate is NECESSARY
but not SUFFICIENT for "CI will be green" — run the Kind's test command
(``cargo test`` / ``npm test`` / ``pytest``; see ``release-core how-to``)
separately before pushing. (Conflating the two is the "gate over-claims its
coverage" trap — same class as a check that silently no-ops; caught in the #501
lex-fmt dogfood run.)

Why ``--all-files`` and not the staged set: lefthook's default ``pre-commit``
run inspects only STAGED files, so a bare run over an unstaged edit reports a
false green (everything ``(skip) no files for inspection``). ``release-core
gate`` always runs the whole tree so "gate green" is never a lie about an
unstaged change. (caught in the #501 footprint validation run.)

A missing lefthook is a HARD gate failure — the gate never skips. Re-run the
bootstrap (``install-release-core`` / ``release-core init``) to provision it.

One definition AND one runner (release#567): the gate runs the TOOLSET-provisioned
lefthook — the binary ``release-core gate --provision`` (the wheel-carried
``release_core.toolset``, the single source post-WS8) pins — never whatever a
consumer's ``node_modules/.bin`` or a floating PATH install happens to carry. The
verb reads the ``LEFTHOOK_VERSION`` pin (env override → ``release_core.toolset``)
and resolves the FIRST PATH lefthook that reports that
version, skipping ``node_modules`` entries (the consumer-dep skew source: the #525
probe saw ``release-core gate`` and the git hook run DIFFERENT lefthook versions in
one session). No pinned binary on PATH is a HARD failure, not a fall-back-to-
whatever — version skew between gate runs is exactly the silent divergence the
one-gate principle forbids.

Config resolution (#501 / WS3 release#524): the gate definition lives ONLY in the
ephemeral ``.release/`` build dir now — the root ``lefthook.yml`` discovery symlink
is gone — so this verb points lefthook at ``.release/lefthook.yml`` explicitly via
``LEFTHOOK_CONFIG`` when present, falling back to lefthook's default discovery for a
release-self / not-yet-migrated repo that still keeps a root config. An
UNBUILT gate (no ``.release/lefthook.yml`` AND no root config) is a HARD
failure naming ``release-core init`` as the remedy — never lefthook's
warn-and-pass (release#567: the first commit of a session fired the hook before
init had composed ``.release/``, printed a "no config files" WARNING and passed,
i.e. the earliest commit of a session was silently ungated).

Git-hook integration (WS3): ``release-core gate --install-hook`` writes
``.git/hooks/pre-commit`` to ``exec release-core gate --hook`` — so the committed
gate runs through the binary, with no tracked root ``lefthook.yml`` for lefthook to
discover. ``--hook`` runs lefthook over the STAGED set (not ``--all-files``), so
``stage_fixed`` auto-fix+restage stays correct at commit time; the bare,
agent-facing ``release-core gate`` keeps ``--all-files`` (no false green on an
unstaged edit).

``--install-hook`` ALSO writes an untracked, hook-less root ``lefthook.yml`` stub
(git-excluded; release#714) in a WS3 consumer (one with no real root config). A
stock npm/pnpm ``prepare: lefthook install`` step otherwise runs ``lefthook
install`` with no config present, and lefthook then CREATES an empty starter
``lefthook.yml`` at the root — an all-commented file that would silently
warn-and-pass an ungated commit if the gate ever discovered it (re-opening the
release#567 boot hole) and a tracked-able root config the WS3 model forbids. The
stub pre-empts that: ``lefthook install`` finds it (no starter created) and, since
it declares no hooks, leaves our binary-driven pre-commit hook untouched. The gate
verb always prefers ``.release/lefthook.yml`` over it, and the unbuilt-gate
fail-loud guard ignores it (a stub is not a real config), so it acts only as the
install-time redirect it is. release-self / not-yet-migrated repos keep their
hand-authored root gate and never get a stub.

Every gate run prints a single final line — ``GATE: OK (N checks)`` or
``GATE: FAILED (name, ...)`` — derived from lefthook's exit code (release#628).
It survives a ``release-core gate | tail`` view, where the pipe would otherwise
mask the exit status and lefthook's last lines don't say pass/fail. ``--quiet``
prints only that verdict line on success (full output on failure), so there's no
incentive to pipe through tail at all.

Toolset provisioning (WS5/I, #762): ``release-core gate --provision`` reconciles
the gate toolset (lefthook, prettier, markdownlint, ruff, yamllint, shellcheck,
actionlint, yq) to the pins in ``release_core.toolset`` — the Python port of
``bin-internal/provision-gate-toolset.sh``. It is the HARD provisioner (a tool it
cannot reconcile is a non-zero exit); ``--provision --best-effort`` warns instead
of failing (the SessionStart / init path, where a transient registry hiccup must
not abort the session). ``arm-gate`` calls ``--provision`` in CI; ``release-core
init`` calls the same code (best-effort) so one definition arms the toolset
everywhere.

Usage:
  release-core gate [extra lefthook args...]   # agent/human: full --all-files gate
  release-core gate --quiet [lefthook args...] # only the verdict line on success
  release-core gate --hook [lefthook args...]  # git pre-commit: staged-set gate
  release-core gate --install-hook             # wire .git/hooks/pre-commit
  release-core gate --provision [--best-effort]# reconcile the gate toolset to pins

On a fresh node-consumer checkout (a root ``package.json`` but no installed
``node_modules/``) the gate's eslint / svelte-check hooks would otherwise surface
a raw ``svelte-check: command not found`` / ``npx canceled due to missing
packages`` (release#718). A node-deps preflight detects that from the filesystem
BEFORE invoking lefthook and fails FRIENDLY with a one-line ``run `<pm> install`
first`` remediation — mirroring the runnable-verb preflight UX (release#735). It
fires ONLY on ``package.json`` present + ``node_modules`` absent (so release-self
and any non-node repo are unaffected) and never auto-installs.

Exit codes:
  0  — the gate passed (or the hook was installed)
  1  — the gate failed, the pinned lefthook is not on PATH, the gate config is
       not built, or a node consumer's dependencies are not installed (all HARD
       failures — the gate never skips)
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator

USAGE = __doc__ or ""

# lefthook's own config-discovery basenames — the release-self / not-yet-migrated
# root-config case. If NONE of these exists and .release/lefthook.yml is absent,
# the gate is not built and must fail loud (release#567), because lefthook
# itself treats "no config found" as a WARNING and exits 0.
_DISCOVERY_NAMES = (
    "lefthook.yml",
    ".lefthook.yml",
    "lefthook.yaml",
    ".lefthook.yaml",
    "lefthook.toml",
    ".lefthook.toml",
    "lefthook.json",
    ".lefthook.json",
)

_HOOK_BODY = """\
#!/bin/sh
# Managed by release — do not edit. Regenerate via release-core gate --install-hook.
# THE pre-commit gate, run through the binary (release#524): the gate definition
# lives in the ephemeral .release/ build dir, so there is no tracked root
# lefthook.yml for a stock git hook to discover — release-core gate points lefthook
# at .release/lefthook.yml itself.
exec release-core gate --hook "$@"
"""

# The marker line every release-written root lefthook stub carries. It identifies
# the file as OUR untracked redirect (not a real gate config) so the unbuilt-gate
# fail-loud guard (release#567) ignores it — see _real_root_config_present.
_STUB_MARKER = "# release-managed lefthook stub"

# An untracked root lefthook stub (release#714). WS3 removed the TRACKED root
# lefthook.yml, but a stock npm/pnpm `prepare: lefthook install` step still runs
# `lefthook install`, and with NO config present lefthook CREATES an empty starter
# `lefthook.yml` at the repo root (empirically confirmed on 2.1.9: "Config not
# found, creating..."). That stray starter (a) is a tracked-able root config the
# WS3 model forbids, and (b) is entirely commented out, so if the gate ever fell
# back to it (when .release/ isn't built yet) lefthook would warn-and-pass an
# UNGATED commit — exactly the boot hole release#567 closed.
#
# This stub pre-empts that: present, `lefthook install` finds a config and does
# NOT create a starter. It declares NO hooks, so `lefthook install`'s hook sync is
# a no-op and never clobbers our binary-driven .git/hooks/pre-commit (a stub WITH
# a pre-commit key would make lefthook install its OWN dispatcher hook, bypassing
# `release-core gate`'s pinned-runner + fail-loud resolution — confirmed; so the
# stub stays hook-less). It is git-excluded (never tracked), and the gate verb
# always prefers .release/lefthook.yml over it, so it only ever acts as the
# install-time redirect it is.
_STUB_BODY = f"""\
{_STUB_MARKER} — do not edit, do not commit (regenerate: release-core gate --install-hook).
# WS3 (release#524): the real gate lives in the ephemeral .release/lefthook.yml;
# `release-core gate` points lefthook there explicitly. This hook-less stub exists
# ONLY so a stray `lefthook install` (e.g. an npm `prepare` script) finds a config
# and does not create an empty starter lefthook.yml that would silently ungate
# commits (release#714). It declares no hooks, so it never shadows the
# binary-driven pre-commit hook.
colors: false
"""


def _node_deps_preflight(root: str) -> int:
    """Friendly-fail BEFORE lefthook when a node consumer's deps are missing.

    On a FRESH checkout of a node consumer (a ``package.json`` at the root but no
    installed ``node_modules/``), the gate's eslint / svelte-check hooks resolve
    through ``node_modules/.bin`` and surface a raw, undiscoverable
    ``svelte-check: command not found`` / ``npx canceled due to missing
    packages`` (release#718). That is not the gate failing — it is "you ran the
    gate on a bare tree". Detect it from the filesystem (never run anything) and
    return ONE actionable remediation line, mirroring the runnable-verb preflight
    UX (release#735, ``repo_commands.preflight``). Detection + message only — we
    never auto-install deps (the load-bearing gotcha: a mechanical tool must not
    pretend to be the consumer's CI).

    Returns 1 (with the remediation printed to stderr) when deps are missing, else
    0. Fires ONLY on ``package.json`` present + ``node_modules/`` absent, so a
    non-node repo (release's own — no ``package.json``) is unaffected."""
    if not os.path.isfile(os.path.join(root, "package.json")):
        return 0
    if os.path.isdir(os.path.join(root, "node_modules")):
        return 0
    from .. import repo_commands

    pm = repo_commands.detect_pm(root)
    print(
        f"release-core gate: dependencies not installed — run `{pm} install` first",
        file=sys.stderr,
    )
    return 1


def _repo_root() -> str:
    """The git work-tree root, so the gate binds to this repo from any cwd."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.getcwd()


def _pinned_lefthook_version(root: str) -> str | None:
    """The toolset's LEFTHOOK_VERSION pin — the single source (release#499/#531).

    WS8 (#765): the pin lives in ``release_core.toolset`` (the wheel-carried
    Python single source — the shell ``gate-tool-versions.sh`` was removed), which
    honors the LEFTHOOK_VERSION env override exactly as the old shell ``${VAR:-}``
    did. Always knowable from the installed wheel — never None when running from a
    wheel; ``root`` is unused now (kept for signature stability)."""
    from .. import toolset

    return toolset.lefthook_version()


def _lefthook_version(binary: str) -> str | None:
    """The version a lefthook binary reports — first dotted token of --version,
    mirroring release_core.toolset.version_matches."""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True)
    except OSError:
        return None
    m = re.search(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", out.stdout or "")
    return m.group(0) if m else None


def _iter_path_lefthooks() -> Iterator[str]:
    """Every distinct lefthook on PATH, in PATH order, SKIPPING node_modules
    entries: a consumer's package.json-vendored lefthook is a floating consumer
    dep, never the toolset's — running it is exactly the version-skew source the
    one-runner principle forbids (release#567).

    ``shutil.which`` per PATH entry (not ``os.access``) so Windows PATHEXT
    resolution finds a ``lefthook.cmd`` wrapper too. De-duped by realpath so a
    symlink chain doesn't probe the same binary twice."""
    from shutil import which

    seen: set[str] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or "node_modules" in entry.split(os.sep):
            continue
        cand = which("lefthook", path=entry)
        if not cand:
            continue
        real = os.path.realpath(cand)
        if real in seen:
            continue
        seen.add(real)
        yield cand


def _resolve_lefthook(pin: str | None) -> str | None:
    """The ONE gate runner: the first PATH lefthook reporting the toolset pin.

    With a known pin, a candidate at any OTHER version is rejected — the
    provisioner (release-core gate --provision) reconciles every
    tool TO the pin each session/run, so "present but out of sync" means the toolset
    is unarmed and the gate must fail loud, not silently run a different runner
    than the last environment did. Without a pin (source-tree dev checkout, no
    versions file) the first non-node_modules PATH lefthook wins — still one
    deterministic runner per environment, just unpinned."""
    candidates = list(_iter_path_lefthooks())
    if pin is None:
        return candidates[0] if candidates else None
    for cand in candidates:
        if _lefthook_version(cand) == pin:
            return cand
    return None


def _install_hook(root: str) -> int:
    """Write .git/hooks/pre-commit to exec ``release-core gate --hook``.

    The committed gate runs through the binary (WS3, release#524): there is no
    tracked root lefthook.yml for a stock git hook to discover, so we install our
    own one-line hook that defers to this verb (which points lefthook at
    .release/lefthook.yml). Idempotent; unsets any core.hooksPath redirect first
    (husky / a prior hook manager would otherwise shadow .git/hooks/)."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except FileNotFoundError:
        print("error: git not found — cannot install the pre-commit hook.", file=sys.stderr)
        return 1
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print("release-core gate: not inside a git work tree — skipping hook install.")
        return 0

    # A custom core.hooksPath redirects git away from .git/hooks/, where we write
    # the hook — unset any value so the install actually takes effect. Use
    # --unset-all (a misconfigured hooksPath can be multi-valued, which --unset
    # refuses) and SURFACE a failure: if the unset fails (permissions, etc.) the
    # hook stays shadowed and the install is effectively a no-op — reporting
    # success would be a lie (mirrors setup-dev-env.sh's warn-don't-swallow).
    hooks_path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    if hooks_path:
        unset = subprocess.run(
            ["git", "config", "--unset-all", "core.hooksPath"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        if unset.returncode != 0:
            print(
                f"warning: failed to unset core.hooksPath (={hooks_path}) — the "
                "pre-commit hook may be shadowed and NOT take effect.",
                file=sys.stderr,
            )

    hooks_dir = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    hooks_dir = os.path.join(root, hooks_dir) if not os.path.isabs(hooks_dir) else hooks_dir
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, "pre-commit")
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write(_HOOK_BODY)
    mode = os.stat(hook_path).st_mode
    os.chmod(hook_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"release-core gate: installed pre-commit hook → {hook_path}")
    _write_root_stub(root)
    return 0


def _has_active_config_lines(text: str) -> bool:
    """True iff `text` has any active (non-blank, non-comment) line — i.e. real
    config content. An empty or entirely-commented file (the starter lefthook.yml
    a stray `lefthook install` creates) has none."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines())


def _real_root_config_present(root: str) -> bool:
    """True iff the repo root has a REAL lefthook config — release-self's
    hand-authored gate or a not-yet-migrated consumer's — as opposed to OUR
    untracked redirect stub or an empty/all-commented starter (release#714).
    Neither the stub (marker-identified) NOR an inactive starter is a real config:
    both declare no active hooks and must not satisfy the unbuilt-gate fail-loud
    guard (release#567). An inactive starter is additionally clobberable by our
    stub — release#737 review: a starter that a `lefthook install` created BEFORE
    `--install-hook` ran would otherwise survive and bypass the guard."""
    for name in _DISCOVERY_NAMES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return True  # unreadable but present — treat as real, don't clobber
        if _STUB_MARKER in text[:512]:
            continue  # our own redirect stub — not a real config
        if _has_active_config_lines(text):
            return True  # authored / real config — never clobber
        # else: empty or all-commented starter — NOT real, clobberable by the stub.
    return False


def _write_root_stub(root: str) -> None:
    """Write the untracked root lefthook redirect stub + git-exclude it (release#714).

    Only in a WS3 consumer (no REAL root config): release-self and not-yet-migrated
    consumers keep a hand-authored root gate, which we must never overwrite. The
    stub stops a stray `lefthook install` (npm/pnpm `prepare`) from creating an
    empty starter lefthook.yml that would silently ungate commits. Best-effort: a
    failure here never fails the hook install (the hook itself is the gate)."""
    if _real_root_config_present(root):
        return
    stub_path = os.path.join(root, "lefthook.yml")
    try:
        # We write when the root is absent, already our stub, OR an inactive
        # starter (no active lines). TOCTOU guard: only bail if a REAL config
        # (active, non-stub content) appeared between the check above and here.
        if os.path.isfile(stub_path):
            with open(stub_path, encoding="utf-8") as fh:
                text = fh.read()
            if _STUB_MARKER not in text[:512] and _has_active_config_lines(text):
                return
        with open(stub_path, "w", encoding="utf-8") as fh:
            fh.write(_STUB_BODY)
    except OSError as exc:
        print(f"warning: could not write the root lefthook stub: {exc}", file=sys.stderr)
        return
    _git_exclude(root, "lefthook.yml")


def _git_exclude(root: str, pattern: str) -> None:
    """Add `pattern` to .git/info/exclude (untrack without a tracked .gitignore),
    matching the WS7 mirror-exclusion model. Idempotent; best-effort — a missing
    `git` (FileNotFoundError) or any git error must never crash `--install-hook`
    (#737 review)."""
    try:
        exclude = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            cwd=root,
        ).stdout.strip()
    except OSError:
        return  # git not on PATH — the stub still works untracked
    if not exclude:
        return
    exclude = os.path.join(root, exclude) if not os.path.isabs(exclude) else exclude
    try:
        existing = ""
        if os.path.isfile(exclude):
            with open(exclude, encoding="utf-8") as fh:
                existing = fh.read()
        if any(line.strip() == pattern for line in existing.splitlines()):
            return
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        sep = "" if existing == "" or existing.endswith("\n") else "\n"
        with open(exclude, "a", encoding="utf-8") as fh:
            fh.write(f"{sep}{pattern}\n")
    except OSError:
        return  # best-effort: the stub still works untracked, just not git-excluded


# lefthook's NO_COLOR summary block is one line per check: "<glyph> <name> (<time>)".
# ✓/✔ = passed, ✗/✖ = failed. Parsed ONLY for the GATE: verdict line's parenthetical
# detail; the pass/fail verdict itself comes from lefthook's exit code (authoritative),
# so a format change degrades the detail gracefully without ever lying about pass/fail.
# Glyphs are collected ONLY after the `summary:` header (matching classify.py's
# boundary), so pre-summary tool output that happens to start with a ✓/✗ can't skew
# the count or names.
# Any CSI escape (SGR color/bold + cursor moves), not just `…m` — robust against
# every ANSI sequence lefthook emits, so captured logs stay fully clean.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_GATE_SUMMARY_RE = re.compile(r"^\s*summary:")
_GATE_PASS_RE = re.compile(r"^\s*[✓✔]\s+(\S+)")
_GATE_FAIL_RE = re.compile(r"^\s*[✗✖]\s+(\S+)")


def _run_and_summarize(cmd: list[str], root: str, env: dict[str, str], *, quiet: bool) -> int:
    """Run the gate, tee its output, and print a final greppable verdict line.

    release#628: agents/humans habitually pipe `release-core gate` through
    `tail`/`grep` to bound transcript output, which masks the exit code — and
    nothing in lefthook's last lines says pass/fail. So always emit a single final
    line — `GATE: OK (N checks)` / `GATE: FAILED (name, ...)` — derived from
    lefthook's exit code, which survives any `| tail` view.

    quiet: spool lefthook's output and print it ONLY on failure; on success print
    just the verdict line — removing the incentive to pipe through tail at all.
    """
    try:
        # Force UTF-8 with a non-fatal handler: lefthook's NO_COLOR summary carries
        # ✓/✗ glyphs, and the locale default (e.g. LANG=C → ascii) would raise
        # UnicodeDecodeError and crash before the verdict line is printed.
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        # Missing (FileNotFoundError) OR present-but-not-executable (PermissionError)
        # and any other spawn failure — all OSError. Still print the verdict.
        print(f"error: could not run lefthook — the gate does not skip: {exc}", file=sys.stderr)
        print("GATE: FAILED (lefthook unavailable)")
        return 1
    passed: list[str] = []
    failed: list[str] = []
    last_line = ""
    assert proc.stdout is not None
    with contextlib.ExitStack() as stack:
        # quiet → spool to memory-then-disk (SpooledTemporaryFile rolls over past
        # 1 MiB) so a verbose failing gate can't grow output unboundedly in RAM.
        spool = (
            stack.enter_context(
                tempfile.SpooledTemporaryFile(max_size=1 << 20, mode="w+", encoding="utf-8")
            )
            if quiet
            else None
        )
        in_summary = False
        # lefthook hardcodes a couple of ANSI sequences (bold on the meta-header
        # hook name, a truecolor summary rule) that NO color knob suppresses —
        # `--colors off`, `NO_COLOR`, `CLICOLOR=0` all leave them. They garble
        # captured / CI / agent logs. When our own stdout is not a TTY, strip the
        # escapes on passthrough (the UTF-8 box glyphs stay — they render fine);
        # an interactive terminal keeps the color.
        strip_ansi = not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty())
        for line in proc.stdout:
            clean = _ANSI_RE.sub("", line)
            if _GATE_SUMMARY_RE.match(clean):
                in_summary = True
            elif in_summary:
                pm = _GATE_PASS_RE.match(clean)
                fm = _GATE_FAIL_RE.match(clean)
                if pm:
                    passed.append(pm.group(1))
                elif fm:
                    failed.append(fm.group(1))
            out_line = clean if strip_ansi else line
            if spool is not None:
                spool.write(out_line)
            else:
                sys.stdout.write(out_line)
                sys.stdout.flush()
            last_line = out_line
        rc = proc.wait()
        emitted = spool is None  # non-quiet always streamed to stdout
        if spool is not None and rc != 0:
            spool.seek(0)
            shutil.copyfileobj(spool, sys.stdout)
            emitted = True
    # Guarantee the verdict stands on its own line, even if lefthook's last chunk
    # had no trailing newline (it would otherwise be glued onto the prior output).
    if emitted and last_line and not last_line.endswith("\n"):
        sys.stdout.write("\n")
    if rc == 0:
        n = len(passed)
        detail = f"{n} check{'' if n == 1 else 's'}" if n else "all checks"
        print(f"GATE: OK ({detail})")
    else:
        detail = ", ".join(failed) if failed else "see output above"
        print(f"GATE: FAILED ({detail})")
    return rc


def _provision(argv: list[str]) -> int:
    """``release-core gate --provision`` — reconcile the gate toolset to its pins.

    The single wheel-carried provisioner (WS5/I, #762): the Python port of
    ``bin-internal/provision-gate-toolset.sh``, reached identically by CI
    (``arm-gate``) and the local boot. HARD by default (a tool it cannot
    reconcile is a non-zero exit, matching the shell's ``exit 1`` — the gate
    never skips); ``--best-effort`` warns + continues (the SessionStart / init
    path, where a transient hiccup must not abort the session)."""
    from .. import toolset

    unknown = [a for a in argv if a != "--best-effort"]
    if unknown:
        print(
            f"error: gate --provision: unexpected argument(s): {' '.join(unknown)}\n"
            "usage: release-core gate --provision [--best-effort]",
            file=sys.stderr,
        )
        return 64
    best_effort = "--best-effort" in argv
    try:
        return toolset.provision(best_effort=best_effort)
    except toolset.ProvisionError as exc:
        print(f"error: gate toolset not provisioned — {exc}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    root = _repo_root()

    if argv and argv[0] == "--install-hook":
        return _install_hook(root)

    if argv and argv[0] == "--provision":
        return _provision(argv[1:])

    # --hook: the git pre-commit entry point. Run lefthook over the STAGED set
    # (lefthook's default — NO --all-files), so stage_fixed auto-fix+restage works
    # at commit time. The bare gate keeps --all-files (no false green on an
    # unstaged edit — see the module docstring).
    hook_mode = bool(argv) and argv[0] == "--hook"
    if hook_mode:
        argv = argv[1:]

    # --quiet (release#628): print only the verdict line on success, full output
    # on failure. Our flag — strip it so it never reaches lefthook.
    quiet = "--quiet" in argv
    if quiet:
        argv = [a for a in argv if a != "--quiet"]

    # Node-deps preflight (release#718): a fresh node-consumer checkout with no
    # installed node_modules makes the gate's eslint / svelte-check hooks surface
    # a raw `svelte-check: command not found`. Fail FRIENDLY with a one-line
    # remediation instead, mirroring the runnable-verb preflight (release#735).
    # Fires only on package.json present + node_modules absent, so release's own
    # repo (no package.json) is unaffected.
    deps_rc = _node_deps_preflight(root)
    if deps_rc != 0:
        print("GATE: FAILED (node dependencies not installed)")
        return deps_rc

    pin = _pinned_lefthook_version(root)
    lefthook = _resolve_lefthook(pin)
    if lefthook is None:
        if pin:
            print(
                f"error: no lefthook at the pinned toolset version {pin} on PATH — "
                "one gate, ONE runner: the gate only runs the toolset-provisioned "
                "lefthook, never a floating or node_modules one (release#567). The "
                "gate does not skip. Re-run `release-core gate --provision` to "
                "reconcile the toolset to the pin.",
                file=sys.stderr,
            )
        else:
            print(
                "error: lefthook not found — the gate does not skip. Re-run the "
                "bootstrap (install-release-core / release-core init).",
                file=sys.stderr,
            )
        print("GATE: FAILED (lefthook unavailable)")
        return 1

    env = dict(os.environ)
    # Strip lefthook's truecolor banner — the `\e[38;2;r;g;b` gradient is a wall
    # of escape codes when an agent captures the output non-interactively. Honor
    # an explicit NO_COLOR=0 / FORCE_COLOR if the caller really wants color.
    if "FORCE_COLOR" not in env and env.get("NO_COLOR") != "0":
        env["NO_COLOR"] = "1"
    # Point lefthook at the managed config explicitly when it lives under
    # .release/ — the root discovery symlink is gone (WS3), so this is how lefthook
    # finds the gate. Default discovery covers a release-self / not-yet-migrated
    # repo that still keeps a root config. NO config anywhere is a HARD failure
    # (release#567): lefthook itself warn-and-passes on "no config found", which
    # silently ungated the first commit of a session (the hook fires before init
    # has built .release/). The gate never skips.
    managed_cfg = os.path.join(root, ".release", "lefthook.yml")
    # An empty/whitespace LEFTHOOK_CONFIG counts as unset — pop it so it never
    # leaks through to lefthook; the managed/discovery branches then apply.
    explicit_cfg = env.pop("LEFTHOOK_CONFIG", "").strip()
    if explicit_cfg:
        # A relative LEFTHOOK_CONFIG means relative to the REPO ROOT — lefthook
        # runs with cwd=root below — so resolve it there (not against the
        # caller's cwd) and pass the resolved path through.
        explicit_cfg = os.path.normpath(os.path.join(root, explicit_cfg))
        if not os.path.isfile(explicit_cfg):
            print(
                f"error: LEFTHOOK_CONFIG={explicit_cfg} does not exist — the gate does not skip.",
                file=sys.stderr,
            )
            print("GATE: FAILED (LEFTHOOK_CONFIG missing)")
            return 1
        env["LEFTHOOK_CONFIG"] = explicit_cfg
    elif os.path.isfile(managed_cfg):
        env["LEFTHOOK_CONFIG"] = managed_cfg
    elif not _real_root_config_present(root):
        # No .release/lefthook.yml AND no REAL root config. Our untracked redirect
        # stub (release#714) does NOT count — it declares no hooks, so discovering
        # it would warn-and-pass an ungated commit, exactly the boot hole this
        # guard closes (release#567). Fail loud regardless of the stub's presence.
        print(
            "error: the gate config is not built — .release/lefthook.yml is "
            "missing and the repo root has no lefthook config. Run `release-core "
            "init`, then retry: an unbuilt gate fails loud, it never "
            "warn-and-passes an ungated commit (release#567).",
            file=sys.stderr,
        )
        print("GATE: FAILED (gate config not built)")
        return 1

    # --no-auto-install: running `lefthook run` otherwise auto-SYNCS hooks — it
    # would back up our binary-driven .git/hooks/pre-commit (release-core gate
    # --install-hook) to .old and reinstall lefthook's OWN hook, which discovers
    # config from the (now-absent) repo root and so breaks the commit hook. We own
    # the hook now (WS3), so lefthook must never re-manage it.
    scope = [] if hook_mode else ["--all-files"]
    cmd = [lefthook, "run", "pre-commit", "--no-auto-install", *scope, "--no-tty", *argv]
    return _run_and_summarize(cmd, root, env, quiet=quiet)
