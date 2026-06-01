"""release-lex — orchestrate a coordinated release across the lex-fmt
repo chain (comms -> lex/tree-sitter-lex -> vscode/nvim/lexed).

Layer 1 of the cross-repo release automation (lex-fmt/lex#640). Walks the
dependency chain and drives each repo's release via the managed `bin/release`
(= `release-cut`) tool. There are no longer any per-repo `scripts/release/*`
primitives — those were retired (the feat/retire-scripts-dir line).

The should-release decision is computed GENERICALLY here via plain git (commits
since the last final release tag), NOT via a per-repo `bin/diff-since-release`.
That tool was absent for the manifest-less Kinds (docs-site / tree-sitter /
nvim-plugin — 3 of the 6 lex-chain repos) and diverged across the others, so the
cascade died at the first repo lacking it. The generic decision replicates the
old `diff-since-release` contract exactly (see `decide_release` below): commits
since the last NON-prerelease tag reachable from HEAD; no final tags ⇒ the
no-tags case (a first release is human-driven). Release is still done by the
managed tooling synced into every consumer under `bin/`:

  bin/release             — thin shim that execs `release-cut`. release-cut is
                            Kind-aware: it reads the current version from the
                            Consumer's canonical manifest source (Cargo.toml,
                            package.json, extension.toml, or the latest git tag
                            for manifest-less Kinds), computes the new version
                            from a bump shortcut or literal X.Y.Z, and
                            DISPATCHES `.github/workflows/release.yml` with that
                            version. CI (the reusable per-Kind release workflow)
                            does the actual bump + CHANGELOG roll + commit + tag
                            + build + GitHub Release.

The old primitive→responsibility mapping, for the record:
  get-current-version       -> release-cut reads it from the Kind manifest.
  get-commits-since-release -> the generic `git log <last-final-tag>..HEAD`.
  should-release            -> that log is non-empty (commits since last final).
  update-release            -+ both fold into `release-cut`: the bump + CHANGELOG
  trigger-release           -+ roll + commit + tag now happen IN CI, dispatched
                               by release-cut. There is no longer a local
                               "bump files + git add" step to commit/PR/merge,
                               so the old per-repo "branch -> update-release ->
                               commit -> PR -> admin-merge -> trigger-release"
                               tail collapses into a single `release-cut`
                               dispatch + a `gh run watch` on the resulting
                               release.yml run. See the PR body for the design
                               note on why the local PR/admin-merge mechanics
                               are gone (CI owns the mutation now).

release-lex is a release-only tool (a real file in bin/, NOT synced to
consumers). The orchestration sequence, stdout, exit codes, and the dry-run /
--only / --status gates are preserved where they still have meaning — pinned by
tests/release-lex/release-lex.bats and the pure-decision pytest unit tests.

The live multi-repo orchestration (fetch/checkout/pull/submodule, `release-cut`
dispatch, `gh run list/watch`) is genuine side-effecting glue and is NOT
unit-tested (it requires live repos + GitHub — that is the script's whole
point). What IS pure and tested: the github-slug map, arg parsing + validation
exit codes, the --only filter, the run-id extractor, the generic git
should-release decision (last-final-tag selection + log over a mocked proc
layer), and the status-line rendering.

Usage:
  release-lex <bump-kind> \\
    --comms <path> --lex <path> --tree-sitter <path> \\
    --vscode <path> --nvim <path> --lexed <path> \\
    [--dry-run] [--only <name>[,<name>...]]

<bump-kind> is one of: patch | minor | major | <X.Y.Z>

Exit codes:
  0  — all attempted releases cut successfully
  1  — at least one repo failed mid-flight (orchestrator stops there)
  64 — bad usage
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from .. import gh, proc

# should-release decision states (the generic git decision, computed by
# `decide_release`). Replaces the old `diff-since-release` exit-code contract —
# the decision is now computed in-process from git, so there is no external
# tool's exit code to interpret. The four states preserve the EXACT semantics
# the old per-Kind `diff-since-release` drove:
#   RELEASE   — final tag found + commits since it (the old rc 0 + non-empty log)
#   UPTODATE  — final tag found + NO commits since it (the old rc 0 + empty log)
#   NOTAGS    — no final (non-prerelease) tag reachable from HEAD; a first
#               release is human-driven (the old rc 1 / NO_TAGS exit)
#   ERROR     — a genuine git failure (corrupt repo, unborn HEAD, bad ref); MUST
#               be surfaced loudly, never masked as "nothing to release" (the old
#               rc > 1, e.g. 128, under `set -e`)
RELEASE = "release"
UPTODATE = "uptodate"
NOTAGS = "notags"
ERROR = "error"

# git exits 128 on the failures we want to surface loudly (corrupt repo, unborn
# HEAD, bad ref). Used as the synthetic process exit code when a decision is
# ERROR but the underlying rc wasn't captured (it always is — kept for clarity).
GIT_ERROR_RC = 128

# Walk order (dependency-respecting). Each repo is included only if its path
# was supplied via flag.
ORDER = ("comms", "lex", "tree-sitter", "vscode", "nvim", "lexed")

_GITHUB_SLUGS = {
    "comms": "lex-fmt/comms",
    "lex": "lex-fmt/lex",
    "tree-sitter": "lex-fmt/tree-sitter-lex",
    "vscode": "lex-fmt/vscode",
    "nvim": "lex-fmt/nvim",
    "lexed": "lex-fmt/lexed",
}

USAGE = """\
Usage:
  release-lex <bump-kind> \\
    --comms <path> --lex <path> --tree-sitter <path> \\
    --vscode <path> --nvim <path> --lexed <path> \\
    [--dry-run] [--only <name>[,<name>...]]

  release-lex --status \\
    --comms <path> --lex <path> --tree-sitter <path> \\
    --vscode <path> --nvim <path> --lexed <path>

