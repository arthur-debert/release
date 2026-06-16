"""release-cut — the one "cut a release" CLI across every Kind in the
arthur-debert/* + lex-fmt/* fleets.

Everything that actually mutates state runs in CI. This script does
the local-side pre-flight:
  1. Detect the Consumer's Kind (via release_core.manifest.detect_kind).
  2. Read the current version from that Kind's version source
     (Cargo.toml, package.json, extension.toml, git tag, …).
  3. Compute the new version (bump shortcut or literal X.Y.Z).
  4. Dispatch .github/workflows/release.yml with the new version.

Usage:
  release-cut <new-version>          # e.g. release-cut 1.8.0
  release-cut <bump-level>           # e.g. release-cut minor
                                     #   (one of: major, minor, patch)

Pre-releases: pass a semver pre-release suffix to cut an RC or beta,
e.g. `release-cut 1.8.0-rc.1`. CI marks the GitHub Release as
"pre-release" (so it isn't shown as Latest) and skips rolling
`## [Unreleased]` in CHANGELOG.md, so subsequent RCs / the final
release can still draw from the same unreleased entries.

Verification cut (`-release-rc` reserved suffix, #663): the live-fire
verification harness cuts `X.Y.Z-release-rc` to exercise the whole
release half (prep → build → sign/notarize → publish → cascade — the
build/sign/publish stages are Kind-dependent; an interpreted Kind such
as nvim-plugin / tree-sitter is just prepare → release)
WITHOUT leaving a trace. It behaves like any pre-release EXCEPT the
prepare step pushes the tag ONLY — the version-bump commit is NOT
pushed to the branch, so the consumer's version line / main history
stay clean (downstream jobs build from the bump sha, reachable via the
tag). The harness deletes the tag + GH release on teardown. This holds
for every Kind (all `prepare-release*` paths). Normal RCs (`-rc.1`,
`-beta.2`) keep the real-RC behavior (bump commit pushed).

What CI does is per-Kind (see your repo's
`.github/workflows/release.yml` thin caller of one of
`arthur-debert/release`'s reusable release workflows: `rust-cli.yml`,
`tauri-app.yml`, `electron-app.yml`, etc.). At minimum CI bumps
version files (where applicable), rolls CHANGELOG, commits + tags,
builds, and creates the GitHub Release. Per-Kind add-ons (crates.io
publish, Homebrew formula push, .deb packages, codesigning,
Marketplace upload, …) are wired in the workflow.

Current-version source by Kind:
  rust-cli, rust-lib       Cargo.toml [workspace.package].version
                           (falls back to first workspace member —
                            covers workspace-only roots like dodot's)
  tauri-app                src-tauri/Cargo.toml [package].version
  electron-app             package.json .version
  vscode-ext               package.json .version
  tree-sitter              package.json .version
  zed-extension            extension.toml version = "..."
  everything else          git describe --tags --abbrev=0
                           (nvim-plugin, go-cli, any Kind without a
                            version manifest, and repos detect-kind
                            can't classify at all — e.g. the release
                            meta repo itself. No manifest — the tag IS
                            the version; pass an explicit X.Y.Z for
                            the first release)

Preconditions (checked by CI before mutating anything):
  - version is MAJOR.MINOR.PATCH[-PRERELEASE]
  - tag vX.Y.Z does not already exist
  - version differs from the manifest's current version
  - CHANGELOG.md has entries under `## [Unreleased]`

Canary gate (#606, checked HERE before dispatching): in a repo whose
managed-repos.yaml registers canaries (the top-level `canaries:` block —
in practice only the release meta repo itself), the cut REFUSES unless
every registered `canary/<family>` context is a green commit status on
the EXACT default-branch HEAD sha being cut — the sha that
`release-core admin canary run --ref main` stamps. Exact-sha binding
makes freshness mechanical: any new commit on main invalidates the
previous round by construction. There is NO skip flag (owner decision,
#587 — escape hatches shrink). A repo with no registered canaries
(every consumer) has no gate — registry-driven inertness, not a skip.

After dispatch:
  gh run watch
  gh run list --workflow=release.yml --limit=1

Shell→Python migration: the kind-aware
version readers + semver math moved here; bin/release-cut is a thin launcher.
release-cut is release-only (a real file in bin/, NOT synced to consumers);
the distributed templates/commons/bin/release launcher execs whatever release-cut
is on $PATH. Stdout, exit codes, and the `gh workflow run` invocation are
preserved byte-for-byte — they are pinned by tests/release-cut/release-cut.bats.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable

from .. import gh, manifest, proc, version
from . import managed_repos
from .changelog import _SEMVER_TOOL_RE

USAGE = """\
usage: release-core cut <major|minor|patch|X.Y.Z[-PRERELEASE]>

