"""managed-repos — accessor for the portfolio fleet manifest.

Reads managed-repos.yaml (the ONLY source of truth — no discovery) and
maps each repo to its on-disk location with ZERO logic: the manifest
states each repo's `path`, and a repo resolves to `$REPOS_ROOT/<path>`.
No probing, no single-vs-multi heuristics, no org guessing.

$REPOS_ROOT defaults to ~/h (a dev machine). Point it at an empty dir
and use --clone for a fresh, self-contained fleet checkout.

Usage:
  managed-repos [--list]   # owner/name, one per line (default)
  managed-repos --paths    # owner/name <TAB> abspath <TAB> found|missing
  managed-repos --clone    # clone missing repos; fetch+reset existing ones
                           # to origin's default branch UNCONDITIONALLY, then
                           # name the ref/sha each clone now sits at (#624).

Existing clones are ALWAYS refreshed — there is no opt-out mode (the old
`--refresh` flag was removed in #624: a frozen-clone use case is a manual
clone, not a verify/list mode). The readout names the fetched ref/sha so the
sweep's freshness is visible, never assumed.

DESTRUCTIVE on a non-disposable root: the refresh is a `git reset --hard`, so
$REPOS_ROOT should be a DISPOSABLE dir (e.g. /tmp/...) for hermetic clones.
A HEALTHY clone with UNCOMMITTED work is detected and SKIPPED-with-warning
rather than reset (the data-loss guard), so a clean/hermetic clone refreshes
safely while a live ~/h checkout's uncommitted work is never silently
discarded.

SELF-HEALING (#748): a leftover dir that is NOT a healthy clone of the
expected remote — a crashed run's half-clone, a corrupt .git, the wrong repo
at that path — is treated as disposable cruft and re-cloned, never protected.
The data-loss guard only ever fires on a healthy repo, so a fixed reused root
can no longer rot: poisoned dirs heal on the next --clone instead of failing
every consumer until a manual `rm -rf`.

Any mode accepts trailing owner/name args to restrict to that subset:
  managed-repos --clone arthur-debert/padz lex-fmt/lex

Exit codes:
  0  — ok
  1  — a clone/refresh failed
  2  — manifest or dependency error
  64 — bad usage

Shell→Python migration: the logic moved to
this verb; bin/managed-repos is a thin wrapper. Stdout (the <TAB>-joined --paths /
--list lines) is preserved byte-for-byte — release-verify-fleet and
audit-portfolio parse it with `IFS=$'\t' read`.
"""

from __future__ import annotations

import os
import shutil
import sys

from .. import gh, proc, yamlio

USAGE = __doc__ or ""


def _help() -> None:
    # Mirror the bash `show_help`: print the usage block (lines 2..first blank
    # of the original header). We render the docstring's leading section, which
    # is the same content.
    print(_usage_block())


def _usage_block() -> str:
    """The help body — the docstring up to (but not including) the migration note."""
    lines = USAGE.strip("\n").splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("Shell→Python migration"):
            break
        out.append(line)
    return "\n".join(out).rstrip("\n")


def _usage_error(msg: str) -> int:
    print(msg, file=sys.stderr)
    print(_usage_block(), file=sys.stderr)
    return 64


def _manifest_path() -> str:
    """Resolve the manifest path, preserving the bash precedence:

    1. MANAGED_REPOS_MANIFEST — explicit override (tests point it at a fixture).
    2. <script_dir>/../managed-repos.yaml — the default the bash used, where
       script_dir is bin/. The wrapper exports MANAGED_REPOS_SCRIPT_DIR (its own
       realpath'd bin/ dir) so this resolves identically regardless of cwd.
    3. managed-repos.yaml in the cwd — last-ditch fallback if invoked outside
       the wrapper (the verb is release-only, so this is the repo root in practice).
    """
    override = os.environ.get("MANAGED_REPOS_MANIFEST")
    if override:
        return override
    script_dir = os.environ.get("MANAGED_REPOS_SCRIPT_DIR")
    if script_dir:
        return os.path.normpath(os.path.join(script_dir, "..", "managed-repos.yaml"))
    return "managed-repos.yaml"