<bump-kind>:  patch | minor | major | <X.Y.Z>
--dry-run:    print everything but make no real state changes
--only:       restrict to a subset of repos (still dependency-ordered)
--status:     read-only — compute the should-release decision (git commits
              since the last final release tag) in each repo and print a
              one-line answer per repo. Useful answer to "what
              would cascade if I cut comms now?"
"""


# --------------------------------------------------------------------------
# Pure helpers (unit-tested).
# --------------------------------------------------------------------------


def github_slug_for(name: str) -> str:
    """The lex-fmt GitHub slug for a repo key (empty string if unknown)."""
    return _GITHUB_SLUGS.get(name, "")


def latest_final_tag(tag_lines: str) -> str:
    """The last FINAL (non-prerelease) release tag from `git tag --list 'v*'
    --sort=-version:refname --merged HEAD` output, or '' if there is none.

    Replicates the old `diff-since-release` selection EXACTLY: tags are already
    highest-version-first; we drop any prerelease tag (anything containing `-`,
    e.g. `v1.0.0-rc.1`) because pre-releases share the `## [Unreleased]`
    changelog scope with the final they lead up to — the relevant diff is against
    the last *final*. The first surviving line is the answer. A repo with only
    prerelease tags therefore yields '' (the no-final-tags case)."""
    for line in tag_lines.splitlines():
        tag = line.strip()
        if tag and "-" not in tag:
            return tag
    return ""


class Decision:
    """The generic should-release decision for one repo (computed by
    ``decide_release`` from plain git). ``state`` is one of RELEASE / UPTODATE /
    NOTAGS / ERROR; ``count`` is the commit count (only meaningful for RELEASE);
    ``tag`` is the last final tag ('' for NOTAGS/ERROR); ``rc`` is the git exit
    code that drove an ERROR (0 otherwise); ``stderr`` carries git's error text
    for an ERROR so the caller can surface it loudly."""

    def __init__(self, state: str, *, count: int = 0, tag: str = "", rc: int = 0, stderr: str = ""):
        self.state = state
        self.count = count
        self.tag = tag
        self.rc = rc
        self.stderr = stderr


def decide_release(path: str) -> Decision:
    """Compute the should-release decision for the repo at ``path`` GENERICALLY,
    via plain git — no per-repo `bin/diff-since-release` needed.

    Steps (replicating the old `diff-since-release` contract):
      1. `git tag --list 'v*' --sort=-version:refname --merged HEAD`. A nonzero
         exit is a genuine git failure (corrupt repo / unborn HEAD) → ERROR,
         surfaced loudly. NEVER read as "no tags".
      2. Pick the last FINAL (non-prerelease) tag. None → NOTAGS (a first release
         is human-driven; we never guess a first version).
      3. `git --no-pager log --oneline <tag>..HEAD`. A nonzero exit is a genuine
         git failure → ERROR. Empty stdout → UPTODATE. Non-empty → RELEASE with
         the commit count.
    """
    tags = gh.git_tag_list_merged("v*", cwd=path)
    if tags.returncode != 0:
        return Decision(ERROR, rc=tags.returncode or GIT_ERROR_RC, stderr=tags.stderr or "")
    tag = latest_final_tag(tags.stdout)
    if not tag:
        return Decision(NOTAGS)
    log = gh.git_log_oneline(f"{tag}..HEAD", cwd=path)
    if log.returncode != 0:
        return Decision(ERROR, tag=tag, rc=log.returncode or GIT_ERROR_RC, stderr=log.stderr or "")
    count = sum(1 for ln in log.stdout.splitlines() if ln.strip())
    if count > 0:
        return Decision(RELEASE, count=count, tag=tag)
    return Decision(UPTODATE, tag=tag)


def render_status_line(key: str, decision: Decision) -> str:
    """Render one status-mode line from a :class:`Decision`.
      RELEASE   -> '⚠ would release: N commit(s) since <tag>'
      UPTODATE  -> '✓ up to date (no commits since <tag>)'
      NOTAGS    -> '✗ no final release tags yet (first release is human-driven)'
      ERROR     -> '✗ should-release decision FAILED (git exited <rc>)'
    Only NOTAGS means "no tags yet"; an ERROR is a genuine git failure (corrupt
    repo, bad ref) and must not be reported as "no tags". The label is
    left-padded to 18 cols for column alignment."""
    label = f"{key:<18}"
    if decision.state == ERROR:
        return f"{label} ✗ should-release decision FAILED (git exited {decision.rc})"
    if decision.state == NOTAGS:
        return f"{label} ✗ no final release tags yet (first release is human-driven)"
    if decision.state == RELEASE:
        return f"{label} ⚠ would release: {decision.count} commit(s) since {decision.tag}"
    return f"{label} ✓ up to date (no commits since {decision.tag})"


def parse_only(only: str) -> list[str]:
    """Split a comma-separated --only value the way bash `IFS=',' read -ra` did."""
    if not only:
        return []
    return only.split(",")


def _looks_like_version(bump: str) -> bool:
    """The loose `*.*.*` validation arm: at least two literal dots (so the
    string has >=3 dot-separated pieces). Looser than a strict semver check —
    release-cut itself does the strict validation when it runs."""
    return bump.count(".") >= 2


def _first_database_id(runs_json: str) -> str:
    """`.[0].databaseId // empty` over `gh run list --json databaseId` output:
    the first run's databaseId as a string, or '' if absent/empty/unparseable."""
    import json

    text = runs_json.strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, list) or not data:
        return ""
    val = data[0].get("databaseId")
    if val is None:
        return ""
    return str(val)


