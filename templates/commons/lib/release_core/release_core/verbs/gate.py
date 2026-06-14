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
bootstrap (``setup-dev-env.sh`` / ``release-core init``) to provision it.

One definition AND one runner (release#567): the gate runs the TOOLSET-provisioned
lefthook — the binary ``setup-dev-env.sh`` / ``provision-gate-toolset.sh`` pin via
``gate-tool-versions.sh`` (the single source, release#499/#531) — never whatever a
consumer's ``node_modules/.bin`` or a floating PATH install happens to carry. The
verb reads the ``LEFTHOOK_VERSION`` pin (env override → ``bin/gate-tool-versions.sh``
→ the wheel-bundled copy) and resolves the FIRST PATH lefthook that reports that
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

Every gate run prints a single final line — ``GATE: OK (N checks)`` or
``GATE: FAILED (name, ...)`` — derived from lefthook's exit code (release#628).
It survives a ``release-core gate | tail`` view, where the pipe would otherwise
mask the exit status and lefthook's last lines don't say pass/fail. ``--quiet``
prints only that verdict line on success (full output on failure), so there's no
incentive to pipe through tail at all.

Usage:
  release-core gate [extra lefthook args...]   # agent/human: full --all-files gate
  release-core gate --quiet [lefthook args...] # only the verdict line on success
  release-core gate --hook [lefthook args...]  # git pre-commit: staged-set gate
  release-core gate --install-hook             # wire .git/hooks/pre-commit

Exit codes:
  0  — the gate passed (or the hook was installed)
  1  — the gate failed, the pinned lefthook is not on PATH, or the gate config is
       not built (all HARD failures — the gate never skips)
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

# The single-source pin line in gate-tool-versions.sh (release#499):
#   LEFTHOOK_VERSION="${LEFTHOOK_VERSION:-2.1.9}"
_PIN_RE = re.compile(r'^LEFTHOOK_VERSION="\$\{LEFTHOOK_VERSION:-([0-9][0-9A-Za-z.]*)\}"', re.M)

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

    Resolution order matches the shell side's contract:
      1. the LEFTHOOK_VERSION env override (the documented per-env knob),
      2. ``<root>/bin/gate-tool-versions.sh`` — release-self's live source / the
         consumer's synced mirror (a symlink into ``.release/``, so it dangles
         before init has run — hence 3),
      3. the wheel-bundled copy shipped WITH this very code
         (``release_core/_bundled_templates/…``) — always present in an installed
         wheel, so the pin is knowable even on a fresh, pre-init clone.

    None only in a source-tree dev checkout with no root file (the bundle is
    staged at wheel build, not committed)."""
    env_pin = os.environ.get("LEFTHOOK_VERSION", "").strip()
    if env_pin:
        return env_pin
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (
        os.path.join(root, "bin", "gate-tool-versions.sh"),
        os.path.join(
            pkg_root, "_bundled_templates", "templates", "commons", "bin", "gate-tool-versions.sh"
        ),
    ):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        m = _PIN_RE.search(text)
        if m:
            return m.group(1)
    return None


def _lefthook_version(binary: str) -> str | None:
    """The version a lefthook binary reports — first dotted token of --version,
    mirroring gate_version_matches in gate-tool-versions.sh."""
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
    provisioners (setup-dev-env.sh / provision-gate-toolset.sh) reconcile every
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
    return 0


# lefthook's NO_COLOR summary block is one line per check: "<glyph> <name> (<time>)".
# ✓/✔ = passed, ✗/✖ = failed. Parsed ONLY for the GATE: verdict line's parenthetical
# detail; the pass/fail verdict itself comes from lefthook's exit code (authoritative),
# so a format change degrades the detail gracefully without ever lying about pass/fail.
# Glyphs are collected ONLY after the `summary:` header (matching classify.py's
# boundary), so pre-summary tool output that happens to start with a ✓/✗ can't skew
# the count or names.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
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


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    root = _repo_root()

    if argv and argv[0] == "--install-hook":
        return _install_hook(root)

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

    pin = _pinned_lefthook_version(root)
    lefthook = _resolve_lefthook(pin)
    if lefthook is None:
        if pin:
            print(
                f"error: no lefthook at the pinned toolset version {pin} on PATH — "
                "one gate, ONE runner: the gate only runs the toolset-provisioned "
                "lefthook, never a floating or node_modules one (release#567). The "
                "gate does not skip. Re-run the bootstrap (setup-dev-env.sh) to "
                "reconcile the toolset to the pin.",
                file=sys.stderr,
            )
        else:
            print(
                "error: lefthook not found — the gate does not skip. Re-run the "
                "bootstrap (setup-dev-env.sh / release-core init).",
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
    elif not any(os.path.isfile(os.path.join(root, name)) for name in _DISCOVERY_NAMES):
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