def _pairs(manifest: str, filter_set: list[str]) -> list[tuple[str, str]]:
    """Every active entry as (repo, path), in manifest declaration order.

    `.projects` is a mapping of project-name → list of {repo, path}; flatten it.
    An optional owner/name filter restricts the set (order: manifest order,
    matching the bash which streams the manifest and tests each row)."""
    data = yamlio.load(manifest) or {}
    projects = data.get("projects") or {}
    pairs: list[tuple[str, str]] = []
    for entries in projects.values():
        for entry in entries or []:
            pairs.append((entry["repo"], entry["path"]))
    if filter_set:
        wanted = set(filter_set)
        pairs = [(r, p) for (r, p) in pairs if r in wanted]
    return pairs


def pairs(manifest: str | None = None) -> list[tuple[str, str]]:
    """Every active ``projects:`` entry as (repo, path) — the public in-process
    accessor over the fleet registry (the CLI's ``--list``/``--paths`` modes
    print the same set). Used by verbs that resolve a repo without spawning
    the accessor subprocess (``admin repos poke``)."""
    return _pairs(manifest or _manifest_path(), [])


def expect_verify_fail(manifest: str | None = None) -> dict[str, str]:
    """The optional per-entry ``expect-verify-fail: <reason>`` annotations
    (#594), as repo → reason.

    An annotated repo's gate FAIL in the hermetic ``admin repos verify`` sweep
    is EXPECTED for an environmental reason the sweep cannot satisfy (e.g. a
    sibling checkout) and not mechanically inferable from the failing checks.
    Shrink-only ratchet: verify flags the annotation as STALE the moment the
    repo passes — remove it then. npm-deps artifacts need no annotation (they
    classify mechanically; see release_core.classify)."""
    data = yamlio.load(manifest or _manifest_path()) or {}
    projects = data.get("projects") or {}
    annotated: dict[str, str] = {}
    for entries in projects.values():
        for entry in entries or []:
            reason = entry.get("expect-verify-fail")
            if reason:
                annotated[str(entry["repo"])] = str(reason)
    return annotated


def canaries(manifest: str | None = None) -> dict[str, str]:
    """The top-level ``canaries:`` block as family → owner/name (#587).

    Canary repos are release-owned synthetic infra, NOT fleet consumers: they
    live OUTSIDE ``projects:`` on purpose so everything built on :func:`_pairs`
    (verify / migrate / inbox / audit, the ``--list``/``--paths`` modes) never
    sweeps them (owner decision OQ6). This accessor is the only reader.

    A missing manifest is an empty registry, not an error: only the release
    meta repo carries managed-repos.yaml, so in a consumer repo "no manifest"
    mechanically means "no canaries registered" — which is what keeps the
    slice-4 cut gate (#606) registry-driven rather than skip-flagged."""
    path = manifest or _manifest_path()
    if not os.path.isfile(path):
        return {}
    data = yamlio.load(path) or {}
    block = data.get("canaries") or {}
    return {str(family): str(repo) for family, repo in block.items()}


def project_repos(manifest: str | None = None) -> set[str]:
    """Every owner/name under ``projects:`` — the fleet consumers ONLY.

    The canary-side refusal surface (#604): ``canary init`` must hard-refuse
    to operate on (let alone force-push) a fleet consumer, so it checks its
    resolved repo against this set. A missing manifest is an empty set, same
    as :func:`canaries`."""
    path = manifest or _manifest_path()
    if not os.path.isfile(path):
        return set()
    return {repo for repo, _path in _pairs(path, [])}