# --------------------------------------------------------------------------
# Argument parsing + validation.
# --------------------------------------------------------------------------


class _Usage(Exception):
    """Signal a usage error (exit 64) with a message for stderr."""

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


def _parse_args(argv: list[str]) -> dict:
    """Parse argv into a config dict, or raise _Usage / SystemExit.

    - no args -> usage() to stdout, exit 64
    - first arg '--status' -> status mode, BUMP_KIND='status'; else the first
      positional is BUMP_KIND
    - remaining: --<repo> <path>, --dry-run, --only <val>, --status
    - unknown arg -> 'release-lex: unknown arg: <arg>' to stderr, exit 64
    """
    if len(argv) < 1:
        print(USAGE, end="")
        raise SystemExit(64)

    rest = list(argv)
    status_mode = False
    if rest[0] == "--status":
        status_mode = True
        bump_kind = "status"
        rest = rest[1:]
    else:
        bump_kind = rest[0]
        rest = rest[1:]

    dry_run = False
    only = ""
    repos: dict[str, str] = {}

    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("--comms", "--lex", "--tree-sitter", "--vscode", "--nvim", "--lexed"):
            key = arg[2:]
            repos[key] = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        elif arg == "--dry-run":
            dry_run = True
            i += 1
        elif arg == "--only":
            only = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        elif arg == "--status":
            status_mode = True
            i += 1
        else:
            raise _Usage(f"release-lex: unknown arg: {arg}")

    return {
        "status_mode": status_mode,
        "bump_kind": bump_kind,
        "dry_run": dry_run,
        "only": only,
        "repos": repos,
    }


