"""release-lex — orchestrate a coordinated release across the lex-fmt
repo chain (comms -> lex/tree-sitter-lex -> vscode/nvim/lexed).

Layer 1 of the cross-repo release automation (lex-fmt/lex#640). Walks the
dependency chain and drives each repo's release via the managed `bin/release`
(= `release-cut`) tool plus `diff-since-release`. There are no longer any
per-repo `scripts/release/*` primitives — those were retired (the
feat/retire-scripts-dir line). Release is done by the managed tooling synced
into every consumer under `bin/`:

  bin/diff-since-release  — commits on the current branch since the last final
                            release tag (the "is there anything to release?"
                            and "what changed?" source). Exits 1 if no release
                            tags exist yet.
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
  get-commits-since-release -> diff-since-release.
  should-release            -> diff-since-release has commit lines (non-empty).
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
exit codes, the --only filter, the run-id extractor, the should-release
decision over diff-since-release output, and the status-line rendering.

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

# `diff-since-release` exit-code contract (see templates/<kind>/bin/diff-since-release):
#   0   — success; stdout has the `Changes since <tag>:` / `---` header + log
#   1   — no final release tags exist yet (benign: first release is human-driven)
#   >1  — genuine failure (git error, corrupt repo, etc.; surfaces under set -e,
#         e.g. 128) — MUST be surfaced, never masked as "nothing to release".
NO_TAGS_RC = 1

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
--status:     read-only — run diff-since-release in each repo and print
              a one-line answer per repo. Useful answer to "what
              would cascade if I cut comms now?"
"""


# --------------------------------------------------------------------------
# Pure helpers (unit-tested).
# --------------------------------------------------------------------------


def github_slug_for(name: str) -> str:
    """The lex-fmt GitHub slug for a repo key (empty string if unknown)."""
    return _GITHUB_SLUGS.get(name, "")


def has_releasable_commits(diff_output: str, rc: int) -> bool:
    """Decide whether a repo has releasable commits, from `diff-since-release`
    output + exit code.

    `diff-since-release` prints a two-line header (`Changes since <tag>:` then
    `---`) followed by `git log --oneline <tag>..HEAD`. When nothing is new the
    log section is empty, so "releasable" = at least one line AFTER the `---`
    separator. A non-zero rc is never releasable; the caller is responsible for
    distinguishing the benign no-tags exit (``NO_TAGS_RC``) from a genuine
    failure (rc > 1) BEFORE relying on this — see ``_release_one`` /
    ``_status_one``. We never guess a first version, so no-tags = not releasable.
    """
    if rc != 0:
        return False
    lines = diff_output.splitlines()
    try:
        sep = lines.index("---")
    except ValueError:
        # No separator (unexpected shape) — be conservative: nothing to release.
        return False
    body = [ln for ln in lines[sep + 1 :] if ln.strip()]
    return len(body) > 0


def count_commits(diff_output: str) -> int:
    """Number of commit lines in `diff-since-release` output (lines after the
    `---` separator). 0 if the separator is absent or no commits follow."""
    lines = diff_output.splitlines()
    try:
        sep = lines.index("---")
    except ValueError:
        return 0
    return sum(1 for ln in lines[sep + 1 :] if ln.strip())


def render_status_line(key: str, releasable: bool, rc: int, count: int) -> str:
    """Render one status-mode line.
      releasable        -> '⚠ would release: N commit(s) since last release'
      not, rc 0         -> '✓ up to date (no commits since last release)'
      rc == NO_TAGS_RC  -> '✗ no release tags yet (diff-since-release exited 1)'
      rc > 1            -> '✗ diff-since-release FAILED (exited <rc>)'
    Only ``NO_TAGS_RC`` means "no tags yet"; any other non-zero rc is a genuine
    failure (git error, corrupt repo) and must not be reported as "no tags".
    The label is left-padded to 18 cols for column alignment."""
    label = f"{key:<18}"
    if rc != 0:
        if rc == NO_TAGS_RC:
            return f"{label} ✗ no release tags yet (diff-since-release exited {rc})"
        return f"{label} ✗ diff-since-release FAILED (exited {rc})"
    if releasable:
        return f"{label} ⚠ would release: {count} commit(s) since last release"
    return f"{label} ✓ up to date (no commits since last release)"


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
      3. each repo: dir exists + the managed bin/release + bin/diff-since-release
         tools are present and executable -> 1
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
    # --status only reads diff-since-release; the cut path additionally needs
    # bin/release. Validate exactly the tools the chosen mode will invoke.
    needed = ("diff-since-release",) if cfg["status_mode"] else ("diff-since-release", "release")
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
    this is now: refresh main -> decide via diff-since-release -> `bin/release
    <bump>` (dispatch release.yml) -> watch the resulting CI run.
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

    # should-release decision: diff-since-release has commit lines?
    res = proc.run(["./bin/diff-since-release"], check=False)
    # A non-zero rc other than NO_TAGS_RC is a genuine failure (git error,
    # corrupt repo — surfaces as 128 under the script's `set -e`). NEVER mask
    # it as "nothing to release": a silent skip here stalls the whole cascade.
    if res.returncode not in (0, NO_TAGS_RC):
        print(
            f"  ✗ diff-since-release FAILED for {key} (exit {res.returncode}); aborting\n"
            f"    {res.stderr.strip()}",
            file=sys.stderr,
        )
        return res.returncode
    if not has_releasable_commits(res.stdout, res.returncode):
        if res.returncode == NO_TAGS_RC:
            print(f"  ↳ no release tags yet; skipping {key}")
        else:
            print(f"  ↳ no new commits since latest release tag; skipping {key}")
        return 0
    count = count_commits(res.stdout)
    print(f"  ↳ {count} commit(s) since latest release")

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
    """Read-only: fetch remote state, run diff-since-release, print one line.
    (A subshell-equivalent: we save/restore cwd to preserve isolation.)"""
    path = cfg["repos"][key]
    saved = os.getcwd()
    try:
        os.chdir(path)
        # Best-effort fetch so the tag/log view is current; ignore failures.
        proc.run(["git", "fetch", "--quiet", "origin"], check=False)
        res = proc.run(["./bin/diff-since-release"], check=False)
        releasable = has_releasable_commits(res.stdout, res.returncode)
        count = count_commits(res.stdout)
        print(render_status_line(key, releasable, res.returncode, count))
        # A non-zero rc other than NO_TAGS_RC is a genuine failure — echo its
        # stderr so an operator scanning --status output doesn't miss the cause.
        if res.returncode not in (0, NO_TAGS_RC) and res.stderr.strip():
            print(f"    {res.stderr.strip()}", file=sys.stderr)
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
        print("Cascade status (read-only — runs diff-since-release in each repo):")
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