def known_repos(manifest: str | None = None) -> set[str]:
    """Every owner/name the registry knows: ``projects:`` entries + ``canaries:``.

    The validation surface for per-repo targeting (#601): a verb that accepts
    an explicit ``--repos`` list checks each entry against this set —
    managed-repos.yaml is the ONLY fleet source of truth. Canary repos are
    included on purpose: they are deliberately excluded from the ``projects:``
    sweeps (OQ6) but must be individually targetable (e.g. installing
    RELEASE_TOKEN on arthur-debert/release-canary-rust)."""
    path = manifest or _manifest_path()
    repos = {repo for repo, _path in _pairs(path, [])}
    repos.update(canaries(path).values())
    return repos


def validate_repo_targets(repos: list[str], manifest: str | None = None) -> str:
    """Check an explicit per-repo target list against the registry (#601).

    Returns the error message to print, or "" when every entry is known.
    Shared by the ``admin secrets token|install`` ``--repos`` flags: an
    unknown owner/name is a hard error naming the registry, so a typo can
    never write a secret to an unmanaged repo. An unreadable registry is the
    same hard error — surfaced as a message, never a traceback: YamlError
    (missing yq, missing/unparseable manifest) plus the structural failures a
    malformed-but-parseable manifest raises out of ``_pairs``/``canaries``
    (wrong shapes / missing keys → KeyError/TypeError/AttributeError)."""
    try:
        known = known_repos(manifest)
    except (yamlio.YamlError, KeyError, TypeError, AttributeError) as exc:
        return (
            "error: cannot read the fleet registry (managed-repos.yaml): "
            f"{type(exc).__name__}: {exc}"
        )
    unknown = sorted(set(repos) - known)
    if not unknown:
        return ""
    return (
        "error: repo(s) not registered in managed-repos.yaml "
        f"(projects: or canaries:): {', '.join(unknown)}"
    )


def main(argv: list[str]) -> int:  # noqa: C901 — flat dispatch mirrors the bash modes
    mode = "list"
    filter_set: list[str] = []

    for arg in argv:
        if arg == "--list":
            mode = "list"
        elif arg == "--paths":
            mode = "paths"
        elif arg == "--clone":
            mode = "clone"
        elif arg in ("-h", "--help"):
            _help()
            return 0
        elif arg.startswith("-"):
            return _usage_error(f"managed-repos: unknown arg: {arg}")
        else:
            filter_set.append(arg)

    manifest = _manifest_path()
    root = os.environ.get("REPOS_ROOT") or os.path.join(os.path.expanduser("~"), "h")

    if not os.path.isfile(manifest):
        print(f"managed-repos: manifest not found: {manifest}", file=sys.stderr)
        return 2
    if shutil.which("yq") is None:
        print("managed-repos: yq required (mikefarah/yq v4)", file=sys.stderr)
        return 2

    pairs = _pairs(manifest, filter_set)

    if mode == "list":
        for repo, _path in pairs:
            print(repo)
        return 0

    if mode == "paths":
        for repo, path in pairs:
            abspath = os.path.join(root, path)
            found = "found" if os.path.isdir(os.path.join(abspath, ".git")) else "missing"
            print(f"{repo}\t{abspath}\t{found}")
        return 0

    # mode == "clone"
    if shutil.which("gh") is None:
        print("managed-repos: gh required for --clone", file=sys.stderr)
        return 2
    # --clone now drives git directly (fetch/reset/rev-parse on existing
    # clones, #624) — guard it the same as gh so a missing git is a clean
    # exit 2, never a FileNotFoundError traceback out of proc.run.
    if shutil.which("git") is None:
        print("managed-repos: git required for --clone", file=sys.stderr)
        return 2
    return _clone(pairs, root)