def _validate(cfg: dict) -> int | None:
    """Post-parse validation; returns an exit code to abort on, or None to
    proceed:
      1. bad bump-kind (non-status mode) -> 64
      2. no repos -> 64
      3. each repo: dir exists -> 1; and in cut mode the managed bin/release tool
         is present and executable -> 1. The should-release decision is now
         computed generically via git, so NO bin/diff-since-release is required
         in any repo / mode (it was absent for 3 of the 6 lex-chain Kinds).
    """
    if not cfg["status_mode"]:
        bump = cfg["bump_kind"]
        if bump in ("patch", "minor", "major") or _looks_like_version(bump):
            pass
        else:
            print(
                f"release-lex: bad bump-kind: {bump} (want patch|minor|major|X.Y.Z)",
                file=sys.stderr,
            )
            return 64

    if not cfg["repos"]:
        print("release-lex: no repo paths supplied", file=sys.stderr)
        return 64

    # Validate paths + the managed tools each repo must carry under bin/. We
    # iterate in the stable ORDER so the first failure reported is deterministic.
    keys = [k for k in ORDER if k in cfg["repos"]]
    keys += [k for k in cfg["repos"] if k not in ORDER]
    # --status only computes the generic git decision (no bin/ tool); the cut
    # path additionally needs bin/release. Validate exactly that.
    needed = () if cfg["status_mode"] else ("release",)
    for key in keys:
        path = cfg["repos"][key]
        if not os.path.isdir(path):
            print(f"release-lex: not a directory: {path} (for --{key})", file=sys.stderr)
            return 1
        for tool in needed:
            tool_path = os.path.join(path, "bin", tool)
            if not (os.path.isfile(tool_path) and os.access(tool_path, os.X_OK)):
                print(
                    f"release-lex: {key} at {path} is missing bin/{tool}",
                    file=sys.stderr,
                )
                print(
                    f"  (re-sync the managed release tooling into lex-fmt/"
                    f"{_repo_name(key)} — run `release-sync` there)",
                    file=sys.stderr,
                )
                return 1
    return None


def _repo_name(key: str) -> str:
    """Bare repo name (slug minus the `lex-fmt/` owner) for error messages."""
    slug = github_slug_for(key)
    return slug.split("/", 1)[1] if "/" in slug else (slug or key)


def _is_allowed(name: str, allowed: list[str], only_raw: str) -> bool:
    """With no --only, everything is allowed; otherwise only names present in
    the comma-split --only list."""
    if not only_raw:
        return True
    return name in allowed


# --------------------------------------------------------------------------
# Side-effecting orchestration (faithful glue — NOT unit-tested).
# --------------------------------------------------------------------------


def _run(cmd: list[str], dry_run: bool, *, cwd: str | None = None) -> None:
    """echo + execute, OR echo only if --dry-run. Prints `  $ <cmd>` then runs
    it (inheriting the parent's stdout/stderr) when not in dry-run. A nonzero
    exit raises (CalledProcessError)."""
    print("  $ " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, check=True)  # noqa: S603 — cmd is a constructed list


