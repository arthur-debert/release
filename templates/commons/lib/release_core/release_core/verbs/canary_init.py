"""release-core admin canary init — idempotent create/reset of a canary repo.

Makes the canary-repo lifecycle reproducible (#604; slice 1 of #587
hand-created `release-canary-rust`). One verb converges a family's canary to
the canonical state: a PUBLIC `arthur-debert/release-canary-<family>` repo
seeded from the per-kind fixture under tests/fixtures/, booted onto the pull
model from THIS checkout (`install-release-core --from-source` + a sandboxed
`release-core init`), carrying exactly the secrets the family needs (never
the publish trio), under the canonical ruleset, and registered in the
`canaries:` block of managed-repos.yaml.

Usage:
  release-core admin canary init --family <f>
      [--reset]    force-push the canary's main back to the canonical seed
                   (wedge escape). Sanctioned ONLY for registered canary
                   repos — the verb hard-refuses anything else.
      [--root DIR] hermetic workdir (default /tmp/release-canary-init-$USER)

Run from inside arthur-debert/release. Idempotent: re-running converges —
repo exists → not recreated; main already seeded → left alone (without
--reset); RELEASE_TOKEN present → kept (pipe a canonical PAT on stdin to
set/rotate it); ruleset → upserted; registry entry → appended once.

The family→fixture mapping is declarative: each tests/fixtures/<kind>/ dir
carries a `.canary-family` marker naming its family (rust-cli → `rust`).
Adding a family (#605: vscode-ext) = a new fixture dir with a marker + this
verb run; the verb itself never changes.

Fail-closed (#587 owner decisions, no escape hatches):
  - the publish secrets (CRATES_IO_KEY, HOMEBREW_TAP_TOKEN, NPM_TOKEN) are
    NEVER installed, and their presence on the repo fails the run until
    removed (`gh secret delete <name> -R <repo>`);
  - a canary without RELEASE_TOKEN fails the run unless a PAT is piped;
  - the verb refuses to touch any repo that is in the `projects:` fleet or
    whose name is not `release-canary-*`.

Exit codes:
  0  — converged
  1  — a convergence step failed (secrets / policy / registration)
  2  — setup error (registry / fixture / gh preflight / refusal / seed)
  64 — bad usage
"""

from __future__ import annotations

import os
import shutil
import sys

from .. import gh, proc
from . import apply_ruleset, install_release_token, managed_repos
from .canary_run import CanaryError, _run_sandboxed, _sandbox_env, rewrite_self_refs
from .release_advance_major import _highest_major

USAGE = __doc__ or ""

OWNER = "arthur-debert"
REPO_PREFIX = "release-canary-"
FAMILY_MARKER = ".canary-family"

# The publish trio a canary must NEVER carry (#587: cuts on a canary are
# prereleases with every publish path fenced — these secrets existing at all
# would arm those paths).
FORBIDDEN_SECRETS = ("CRATES_IO_KEY", "HOMEBREW_TAP_TOKEN", "NPM_TOKEN")

SEED_COMMIT_SUBJECT = "canary: canonical seed"


def _usage_block() -> str:
    """The docstring up to (not including) the exit-codes block."""
    lines = USAGE.strip("\n").splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("Exit codes:"):
            break
        out.append(line)
    return "\n".join(out).rstrip("\n")


# ── pure functions (pytest-covered) ──────────────────────────────────────────


def find_fixture(fixtures_root: str, family: str) -> str:
    """The fixture dir whose ``.canary-family`` marker names ``family``.

    Scans tests/fixtures/*/ — the marker makes the family→kind mapping
    declarative in-tree (rust → rust-cli today; #605 adds vscode-ext →
    vscode-ext) so a new family never touches this verb. Raises CanaryError
    when no fixture (or more than one) claims the family."""
    matches: list[str] = []
    if os.path.isdir(fixtures_root):
        for name in sorted(os.listdir(fixtures_root)):
            marker = os.path.join(fixtures_root, name, FAMILY_MARKER)
            if not os.path.isfile(marker):
                continue
            with open(marker, encoding="utf-8") as fh:
                if fh.read().strip() == family:
                    matches.append(os.path.join(fixtures_root, name))
    if not matches:
        raise CanaryError(
            f"no fixture for family {family!r}: no {fixtures_root}/*/{FAMILY_MARKER} "
            f"names it — author one (see tests/fixtures/rust-cli/)"
        )
    if len(matches) > 1:
        raise CanaryError(
            f"family {family!r} is claimed by more than one fixture: " + ", ".join(matches)
        )
    return matches[0]