def _clone(pairs: list[tuple[str, str]], root: str) -> int:
    """Clone every missing repo and UNCONDITIONALLY fetch+reset every existing
    one to origin's default branch (#624).

    Reusing a clone without fetching is a quiet-wrong default: the managed
    surface gets synced from the candidate ref either way, so a stale clone's
    consumer-authored half makes the sweep *look* faithful while it lies. The
    readout names the ref/sha each clone now sits at so the freshness is
    visible, never assumed.

    SELF-HEALING (#748): a leftover dir that is NOT a healthy clone of the
    expected remote — a half-finished clone from a crashed run, a corrupt
    .git, the wrong repo at this path — is DISPOSABLE: it is removed and
    re-cloned, never protected. A fixed reused root would otherwise rot:
    `git status` fails on a corrupt repo, the old guard read that as "dirty —
    protect it," and every consumer then failed sync until the path was
    manually `rm -rf`'d (the crashed-run poisoning #747 surfaced). Only a
    HEALTHY repo (valid .git, the expected `origin`) is eligible for the
    refresh / data-loss path.

    One exception, and it is a DATA-LOSS guard not a freshness opt-out: a
    HEALTHY clone with a DIRTY working tree is skipped-with-warning rather
    than hard-reset, so pointing REPOS_ROOT at live ~/h checkouts can never
    silently discard uncommitted work (:func:`_is_dirty`). Hermetic /tmp
    clones are always clean, so they always refresh."""
    rc = 0
    for repo, path in pairs:
        abspath = os.path.join(root, path)
        existing = os.path.isdir(os.path.join(abspath, ".git"))
        if existing and _is_healthy_clone(abspath, repo):
            # DATA-LOSS GUARD: the refresh hard-resets the working tree, which
            # would silently discard uncommitted work if REPOS_ROOT points at a
            # live checkout (default ~/h). A dirty tree is SKIPPED with a loud
            # warning — never reset — and the sweep continues. This is not the
            # forbidden `--refresh` opt-out (#624): hermetic /tmp clones are
            # always clean, so the guard never fires there and they always
            # refresh; it only protects a human's real working tree.
            if _is_dirty(abspath):
                print(
                    f"⚠ {repo}: uncommitted changes — skipping refresh to protect "
                    "your work (point REPOS_ROOT at a disposable dir for hermetic "
                    f"clones) ({abspath})",
                    file=sys.stderr,
                )
                continue
            ref_sha = _refresh_one(abspath)
            if ref_sha is None:
                print(f"→ {repo}: refresh FAILED ({abspath})", file=sys.stderr)
                rc = 1
            else:
                print(f"→ {repo}: refreshed to {ref_sha} ({abspath})", file=sys.stderr)
        else:
            if os.path.exists(abspath):
                # A leftover dir that isn't a healthy clone of THIS repo is
                # poisoned cruft, not work to protect (#748): remove it so the
                # re-clone lands on a clean path. The dirty guard above already
                # claimed every healthy clone, so reaching here means corrupt /
                # wrong-remote / half-cloned — disposable by definition.
                verb = "cloning into" if not existing else "re-cloning (poisoned clone removed)"
                print(f"→ {repo}: {verb} {abspath}", file=sys.stderr)
                shutil.rmtree(abspath, ignore_errors=True)
            else:
                print(f"→ {repo}: cloning into {abspath}", file=sys.stderr)
            os.makedirs(os.path.dirname(abspath), exist_ok=True)
            # gh repo clone works in gh-authenticated sandboxes where plain
            # git clone is restricted (matches clone-lex-* convention).
            if gh.repo_clone(repo, abspath).returncode != 0:
                print(f"→ {repo}: clone FAILED", file=sys.stderr)
                rc = 1
            else:
                head = proc.run(
                    ["git", "-C", abspath, "rev-parse", "--short", "HEAD"],
                    check=False,
                )
                sha = head.stdout.strip() if head.returncode == 0 else "?"
                print(f"→ {repo}: cloned at {sha}", file=sys.stderr)
    return rc