Bump shortcuts operate on the current MAJOR.MINOR.PATCH (any
pre-release suffix is stripped before bumping; to step from
1.0.0-rc.1 to 1.0.0, type the version literally).

Pre-releases: pass a semver pre-release suffix (e.g. 1.8.0-rc.1) to
cut an RC/beta — CI marks the GitHub Release "pre-release" and skips
rolling `## [Unreleased]` so later RCs can still draw from it.

Reserved `-release-rc` suffix (live-fire verification, #663):
`X.Y.Z-release-rc` is a throwaway verification cut. It behaves like
any pre-release EXCEPT the prepare step pushes the TAG ONLY — the
version-bump commit is NOT pushed to the branch, so the consumer's
version line / main history stay clean. The live-fire harness deletes
the tag + GitHub Release on teardown. Do not use it for a real RC
(use `-rc.N` / `-beta.N` for those).

The current version is read from the version source for the
Consumer's Kind (Cargo.toml, package.json, extension.toml, or the
latest git tag for Kinds without a manifest — including repos with
no detectable Kind at all, e.g. manifest-less meta repos). See the
script header for the per-Kind mapping.
"""

# ---------------------------------------------------------------------
# Per-Kind current-version readers.
# Each reader returns the current version (no leading 'v') or None if it
# can't determine one — mirroring the bash readers' "print or return 1".
# ---------------------------------------------------------------------


def _read_toml_version(path: str) -> str | None:
    """Read the package version from a TOML file. Works for Cargo.toml
    ([package] / [workspace.package]) and extension.toml (top-level
    version key).

    Section-aware: starts in "matching" mode so top-level keys before
    any [section] header are read (extension.toml shape); turns off on
    any [section] header; turns back on only for [package] and
    [workspace.package]. This avoids matching a `version = "..."` line
    inside [dependencies], [workspace.dependencies.<dep>], [lints],
    [features], etc.
    """
    if not os.path.isfile(path):
        return None
    in_sect = True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if re.match(r"^\[package\][ \t]*$", line):
                    in_sect = True
                    continue
                if re.match(r"^\[workspace\.package\][ \t]*$", line):
                    in_sect = True
                    continue
                if line.startswith("["):
                    in_sect = False
                    continue
                if in_sect and re.match(r"^[ \t]*version[ \t]*=", line):
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        return m.group(1)
    except OSError:
        return None
    return None


def _read_json_version(path: str) -> str | None:
    """Read the first top-level `"version": "..."` line from a JSON file.

    The anchor `^\\s*"version"` excludes dependency-map entries like
    `"@scope/pkg": "^1.2.3"` and `"version-helper": "..."` keys nested
    inside other objects (where the field name precedes `version`).
    """
    if not os.path.isfile(path):
        return None
    pat = re.compile(r'^[ \t]*"version"[ \t]*:[ \t]*"([^"]+)"')
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = pat.match(line)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def _read_rust_version() -> str | None:
    """Rust: try the root Cargo.toml first (single crate OR workspace with
    [workspace.package].version — the standard layout for ~all rust
    consumers). If empty, probe workspace members for the first one
    carrying a literal version (covers dodot's workspace-only root,
    where the lib crate at crates/dodot-lib holds the version
    and the bin crate inherits via version.workspace = true).
    """
    v = _read_toml_version("Cargo.toml")
    if v is not None:
        return v
    members = _workspace_members("Cargo.toml")
    if not members:
        return None
    for path in members:
        # Word-split for glob expansion ("crates/*" → multiple paths).
        for expanded in _expand_glob(path):
            v = _read_toml_version(os.path.join(expanded, "Cargo.toml"))
            if v is not None:
                return v
    return None


def _workspace_members(cargo_toml: str) -> list[str]:
    """Extract paths from the `members = [...]` array. Handles single-line
    and multi-line forms, plus shell globs (e.g. "crates/*")."""
    if not os.path.isfile(cargo_toml):
        return []
    collected: list[str] = []
    in_arr = False
    try:
        with open(cargo_toml, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if re.match(r"^[ \t]*members[ \t]*=[ \t]*\[", line):
                    in_arr = True
                if in_arr:
                    collected += re.findall(r'"([^"]+)"', line)
                    if "]" in line:
                        in_arr = False
    except OSError:
        return []
    return collected


def _expand_glob(path: str) -> list[str]:
    """Expand a members-array entry the way the bash `for expanded in $path`
    word-split + glob did. A glob with no matches expands to itself (bash
    nullglob is off), so the caller still attempts the literal path."""
    import glob as _glob

    if any(ch in path for ch in "*?["):
        matches = sorted(_glob.glob(path))
        return matches if matches else [path]
    return [path]


def _read_git_tag_version() -> str | None:
    """Kinds without a local manifest: the latest git tag is the version
    source of truth."""
    res = proc.run(["git", "describe", "--tags", "--abbrev=0"], check=False)
    if res.returncode != 0:
        return None
    tag = res.stdout.strip()
    if not tag:
        return None
    return tag[1:] if tag.startswith("v") else tag


# Kinds whose version lives in a manifest file, mapped to their
# reader. This is the ONE version-source mapping: a Kind listed here reads
# its manifest; everything else — tag-sourced Kinds (nvim-plugin, go-cli),
# Kinds with no version manifest, and repos detect-kind can't classify at
# all (manifest-less meta repos like release itself) — reads the latest
# git tag, because without a manifest the tag IS the version.
_MANIFEST_READERS: dict[str, Callable[[], str | None]] = {
    "rust-cli": _read_rust_version,
    "rust-lib": _read_rust_version,
    "tauri-app": lambda: _read_toml_version("src-tauri/Cargo.toml"),
    "electron-app": lambda: _read_json_version("package.json"),
    "vscode-ext": lambda: _read_json_version("package.json"),
    "tree-sitter": lambda: _read_json_version("package.json"),
    "zed-extension": lambda: _read_toml_version("extension.toml"),
}


def _read_current_version(kind: str | None) -> str | None:
    reader = _MANIFEST_READERS.get(kind) if kind is not None else None
    if reader is not None:
        return reader()
    return _read_git_tag_version()


def _is_valid_literal_version(arg: str) -> bool:
    """Mirror the bash literal-version guard byte-for-byte.

    The bash rejected the literal unless ALL of:
      - it did not start with ``[vV]``                         (`^[vV]` test)
      - the vendored semver-tool's ``validate`` said "valid"   (SEMVER_REGEX)
      - the vendored semver-tool's ``get build`` was empty     (no ``+BUILD``)

    semver-tool's SEMVER_REGEX uses NAT = ``0|[1-9][0-9]*``, so it REJECTS
    leading-zero numeric fields (``01.0.0``, ``1.00.0``, ``1.0.0-01``) that
    ``release_core.version.parse`` would silently ACCEPT (and normalize). We
    therefore gate validity on the strict ``_SEMVER_TOOL_RE`` (shared with the
    changelog migration, which reproduces that exact regex) rather than on
    ``version.parse``. ``version.parse``/``version.bump`` stay in charge of the
    bump-shortcut computation; only this VALIDATION gate is strict.

    ``_SEMVER_TOOL_RE`` itself permits a trailing ``+BUILD`` (the regex does),
    so we keep the explicit ``+`` rejection in front of it to reproduce the
    bash ``get build`` guard. The leading-``v`` rejection reproduces ``^[vV]``.
    """
    if arg[:1] in ("v", "V"):
        return False
    if "+" in arg:  # the bash checked `semver get build` was empty
        return False
    return bool(_SEMVER_TOOL_RE.match(arg))


# ---------------------------------------------------------------------
# Canary gate (#606): the cut refuses without green canary statuses on
# the exact HEAD sha being cut.
# ---------------------------------------------------------------------

_CANARY_NEXT_ACTION = "run: release-core admin canary run --ref main"


def _canary_gate() -> int | None:
    """Refuse the cut unless every registered `canary/<family>` context is a
    green commit status on the exact sha this cut will run on. Returns None
    to proceed, or an exit code to refuse.

    The sha checked is the REMOTE default-branch head — `gh workflow run`
    dispatches release.yml on the default branch at GitHub, so that head (not
    the local HEAD, which may be ahead/behind) IS what gets cut. It is the
    same sha `release-core admin canary run --ref main` resolves and stamps,
    so exact-sha equality is the whole freshness story: a status on any older
    sha simply isn't on this one.

    Registry-driven inertness, not a skip flag: no `canaries:` registered
    (every consumer repo — they have no managed-repos.yaml at all) → no gate.
    There is NO skip flag and no env-var escape (owner decision, #587).

    The registry is read from the manifest of the REPO BEING CUT (main() has
    already chdir'd to its root) — deliberately NOT managed_repos's
    script-dir/env resolution, which the in-checkout `bin/release-core`
    launcher pins to release's own manifest: a maintainer cutting a *consumer*
    repo from a
    release dev shell must not be gated on release's canaries."""
    try:
        registry = managed_repos.canaries(os.path.join(os.getcwd(), "managed-repos.yaml"))
    except Exception as exc:  # a malformed registry must refuse loudly, not crash
        print(f"release-core cut: cannot read the canaries registry: {exc}", file=sys.stderr)
        return 1
    if not registry:
        return None

    try:
        repo_info = gh.rest("repos/{owner}/{repo}") or {}
        default_branch = repo_info["default_branch"]
        combined = gh.rest(f"repos/{{owner}}/{{repo}}/commits/{default_branch}/status") or {}
    except (gh.GhError, KeyError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError: a gh that answers with
        # non-JSON (proxy page, truncated output) must refuse, never traceback.
        print(
            f"release-core cut: canary gate: cannot read commit statuses: {exc}",
            file=sys.stderr,
        )
        return 1

    sha = combined.get("sha") or ""
    # The combined-status endpoint already collapses to the LATEST status per
    # context, so this is one state per `canary/<family>`.
    states = {s.get("context"): s.get("state") for s in combined.get("statuses") or []}
    not_green: list[str] = []
    for family in sorted(registry):
        context = f"canary/{family}"
        state = states.get(context)
        if state != "success":
            detail = state if state is not None else "missing (no status on this sha)"
            not_green.append(f"  {context}: {detail}")
    if not not_green:
        contexts = ", ".join(f"canary/{family}" for family in sorted(registry))
        print(f"Canary gate green on {sha[:12]} ({default_branch} head): {contexts}.")
        return None

    print(
        f"release-core cut: REFUSING to cut — the canary gate is not green on "
        f"{sha[:12]} (the {default_branch} head this cut runs on):\n"
        + "\n".join(not_green)
        + f"\n{_CANARY_NEXT_ACTION}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:  # noqa: C901 — flat dispatch mirrors the bash control flow
    if len(argv) != 1:
        print(USAGE, file=sys.stderr, end="")
        return 2
    arg = argv[0]
    if arg in ("-h", "--help"):
        print(USAGE, file=sys.stderr, end="")
        return 0

    try:
        repo_root = proc.out(["git", "rev-parse", "--show-toplevel"])
    except proc.ProcError as exc:
        print(exc.stderr.strip(), file=sys.stderr)
        return 1
    os.chdir(repo_root)

    # Graceful no-op for Consumers that don't ship a release workflow
    # (github-action repos, tree-sitter standalone grammars, etc.).
    if not os.path.isfile(".github/workflows/release.yml"):
        print("release-core cut: no .github/workflows/release.yml in this repo; nothing to do.")
        return 0

    if arg in ("major", "minor", "patch"):
        # Detect Kind lazily — only needed for bump shortcuts that read the
        # current version. Literal-version dispatches skip this so they work
        # in any Consumer regardless of Kind. An unclassifiable repo (a
        # manifest-less meta repo like release itself) is by definition not
        # a manifest Kind, so it takes the tag-sourced path like every other
        # Kind without a manifest.
        kind: str | None
        try:
            kind = manifest.detect_kind(".")
        except manifest.KindError:
            kind = None
        current = _read_current_version(kind)
        if current is None:
            if kind is not None and kind in _MANIFEST_READERS:
                print(
                    f"release-core cut: couldn't determine current version for Kind={kind}.\n"
                    "  Expected manifest not found or has no version field.",
                    file=sys.stderr,
                )
            else:
                source = f"Kind={kind}" if kind is not None else "a repo with no detectable Kind"
                print(
                    f"release-core cut: no git tags found — {source} reads the current version\n"
                    "  from `git describe --tags --abbrev=0`. Pass an explicit version\n"
                    "  (e.g. release-core cut 0.1.0) for the first release.",
                    file=sys.stderr,
                )
            return 1
        new_version = version.fmt(version.bump(version.parse(current), arg))
        print(f"Bumping {arg}: {current} -> {new_version}")
    elif _is_valid_literal_version(arg):
        new_version = arg
    else:
        print(
            "release-core cut: version must be MAJOR.MINOR.PATCH[-PRERELEASE] or one of: "
            f"major, minor, patch (got: {arg})",
            file=sys.stderr,
        )
        return 2

    if shutil.which("gh") is None:
        print(
            "release-core cut: gh CLI not found — install from https://cli.github.com/",
            file=sys.stderr,
        )
        return 1

    gate_rc = _canary_gate()
    if gate_rc is not None:
        return gate_rc

    print(f"Triggering release.yml for v{new_version}...")
    # proc.run captures; forward gh's own stdout/stderr so the dispatch output
    # (and any gh error) reaches the user exactly as the bash `gh ...` did.
    res = gh.workflow_run("release.yml", fields={"version": new_version})
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr)
    if res.returncode != 0:
        return res.returncode

    print(
        "\nWorkflow queued. Follow with:\n"
        "  gh run watch\n"
        "  gh run list --workflow=release.yml --limit=1"
    )
    return 0