def resolve_repo(family: str, registry: dict[str, str], projects: set[str], reset: bool) -> str:
    """The canary repo for ``family``, with every fail-closed refusal applied.

    Registered family → the registry value (managed-repos.yaml is the source
    of truth). Unregistered: --reset refuses (force-push is sanctioned only
    for registered canaries); create derives `arthur-debert/release-canary-
    <family>` and registration appends it later. Either way the repo must be
    a `release-canary-*` name and must NOT be a `projects:` fleet consumer."""
    repo = registry.get(family)
    if repo is None:
        if reset:
            raise CanaryError(
                f"--reset refused: family {family!r} is not in the canaries: registry "
                f"(registered: {', '.join(sorted(registry)) or 'none'}) — force-push is "
                "sanctioned only for registered canary repos"
            )
        repo = f"{OWNER}/{REPO_PREFIX}{family}"
    if repo in projects:
        raise CanaryError(
            f"refusing to operate on {repo}: it is a projects: fleet consumer in "
            "managed-repos.yaml, not a canary"
        )
    name = repo.split("/", 1)[1] if "/" in repo else repo
    if not name.startswith(REPO_PREFIX):
        raise CanaryError(
            f"refusing to operate on {repo}: not a {REPO_PREFIX}* repo — canary init "
            "only ever touches release-owned canary repos"
        )
    return repo


def register_canary_text(text: str, family: str, repo: str) -> str:
    """managed-repos.yaml with ``family: repo`` present in the ``canaries:`` block.

    Pure text edit (the manifest is hand-maintained YAML with load-bearing
    comments — a parse/dump round-trip would destroy them): the entry is
    appended after the block's last `  family: repo` line. Already-registered
    family → unchanged (idempotent). No `canaries:` block → CanaryError (the
    block carries the OQ6 design comment; it is never auto-created)."""
    lines = text.splitlines(keepends=True)
    block_start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "canaries:":
            block_start = i
            break
    if block_start is None:
        raise CanaryError("managed-repos.yaml has no `canaries:` block — restore it first")
    insert_at = block_start + 1
    for j in range(block_start + 1, len(lines)):
        stripped = lines[j].strip()
        if lines[j].startswith("  ") and stripped and not stripped.startswith("#"):
            key = stripped.split(":", 1)[0]
            if key == family:
                return text  # already registered
            insert_at = j + 1
        else:
            break
    lines.insert(insert_at, f"  {family}: {repo}\n")
    return "".join(lines)


def forbidden_secrets_present(names: list[str]) -> list[str]:
    """The publish-trio secrets present on the repo (must be empty to proceed)."""
    return [n for n in FORBIDDEN_SECRETS if n in names]


# ── gh/git seams ─────────────────────────────────────────────────────────────


def _gh_preflight() -> bool:
    return proc.run(["gh", "auth", "status"], check=False).returncode == 0


def _repo_exists(repo: str) -> bool:
    return gh.repo_view(repo=repo, check=False).returncode == 0


def _repo_create(repo: str, description: str) -> None:
    """`gh repo create <repo> --public` — PUBLIC is an owner decision (OQ2),
    deliberately not a flag."""
    res = proc.run(
        ["gh", "repo", "create", repo, "--public", "--description", description],
        check=False,
    )
    if res.returncode != 0:
        raise CanaryError(f"gh repo create {repo} failed: {res.stderr.strip()}")


def _clone(repo: str, dest: str) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    if gh.repo_clone(repo, dest).returncode != 0:
        raise CanaryError(f"clone of {repo} failed")


def _main_seeded(dest: str) -> bool:
    """True when the clone's origin/main exists (the repo has a seeded main)."""
    return gh.git_rev_parse_verify("origin/main", cwd=dest)


def _copy_fixture(fixture_dir: str, dest: str) -> None:
    """Copy the fixture tree into the seed clone, minus the verb's own
    ``.canary-family`` marker (metadata, not seed content)."""
    shutil.copytree(
        fixture_dir,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(FAMILY_MARKER),
    )