def _is_healthy_clone(abspath: str, repo: str) -> bool:
    """True if ``abspath`` is a usable git clone of ``repo``'s ``origin``.

    The self-healing gate (#748): only a healthy clone is eligible for the
    refresh + data-loss-guard path; everything else (a crashed run's
    half-clone, a corrupt .git, the wrong repo checked out at this path) is
    DISPOSABLE and gets re-cloned. Two checks, both `check=False` so a broken
    repo yields ``False`` rather than raising:

    1. ``git rev-parse --git-dir`` succeeds — the dir is a real git repo, not
       an empty/half-written tree that merely has a ``.git`` entry.
    2. ``origin`` points at ``repo`` — a path holding a DIFFERENT repo (a
       manifest path reshuffle, a botched clone) must not be reset onto this
       repo's default branch.

    Fail toward re-clone: any ambiguity (git error, unreadable remote) returns
    ``False`` so the path is rebuilt clean rather than silently mis-synced."""
    git_dir = proc.run(["git", "-C", abspath, "rev-parse", "--git-dir"], check=False)
    if git_dir.returncode != 0:
        return False
    remote = proc.run(["git", "-C", abspath, "remote", "get-url", "origin"], check=False)
    if remote.returncode != 0:
        return False
    return _remote_matches(remote.stdout.strip(), repo)


def _remote_matches(url: str, repo: str) -> bool:
    """True if ``url`` is an origin URL for ``owner/name`` (``repo``).

    Accepts the shapes `gh repo clone` / `git clone` produce — HTTPS
    (`https://github.com/owner/name(.git)`), SSH
    (`git@github.com:owner/name(.git)`), and a bare `owner/name` — by
    normalizing to the trailing `owner/name`, optional `.git` stripped. We
    match on the slug, not the host, so a clone via either transport is
    recognized as the same repo."""
    norm = url.strip()
    if norm.endswith(".git"):
        norm = norm[: -len(".git")]
    norm = norm.replace(":", "/")
    parts = [p for p in norm.split("/") if p]
    if len(parts) < 2:
        return False
    return "/".join(parts[-2:]) == repo


def _is_dirty(abspath: str) -> bool:
    """True if the clone's working tree has uncommitted changes.

    Only reached for a HEALTHY clone (:func:`_is_healthy_clone` ran first), so
    a `git status` failure here is not the corrupt-repo case — that was already
    routed to re-clone. `git status --porcelain` prints one line per
    changed/untracked path and nothing for a clean tree — so non-empty stdout
    means dirty. A git failure on an otherwise-healthy repo is still treated as
    dirty: fail toward PROTECTING the tree, never toward a hard reset we can't
    justify."""
    status = proc.run(
        ["git", "-C", abspath, "status", "--porcelain"],
        check=False,
    )
    if status.returncode != 0:
        return True
    return bool(status.stdout.strip())


def _refresh_one(abspath: str) -> str | None:
    """fetch+reset an existing clone to origin's default branch.

    Returns the ``<branch>@<short-sha>`` the clone now sits at (so the sweep
    readout can name its freshness), or ``None`` on any failure."""
    head = proc.run(
        ["git", "-C", abspath, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        check=False,
    )
    default = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else "main"
    # Strip the fixed `refs/remotes/origin/` prefix ONLY — never rsplit on the
    # last `/`, which would truncate a branch that legitimately contains slashes
    # (e.g. `release/v1` → `v1`, then fetch/reset target the wrong ref).
    prefix = "refs/remotes/origin/"
    if default.startswith(prefix):
        default = default[len(prefix) :]
    # Shallow fetch — the verify sweep lints the working tree, never history.
    fetch = proc.run(
        ["git", "-C", abspath, "fetch", "--quiet", "--depth", "1", "origin", default],
        check=False,
    )
    if fetch.returncode != 0:
        return None
    reset = proc.run(
        ["git", "-C", abspath, "reset", "--quiet", "--hard", f"origin/{default}"],
        check=False,
    )
    if reset.returncode != 0:
        return None
    rev = proc.run(
        ["git", "-C", abspath, "rev-parse", "--short", "HEAD"],
        check=False,
    )
    sha = rev.stdout.strip() if rev.returncode == 0 and rev.stdout.strip() else "?"
    return f"{default}@{sha}"
