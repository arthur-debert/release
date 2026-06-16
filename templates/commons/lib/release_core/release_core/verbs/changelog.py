"""changelog — the changelog-* family (shell→Python migration, Phase 1).

One module for the tight changelog cluster; each former bash script maps to a
``*_main`` here and is driven by its own thin wrapper on ``$PATH``:

  - :func:`orchestrator_main`  ← changelog (the dispatch front-end)
  - :func:`add_main`           ← changelog-add
  - :func:`cut_main`           ← changelog-cut
  - :func:`render_main`        ← changelog-render

The CLI contract is consumed by consumers + CI (changelog-tests.yml, the
changelog-check action, bin-internal/roll-changelog.sh shelling to changelog)
so stdout, exit codes, flags, and the generated CHANGELOG.md / fragment bytes
match the old bash byte-for-byte. Validation reproduces the (now removed)
vendored semver-tool's regex semantics (NAT — no leading zeros — and
NAT/ALPHANUM prerelease identifiers) exactly via _SEMVER_TOOL_RE so validation
parity holds. The standalone `validate`/`get` edge the rest of the pipeline
shelled out to now lives in release_core.verbs.semver (semver).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

from .. import version

# --- shared helpers ---------------------------------------------------------

# Reproduces share/semver-tool's SEMVER_REGEX exactly (NAT = '0|[1-9][0-9]*',
# ALPHANUM = '[0-9]*[A-Za-z-][0-9A-Za-z-]*', IDENT = NAT|ALPHANUM), anchored.
# release_core.version.parse is laxer (accepts a leading 'v' and leading zeros),
# so we gate validity on this regex and only use version.parse for ordering.
_NAT = r"(?:0|[1-9][0-9]*)"
_ALPHANUM = r"(?:[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_IDENT = rf"(?:{_NAT}|{_ALPHANUM})"
_SEMVER_TOOL_RE = re.compile(
    rf"^{_NAT}\.{_NAT}\.{_NAT}"
    rf"(?:-{_IDENT}(?:\.{_IDENT})*)?"
    rf"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _is_valid_semver(v: str) -> bool:
    """True iff ``v`` validates the way the vendored semver-tool's `validate` did."""
    return bool(_SEMVER_TOOL_RE.match(v))


def _resolve_changelog_root() -> str | None:
    """Walk up from cwd for an existing CHANGELOG/; else fall back to git root.

    Mirrors the bash `resolve_changelog_root`: returns the first ancestor (incl.
    cwd) that contains a CHANGELOG/ dir, otherwise `git rev-parse --show-toplevel`
    (or None if that fails — not in a git repo).
    """
    d = os.getcwd()
    while d != "/":
        if os.path.isdir(os.path.join(d, "CHANGELOG")):
            return d
        d = os.path.dirname(d)
    try:
        top = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    out = top.stdout.strip()
    return out or None


def _frag_with_newline(data: bytes) -> bytes:
    """A fragment's bytes, with a single '\\n' appended iff non-empty and not
    already newline-terminated. Mirrors bash `[[ -s f && tail -c1 != "" ]]`."""
    if data and not data.endswith(b"\n"):
        return data + b"\n"
    return data


def _sorted_fragments(changelog_dir: str) -> list[str]:
    """unreleased-*.md fragment paths in stable byte order (LC_ALL=C glob)."""
    try:
        names = os.listdir(changelog_dir)
    except OSError:
        return []
    frags = [n for n in names if n.startswith("unreleased-") and n.endswith(".md")]
    frags.sort()  # byte order; ASCII filenames so codepoint sort == LC_ALL=C
    return [os.path.join(changelog_dir, n) for n in frags]


# --- changelog-add ----------------------------------------------------------