def _release_one(key: str, cfg: dict) -> int:
    """Cut one repo's release. Returns 0 on success/skip, 1 on failure.

    New model (post scripts/release retirement): the bump + CHANGELOG roll +
    commit + tag all happen IN CI, dispatched by `bin/release` (= release-cut).
    There is no local file mutation to branch/commit/PR/admin-merge anymore, so
    this is now: refresh main -> decide generically via git (commits since the
    last final release tag) -> `bin/release <bump>` (dispatch release.yml) ->
    watch the resulting CI run.
    """
    path = cfg["repos"][key]
    dry_run = cfg["dry_run"]
    bump_kind = cfg["bump_kind"]
    gh_repo = github_slug_for(key)

    print()
    print(f"═══ {key} ({gh_repo}) at {path} ═══")

    os.chdir(path)
    _run(["git", "fetch", "origin"], dry_run)
    _run(["git", "checkout", "main"], dry_run)
    _run(["git", "pull", "--ff-only"], dry_run)
    # Submodule init for consumers of comms (no-op if absent).
    if os.path.isfile(".gitmodules"):
        _run(["git", "submodule", "update", "--init", "--recursive"], dry_run)

    # should-release decision: commits since the last final release tag (generic
    # git — no per-repo bin/diff-since-release).
    decision = decide_release(path)
    # An ERROR is a genuine git failure (corrupt repo, bad ref — exits 128).
    # NEVER mask it as "nothing to release": a silent skip stalls the cascade.
    if decision.state == ERROR:
        print(
            f"  ✗ should-release decision FAILED for {key} (git exit {decision.rc}); aborting\n"
            f"    {decision.stderr.strip()}",
            file=sys.stderr,
        )
        return decision.rc
    if decision.state in (NOTAGS, UPTODATE):
        if decision.state == NOTAGS:
            print(f"  ↳ no final release tags yet; skipping {key}")
        else:
            print(f"  ↳ no new commits since {decision.tag}; skipping {key}")
        return 0
    print(f"  ↳ {decision.count} commit(s) since {decision.tag}")

    # `bin/release <bump>` is Kind-aware: it reads the current version from the
    # manifest, computes the new version, and dispatches release.yml. CI does
    # the bump + CHANGELOG roll + commit + tag + build + GitHub Release.
    if dry_run:
        print(f"  $ ./bin/release {bump_kind}")
        print("  ↳ dry-run: skipping release-cut dispatch + CI wait")
        return 0

    print(f"  $ ./bin/release {bump_kind}")
    cut = subprocess.run(  # noqa: S603 — constructed list, no shell
        ["./bin/release", bump_kind], check=False
    )
    if cut.returncode != 0:
        print(f"  ✗ bin/release {bump_kind} failed (exit {cut.returncode})", file=sys.stderr)
        return 1
    print(f"  ↳ release.yml dispatched for {key} ({bump_kind})")

    # Find the release-CI run release-cut just dispatched and watch it. Filter
    # to release.yml and take the most recent run (dispatch is near-instant;
    # the brief sleep lets the run register before we query).
    time.sleep(8)
    runs = gh.run_list(
        repo=gh_repo,
        workflow_eq="release.yml",
        limit=1,
        json_fields=["databaseId"],
    )
    run_id = _first_database_id(runs.stdout)
    if not run_id:
        if runs.returncode != 0:
            print(
                f"  ✗ gh run list failed:\n"
                f"STDOUT: {runs.stdout.strip()}\n"
                f"STDERR: {runs.stderr.strip()}",
                file=sys.stderr,
            )
        print(
            f"  ✗ could not find dispatched release CI run for {key}",
            file=sys.stderr,
        )
        print(
            f"    inspect manually: gh run list --repo {gh_repo} --workflow=release.yml",
            file=sys.stderr,
        )
        return 1
    print(f"  ↳ watching release CI run {run_id}...")
    gh.run_watch(run_id, repo=gh_repo, exit_status=True)
    print(f"  ✓ release CI complete for {key}")
    return 0


def _status_one(key: str, cfg: dict) -> None:
    """Read-only: fetch remote state, compute the generic git should-release
    decision, print one line. (A subshell-equivalent: we save/restore cwd to
    preserve isolation.)"""
    path = cfg["repos"][key]
    saved = os.getcwd()
    try:
        os.chdir(path)
        # Best-effort fetch so the tag/log view is current; ignore failures.
        proc.run(["git", "fetch", "--quiet", "origin"], check=False)
        decision = decide_release(path)
        print(render_status_line(key, decision))
        # An ERROR is a genuine git failure — echo its stderr so an operator
        # scanning --status output doesn't miss the cause.
        if decision.state == ERROR and decision.stderr.strip():
            print(f"    {decision.stderr.strip()}", file=sys.stderr)
    finally:
        os.chdir(saved)


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0

    try:
        cfg = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    except _Usage as exc:
        if exc.message:
            print(exc.message, file=sys.stderr)
        return 64

    abort = _validate(cfg)
    if abort is not None:
        return abort

    if cfg["status_mode"]:
        print("Cascade status (read-only — commits since last final tag, per repo):")
        print()
        for key in ORDER:
            if cfg["repos"].get(key):
                _status_one(key, cfg)
        print()
        print("Legend: ✓ up to date  ⚠ release would happen  ✗ error / no tags")
        return 0

    if cfg["dry_run"]:
        print("release-lex: dry-run mode — no dispatches will be made")

    allowed = parse_only(cfg["only"])
    for key in ORDER:
        if cfg["repos"].get(key) and _is_allowed(key, allowed, cfg["only"]):
            try:
                rc = _release_one(key, cfg)
            except (proc.ProcError, subprocess.CalledProcessError, gh.GhError):
                # Any failed command aborts the whole run with a nonzero status.
                return 1
            if rc != 0:
                return rc

    print()
    print("release-lex: all attempted releases complete.")
    return 0