def _rewrite_callers(dest: str, major: str) -> None:
    """Point every release self-ref in the seed's workflows at the floating
    major (the fixture's literal ref is just a readable default)."""
    workflows = os.path.join(dest, ".github", "workflows")
    rewritten = 0
    for name in sorted(os.listdir(workflows)):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(workflows, name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new_text, count = rewrite_self_refs(text, major)
        if count:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            rewritten += count
    if rewritten == 0:
        raise CanaryError("no `uses: arthur-debert/release/...` caller refs in the fixture")


def _build_seed(
    *, dest: str, fixture_dir: str, family: str, release_root: str, major: str, root: str
) -> None:
    """Construct the canonical seed in the clone: orphan history, fixture
    content, callers at the floating major, pull-model boot from THIS checkout.

    A fresh root commit every time (orphan): --reset is a wedge escape, so the
    seed must sever from whatever history wedged the repo."""
    if gh.git_rev_parse_verify("HEAD", cwd=dest):
        gh.git(["-C", dest, "checkout", "--quiet", "--orphan", "canary-seed"])
        gh.git(["-C", dest, "rm", "-rfq", "."])
    _copy_fixture(fixture_dir, dest)
    _rewrite_callers(dest, major)
    gh.git(["-C", dest, "add", "-A"])
    gh.git(
        [
            "-C",
            dest,
            "commit",
            "--quiet",
            "-m",
            f"{SEED_COMMIT_SUBJECT} ({family}, fixture {os.path.basename(fixture_dir)}@{major})",
        ]
    )

    env = _sandbox_env(root)
    installer = os.path.join(release_root, "templates", "commons", "bin", "install-release-core")
    _run_sandboxed(
        ["bash", installer, "--from-source", release_root, "--no-init"],
        cwd=dest,
        env=env,
        what=f"{family}: sandboxed install-release-core --from-source",
    )
    sandbox_cli = os.path.join(env["XDG_BIN_HOME"], "release-core")
    _run_sandboxed(
        [sandbox_cli, "init"],
        cwd=dest,
        env=env,
        what=f"{family}: release-core init (sandbox, source wheel)",
    )


def _push_seed(dest: str, *, force: bool) -> None:
    args = ["-C", dest, "push", "--quiet"]
    if force:
        args.append("--force")
    args += ["origin", "HEAD:refs/heads/main"]
    gh.git(args)


def _install_token(repo: str) -> int:
    """The existing per-repo-targeted token verb (#601) — validates the PAT on
    stdin, sets RELEASE_TOKEN, verifies the set persisted."""
    return install_release_token.main(["--repos", repo])


def _apply_policy(dest: str) -> int:
    """The existing ruleset verb, run from inside the canary clone (it resolves
    the repo + required checks from cwd; #602 resolves nested contexts)."""
    prev = os.getcwd()
    os.chdir(dest)
    try:
        return apply_ruleset.main([])
    finally:
        os.chdir(prev)


def _converge_secrets(repo: str, *, stdin_is_tty: bool) -> None:
    """RELEASE_TOKEN present (set/rotated from a piped PAT), publish trio absent.

    Fail-closed both ways: a forbidden secret on the repo fails the run until
    deleted, and a missing RELEASE_TOKEN fails it until a PAT is piped — no
    flag silences either."""
    names = gh.secret_list(repo)
    forbidden = forbidden_secrets_present(names)
    if forbidden:
        raise CanaryError(
            f"forbidden publish secret(s) on {repo}: {', '.join(forbidden)} — remove them "
            f"(gh secret delete <name> -R {repo}) and re-run; canaries never publish (#587)"
        )
    if not stdin_is_tty:
        if _install_token(repo) != 0:
            raise CanaryError(f"installing RELEASE_TOKEN on {repo} failed")
        print(f"canary init: RELEASE_TOKEN set on {repo} (from stdin)")
    elif "RELEASE_TOKEN" in names:
        print(f"canary init: RELEASE_TOKEN present on {repo} — kept (pipe a PAT to rotate)")
    else:
        raise CanaryError(
            f"{repo} has no RELEASE_TOKEN — pipe the canonical PAT to stdin "
            "(e.g. pbpaste | release-core admin canary init --family …)"
        )


def _register(manifest: str, family: str, repo: str) -> bool:
    """Ensure the registry entry; True when the manifest was edited."""
    with open(manifest, encoding="utf-8") as fh:
        text = fh.read()
    new_text = register_canary_text(text, family, repo)
    if new_text == text:
        return False
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return True


# ── main ─────────────────────────────────────────────────────────────────────


def _usage_error(msg: str) -> int:
    print(f"release-core admin canary init: {msg}", file=sys.stderr)
    print(_usage_block(), file=sys.stderr)
    return 64


def _workdir_root() -> str:
    """The default hermetic workdir — per-user, same pattern as `canary run`."""
    user = os.environ.get("USER") or "shared"
    return f"/tmp/release-canary-init-{user}"  # noqa: S108 — per-user, matches canary run


def _parse_args(argv: list[str]) -> dict | int:
    opts: dict = {
        "family": None,
        "reset": False,
        "root": None,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(USAGE.strip("\n"))
            return 0
        if arg in ("--family", "--root"):
            if i + 1 >= len(argv):
                return _usage_error(f"{arg} needs a value")
            opts[arg.lstrip("-")] = argv[i + 1]
            i += 2
        elif arg == "--reset":
            opts["reset"] = True
            i += 1
        else:
            return _usage_error(f"unknown arg: {arg}")
    if not opts["family"]:
        return _usage_error("--family is required (e.g. --family rust)")
    if opts["root"] is None:
        opts["root"] = _workdir_root()
    return opts


def main(argv: list[str]) -> int:  # noqa: C901, PLR0912, PLR0915 — linear phase pipeline
    opts = _parse_args(argv)
    if isinstance(opts, int):
        return opts
    family = opts["family"]
    reset = opts["reset"]

    try:
        release_root = gh.repo_root()
        manifest = os.path.join(release_root, "managed-repos.yaml")
        if not os.path.isfile(manifest):
            raise CanaryError(
                f"no managed-repos.yaml at {release_root} — run from inside arthur-debert/release"
            )
        registry = managed_repos.canaries(manifest)
        projects = managed_repos.project_repos(manifest)
        repo = resolve_repo(family, registry, projects, reset)
        fixture_dir = find_fixture(os.path.join(release_root, "tests", "fixtures"), family)

        if not _gh_preflight():
            raise CanaryError("`gh auth status` failed — authenticate first")

        major = _floating_major()
        if not major:
            raise CanaryError("no origin/vN floating-major branch found in this checkout")

        # ── repo: create if missing (PUBLIC, OQ2) ────────────────────────────
        if _repo_exists(repo):
            print(f"canary init: {repo} exists — skip create")
        else:
            _repo_create(
                repo,
                f"Synthetic {family} consumer exercised by release's pre-cut canary "
                "loop (arthur-debert/release#587). Do not depend on this.",
            )
            print(f"canary init: created {repo} (public)")

        dest = os.path.join(opts["root"], f"init-{family}")
        os.makedirs(opts["root"], exist_ok=True)
        _clone(repo, dest)

        # ── seed: empty main, or --reset → force-push the canonical seed ─────
        if _main_seeded(dest) and not reset:
            print(f"canary init: {repo} main is seeded — skip (use --reset to re-seed)")
        else:
            _build_seed(
                dest=dest,
                fixture_dir=fixture_dir,
                family=family,
                release_root=release_root,
                major=major,
                root=opts["root"],
            )
            _push_seed(dest, force=reset)
            print(
                f"canary init: pushed the canonical seed to {repo} main"
                f"{' (forced)' if reset else ''}"
            )
    except (CanaryError, gh.GhError, proc.ProcError, OSError) as exc:
        print(f"canary init: {exc}", file=sys.stderr)
        return 2

    # ── converge: secrets (fail-closed) → policy → registration ──────────────
    try:
        _converge_secrets(repo, stdin_is_tty=sys.stdin.isatty())
        if _apply_policy(dest) != 0:
            raise CanaryError(f"applying the ruleset to {repo} failed")
        print(f"canary init: ruleset applied to {repo}")
        if _register(manifest, family, repo):
            print(f"canary init: registered {family}: {repo} in managed-repos.yaml — commit it")
        else:
            print(f"canary init: {family} already registered in managed-repos.yaml")
    except (CanaryError, gh.GhError, proc.ProcError, OSError) as exc:
        print(f"canary init: {exc}", file=sys.stderr)
        return 1

    print(f"canary init: {family} converged ({repo})")
    return 0


def _floating_major() -> str:
    """The highest origin/vN branch of this checkout (what the seed's thin
    callers pin) — same detection advance-major uses."""
    return _highest_major()