ADD_USAGE = (
    "usage: changelog-add [--force] [--section <name>] <slug> [body...]\n"
    "\n"
    "Write CHANGELOG/unreleased-<slug>.md from [body...] (or stdin).\n"
    "\n"
    "Options:\n"
    "  --force            overwrite an existing fragment\n"
    "  --section <name>   write a `### <name>` group heading above the bullet\n"
    "                     (keepachangelog-style); default is a bare bullet (no\n"
    "                     heading), matching the verbatim renderer's flat list\n"
    "  -h, --help         show this help and exit"
)

# A fragment is a bare `- bullet` by default: the renderer concatenates fragment
# bytes verbatim into a flat list under the version (it does NOT group by
# section), so a per-fragment `### <section>` heading would scatter stray
# headings through that list. `--section <name>` is opt-in for repos that
# genuinely author keepachangelog-style sectioned fragments (release#720).
DEFAULT_SECTION = ""

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def add_main(argv: list[str]) -> int:
    """changelog-add [--force] [--section <name>] <slug> [body...]"""
    args = list(argv)

    # Intercept help before any validation so `add --help` / `add -h` (and
    # `add --force --help`) print usage instead of failing slug validation on
    # the literal "--help" token (release#686 — recurred fleet-wide). Scoped to
    # the LEADING option region — before the positional <slug> and before a bare
    # `--` terminator — so a literal `--help` can still be passed in the body
    # (release#732 review). `--section <value>` skips its value, which is never
    # a help trigger.
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(ADD_USAGE)
            return 0
        if a == "--":
            break
        if a == "--section":
            i += 2
            continue
        if a == "--force" or a.startswith("--section="):
            i += 1
            continue
        break  # reached the positional <slug>; stop scanning for help

    root = _resolve_changelog_root()
    if not root:
        print(
            "error: no CHANGELOG/ found above cwd and not inside a git repository",
            file=sys.stderr,
        )
        return 1
    os.chdir(root)

    force = False
    section = DEFAULT_SECTION
    # Parse leading options (--force, --section <name>) in any order before the
    # positional slug. A bare "--" terminates option parsing.
    while args:
        if args[0] == "--force":
            force = True
            args = args[1:]
        elif args[0] == "--section":
            if len(args) < 2:
                print(ADD_USAGE, file=sys.stderr)
                return 2
            section = args[1]
            args = args[2:]
        elif args[0].startswith("--section="):
            section = args[0][len("--section=") :]
            args = args[1:]
        elif args[0] == "--":
            args = args[1:]
            break
        else:
            break

    slug = args[0] if args else ""
    if not slug:
        print(ADD_USAGE, file=sys.stderr)
        return 2
    args = args[1:]

    if re.fullmatch(r"[0-9]+", slug):
        slug = f"pr-{slug}"

    if not _SLUG_RE.match(slug):
        print(
            f"error: slug must match [A-Za-z0-9][A-Za-z0-9._-]* (got: {slug})",
            file=sys.stderr,
        )
        return 2

    os.makedirs("CHANGELOG", exist_ok=True)
    target = os.path.join("CHANGELOG", f"unreleased-{slug}.md")

    if os.path.exists(target) and not force:
        print(
            f"error: {target} already exists (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    # Inline args: joined by a single space + one trailing newline (printf
    # '%s\n' "$*"). No args: stdin bytes (cat > target).
    body = (" ".join(args) + "\n").encode() if args else sys.stdin.buffer.read()
    # A body the caller already opened with its own `###` section heading is a
    # complete fragment — write it verbatim (don't bullet the heading line, and
    # don't prepend a second section). Otherwise apply the conventions:
    #   1. `- ` bullet (CHANGELOG/README.txt mandates a leading bullet; the
    #      renderer concatenates fragment bytes verbatim into the rendered list).
    #      Skipped when the body already starts with `-` (never double-bulleted).
    #   2. a `### <section>` heading above the bullet (release#720) so a fragment
    #      reads as a keepachangelog section; `--section ''` opts out.
    if not body.lstrip().startswith(b"###"):
        body = _ensure_bullet(body)
        body = _with_section(body, section)
    with open(target, "wb") as fh:
        fh.write(body)
    print(f"wrote {target}")
    return 0


def _with_section(body: bytes, section: str) -> bytes:
    """Prepend a ``### <section>`` heading + blank line to ``body``.

    ``section`` empty → ``body`` unchanged (bare-bullet, the old behavior).
    A ``section`` containing newlines would otherwise emit a multi-line `###`
    heading (scattering stray headings / breaking the bullet grouping), so the
    name is collapsed to a single line first — interior whitespace runs become a
    single space, leading/trailing whitespace stripped. An all-whitespace name
    collapses to empty → no heading."""
    section = " ".join(section.split())
    if not section:
        return body
    return f"### {section}\n\n".encode() + body


def _ensure_bullet(body: bytes) -> bytes:
    """Prepend a `- ` markdown bullet to the first non-blank line of ``body``
    unless it already starts with `-` (the CHANGELOG fragment convention).

    Any leading blank lines are preserved (the bullet attaches to the first
    content line, never to a blank), so a body like ``b"\\ntext"`` becomes
    ``b"\\n- text"`` rather than a dangling ``b"- \\ntext"``. An all-blank/empty
    body is left untouched."""
    stripped = body.lstrip()
    if not stripped or stripped.startswith(b"-"):
        return body
    # Re-attach the leading whitespace the lstrip removed, then bullet the
    # first content line.
    lead = body[: len(body) - len(stripped)]
    return lead + b"- " + stripped


# --- changelog-cut ----------------------------------------------------------

CUT_USAGE = "usage: changelog-cut <version>"


def cut_main(argv: list[str]) -> int:
    """changelog-cut <version>"""
    ver = argv[0] if argv else ""
    if not ver:
        print(CUT_USAGE, file=sys.stderr)
        return 2

    if ver[:1] in ("v", "V"):
        print(
            f"error: version must be bare semver without 'v' prefix (got: {ver})",
            file=sys.stderr,
        )
        return 2

    if not _is_valid_semver(ver):
        print(
            f"error: version must be valid semver (got: {ver})",
            file=sys.stderr,
        )
        return 2

    root = _resolve_changelog_root()
    if not root:
        print(
            "error: no CHANGELOG/ found above cwd and not inside a git repository",
            file=sys.stderr,
        )
        return 1
    os.chdir(root)

    if not os.path.isdir("CHANGELOG"):
        print("error: CHANGELOG/ directory not found", file=sys.stderr)
        return 1

    fragments = _sorted_fragments("CHANGELOG")
    if not fragments:
        print(
            "error: no CHANGELOG/unreleased-*.md fragments to cut",
            file=sys.stderr,
        )
        return 1

    target = os.path.join("CHANGELOG", f"{ver}.md")
    if os.path.exists(target):
        print(
            f"error: {target} already exists; refuse to overwrite an existing version file",
            file=sys.stderr,
        )
        return 1

    today = datetime.now(UTC).strftime("%Y-%m-%d")

    buf = bytearray()
    buf += f"## {ver} - {today}\n\n".encode()
    for f in fragments:
        with open(f, "rb") as fh:
            buf += _frag_with_newline(fh.read())
    with open(target, "wb") as fh:
        fh.write(buf)

    for f in fragments:
        os.remove(f)

    n = len(fragments)
    print(f"cut {target} ({n} fragment(s))")
    return 0


# --- changelog-render -------------------------------------------------------


def render_main(argv: list[str]) -> int:
    """changelog-render — regenerate CHANGELOG.md from CHANGELOG/*."""
    root = _resolve_changelog_root()
    if not root:
        print(
            "error: no CHANGELOG/ found above cwd and not inside a git repository",
            file=sys.stderr,
        )
        return 1
    os.chdir(root)

    if not os.path.isdir("CHANGELOG"):
        print("error: CHANGELOG/ directory not found", file=sys.stderr)
        return 1

    # Validate every CHANGELOG/<stem>.md version filename; collect the good ones.
    bad: list[str] = []
    versions: list[str] = []
    for name in sorted(os.listdir("CHANGELOG")):
        if not name.endswith(".md"):
            continue
        stem = name[: -len(".md")]
        if stem in ("README", "legacy"):
            continue
        if stem.startswith("unreleased-"):
            continue
        if stem[:1] in ("v", "V"):
            bad.append(name)
            continue
        if _is_valid_semver(stem):
            versions.append(stem)
        else:
            bad.append(name)

    if bad:
        print(
            f"error: unparseable version filename(s) in CHANGELOG/: {' '.join(bad)}",
            file=sys.stderr,
        )
        return 1

    versions_output = _sort_versions(versions)

    unreleased = _sorted_fragments("CHANGELOG")

    buf = bytearray()
    buf += b"<!-- generated - do not edit. See CHANGELOG/README.txt -->\n\n"
    buf += b"# Changelog\n\n"
    buf += b"## Unreleased\n\n"
    if unreleased:
        for f in unreleased:
            with open(f, "rb") as fh:
                buf += _frag_with_newline(fh.read())
        buf += b"\n"

    for v in versions_output:
        with open(os.path.join("CHANGELOG", f"{v}.md"), "rb") as fh:
            buf += fh.read()
        buf += b"\n"

    legacy = os.path.join("CHANGELOG", "legacy.md")
    if os.path.isfile(legacy):
        with open(legacy, "rb") as fh:
            buf += fh.read()

    # Atomic write: temp file in the same dir, then rename (matches mktemp+mv).
    target = "CHANGELOG.md"
    fd, tmp = tempfile.mkstemp(prefix="CHANGELOG.md.tmp.", dir=".")
    try:
        # mkstemp creates the temp file 0o600 and os.replace preserves that;
        # the bash `mktemp + mv` produced a umask-default (typically 0o644)
        # CHANGELOG.md. Re-derive the umask-respecting mode so the rendered
        # file stays world-readable for downstream tools/CI.
        try:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as fh:
            fh.write(buf)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    print(f"rendered {target}")
    return 0


def _sort_versions(versions: list[str]) -> list[str]:
    """Descending semver order, bare release ABOVE its prereleases (semver §11).

    Replaces the bash `sort -V -r | awk` two-step. release_core.version.SemVer
    orders natively per §11 (release > prerelease; numeric identifiers rank below
    alphanumeric); reverse-sorting the parsed versions yields the same sequence
    the bash produced, but the bash GROUPS by base version then emits bare-first
    within a group. SemVer's native descending order already places a release
    above all of its own prereleases, and orders distinct base versions
    correctly, so a single reverse sort is equivalent.
    """
    return sorted(versions, key=version.parse, reverse=True)


# --- changelog (orchestrator) -----------------------------------------------

ORCHESTRATOR_USAGE = """usage: changelog <command> [args...]

Commands:
  add [--force] [--section <name>] <slug> [body...]
                                   add an unreleased fragment
  cut <version>                    cut unreleased fragments into a version file
  render                           regenerate CHANGELOG.md
  new-version <version>            cut + render

See CHANGELOG/README.txt."""


def orchestrator_main(argv: list[str]) -> int:
    """changelog <command> [args...] — dispatch to the add/cut/render verbs."""
    cmd = argv[0] if argv else ""
    rest = argv[1:]

    if cmd == "add":
        return add_main(rest)
    if cmd == "cut":
        return cut_main(rest)
    if cmd == "render":
        return render_main([])
    if cmd == "new-version":
        if not rest:
            print("usage: changelog new-version <version>", file=sys.stderr)
            return 2
        rc = cut_main([rest[0]])
        if rc != 0:
            return rc
        return render_main([])
    if cmd in ("-h", "--help", "help"):
        print(ORCHESTRATOR_USAGE)
        return 0
    if cmd == "":
        print(ORCHESTRATOR_USAGE, file=sys.stderr)
        return 2
    print(f"unknown command: {cmd}", file=sys.stderr)
    print(ORCHESTRATOR_USAGE, file=sys.stderr)
    return 2
