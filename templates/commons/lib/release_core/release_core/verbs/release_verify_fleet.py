"""release-verify-fleet — pre-flight lint sweep across the whole portfolio.

Verifies that a candidate release revision, once synced into every
managed consumer, still passes the shared lint gate. Run this BEFORE
`release-advance-v1` to catch a commons/lint regression in release's own
tree instead of discovering it one consumer at a time after @v1 moves.

It is HERMETIC: it clones the fleet into a throwaway root (NOT your ~/h
checkouts), syncs each from the candidate ref, and runs the shared gate via
`release-core gate` (which points lefthook at the built
`.release/lefthook.yml` — there is no tracked root lefthook.yml since WS3
release#524). It never mutates your working repos. Every sweep fetches+resets
existing fleet clones to the consumer's default branch (resolved from
`origin/HEAD` — usually `main`, but `master` / a slashed name like
`release/v1` are honored too) UNCONDITIONALLY (#624) — a stale clone's
consumer-authored half makes the pre-flight look faithful while it lies — and
names the ref/sha each clone now sits at. The reset is a hard reset, so a
HEALTHY clone with uncommitted work is skipped-with-warning rather than
discarded (the data-loss guard); the throwaway /tmp clones are always clean,
so they always refresh. A poisoned clone left by a crashed run (corrupt .git,
half-finished checkout, wrong remote) is self-healing (#748): it is removed and
re-cloned rather than protected, so a reused fixed root can no longer rot into
"0 pass, N unexpected" until a manual `rm -rf`.

This is the "checkout all repos, release-sync them, try to commit" idea:
real consumer files (the genuine edge cases), zero synthetic fixtures.

PROVISION, then gate (release#772 follow-up): release IS the provisioning
authority, so the sweep INSTALLS each consumer's project deps
(`provision.dep_caches` — npm/pnpm/yarn/cargo/go/bundle) BEFORE running the gate,
exactly as a real boot does. It does NOT run the gate on a dep-less tree and
then blame the consumer. The ONLY honest non-result is a SKIP: when a NEEDED
toolchain is genuinely absent in THIS environment (no `npm` to install node
deps), the repo is "skipped (no <tool> to provision)" — never a false red, never
a laundered "artifact". Once provisioned, a gate FAIL is a REAL failure.

A repo whose gate needs an environmental cause the sweep still can't satisfy (a
sibling REPO checkout, not deps) carries an `expect-verify-fail: <reason>`
annotation in managed-repos.yaml; an annotated repo that PASSES is flagged STALE
(shrink-only ratchet — remove the annotation). The operator only sees
deviations: logs and a non-zero exit are reserved for UNEXPECTED failures.

Usage:
  release-verify-fleet [--ref <ref>] [--root <dir>] [--only <owner/name,...>]

  --ref <ref>     release revision to sync FROM (default: HEAD of this
                  release checkout). Tests the code you have right here.
  --root <dir>    where to clone the fleet (default:
                  /tmp/release-fleet-verify-$USER, per-user to avoid
                  permission clashes on shared machines).
  --only <list>   restrict to a comma-separated subset of owner/name.

Exit codes:
  0  — no unexpected failures (annotated-expected FAILs + un-provisionable SKIPs
       are classified, not red)
  1  — at least one UNEXPECTED failure (missing clone, sync FAIL, or a gate FAIL
       on a PROVISIONED tree that carries no expect-verify-fail annotation)
  2  — setup/dependency error
  64 — bad usage
"""

from __future__ import annotations

import os
import shutil
import sys

from .. import gh, proc, provision
from . import managed_repos

# Re-invoke THIS package's CLI in a subprocess: same interpreter, same env, same
# code as the running process — never a bare-name PATH lookup, which on a dev
# box resolves to the in-checkout launcher and dies on nested dep re-resolution
# (release#534: `admin repos verify` failed in phase 1 before any clone/gate).
_SELF_CLI = [sys.executable, "-m", "release_core"]

# The --help body mirrors the bash `show_help() { sed -n '2,/^$/p' "$0" | sed -E
# 's/^# ?//'; }`: lines 2..first-blank of the header comment. That range is the
# docstring's first paragraph (down to the blank line before "Usage:"); since
# this docstring carries no migration note, the help text is the full docstring
# — the same content the bash printed from its header comment.
USAGE = __doc__ or ""


def _usage_block() -> str:
    """The help body, byte-for-byte the bash `show_help` output.

    The bash `show_help() { sed -n '2,/^$/p' "$0" | sed -E 's/^# ?//'; }` prints
    the header comment from line 2 through the first truly-blank line INCLUSIVE,
    so its output carries ONE trailing blank line. We reproduce that: the
    docstring body + a single trailing newline (the empty terminating line)."""
    return USAGE.strip("\n") + "\n"


def _usage_error(msg: str) -> int:
    print(msg, file=sys.stderr)
    print(_usage_block(), file=sys.stderr)
    return 64


def _release_home() -> str:
    """RELEASE_HOME = <launcher bin/>/.. — the script_dir/.. the bash computed.

    The launcher exports VERIFY_FLEET_SCRIPT_DIR (its own realpath'd bin/ dir) so
    this resolves identically regardless of cwd. Falls back to ~/release."""
    script_dir = os.environ.get("VERIFY_FLEET_SCRIPT_DIR")
    if script_dir:
        return os.path.realpath(os.path.join(script_dir, ".."))
    return os.path.join(os.path.expanduser("~"), "release")


def main(argv: list[str]) -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915 — faithful port of a long linear bash
    # --- Arg parse (mirror the bash while/case; bad usage → 64) ----------
    ref = "HEAD"
    user = os.environ.get("USER") or "shared"
    root = f"/tmp/release-fleet-verify-{user}"  # noqa: S108 — matches the bash default path
    only = ""

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--ref":
            if i + 1 >= len(argv):
                return _usage_error("release-verify-fleet: --ref needs a value")
            i += 1
            ref = argv[i]
        elif arg == "--root":
            if i + 1 >= len(argv):
                return _usage_error("release-verify-fleet: --root needs a value")
            i += 1
            root = argv[i]
        elif arg == "--only":
            if i + 1 >= len(argv):
                return _usage_error("release-verify-fleet: --only needs a value")
            i += 1
            only = argv[i]
        elif arg in ("-h", "--help"):
            print(_usage_block())
            return 0
        else:
            return _usage_error(f"release-verify-fleet: unknown arg: {arg}")
        i += 1

    release_home = _release_home()
    os.environ["RELEASE_HOME"] = release_home

    # --- Dependency guard ------------------------------------------------
    # NO "release-core" here: the nested CLI calls go through _SELF_CLI (this
    # interpreter, -m release_core), never a PATH lookup (release#534).
    for tool in ("detect-kind", "lefthook", "yq", "gh", "git"):
        if shutil.which(tool) is None:
            print(f"release-verify-fleet: {tool} not on PATH", file=sys.stderr)
            return 2

    # Resolve the candidate ref to a concrete SHA up front so the report is
    # unambiguous and every consumer syncs from the identical revision.
    if not gh.git_rev_parse_verify(ref, cwd=release_home):
        print(
            f"release-verify-fleet: bad --ref '{ref}' in {release_home}",
            file=sys.stderr,
        )
        return 2
    ref_sha = gh.git_rev_parse(ref, cwd=release_home)
    print(
        f"release-verify-fleet: syncing fleet from release@{ref_sha[:12]} ({ref}) into {root}",
        file=sys.stderr,
    )

    # An --only subset (comma-separated) becomes positional filter args shared
    # by both `release-core admin repos list` calls, so the clone is scoped too.
    subset = [s for s in only.split(",") if s] if only else []

    # The fleet accessor is now the hierarchical CLI verb (the flat
    # `managed-repos` console-script was retired in the B2 cutover; #468). Argv
    # passes through `release-core admin repos list` straight to the verb —
    # via _SELF_CLI, so the nested call runs THIS process's interpreter+code.
    repos_list = [*_SELF_CLI, "admin", "repos", "list"]

    # --- Phase 1: set up the fleet (hermetic — into $root, never ~/h) ---
    # `--clone` ALWAYS fetches+resets existing clones to the consumer's default
    # branch (resolved from origin/HEAD) and names the ref/sha (#624) — a
    # faithful pre-flight never lints a frozen-at-clone-time working tree. The
    # old `--refresh` opt-in was removed.
    print("==> cloning/refreshing fleet", file=sys.stderr)
    clone = proc.run(
        [*repos_list, "--clone", *subset],
        env={"REPOS_ROOT": root},
        check=False,
        capture_output=False,
    )
    if clone.returncode != 0:
        print(
            "release-verify-fleet: fleet clone reported failures (continuing with what cloned)",
            file=sys.stderr,
        )

    # The expect-verify-fail annotations (#594) — read in-process from the same
    # manifest the accessor subprocess resolves (identical env precedence). A
    # failed read (missing/unparseable manifest, yq gone mid-run) is a setup
    # error like every other phase: clean message, exit 2 — never a traceback,
    # and never a silent {} (an annotation set that vanished would resurface
    # the annotated repos as false unexpected reds).
    try:
        annotations = managed_repos.expect_verify_fail()
    except Exception as exc:
        print(
            f"release-verify-fleet: cannot read the fleet manifest annotations: {exc}",
            file=sys.stderr,
        )
        return 2

    # --- Phase 2: per-consumer sync + gate -------------------------------
    print("repo\tkind\tsync\tgate")
    n_pass = 0
    n_expected = 0
    n_skipped = 0
    unexpected = 0
    stale: list[str] = []
    seen = 0

    paths = proc.run(
        [*repos_list, "--paths", *subset],
        env={"REPOS_ROOT": root},
    )
    for line in paths.stdout.splitlines():
        if not line:
            continue
        # Mirror `IFS=$'\t' read -r repo abspath found`: split into exactly three
        # fields, the last absorbing any further tabs (a tab in abspath is absurd
        # but read tolerates it, so the faithful port must too — and maxsplit
        # avoids a ValueError unpack on a >3-field line).
        repo, abspath, found = line.split("\t", 2)
        seen += 1

        if found != "found":
            print(f"{repo}\t-\tmissing\tskipped")
            unexpected += 1
            continue

        kind = _detect_kind(abspath)

        if not _run_sync(abspath, ref_sha, release_home):
            # A sync (init) failure is release plumbing — ALWAYS unexpected;
            # the annotations only cover the gate phase.
            print(f"{repo}\t{kind}\tFAILED\tskipped")
            unexpected += 1
            continue

        # PROVISION the consumer's deps BEFORE gating: release IS the provisioning
        # authority, so the pre-flight installs what the gate needs (npm/pnpm/cargo/
        # …) instead of running the gate on a dep-less tree and blaming the consumer
        # (release#772 follow-up). Only when a NEEDED toolchain is genuinely absent
        # in this environment do we SKIP-with-reason — never a false red, never a
        # laundered "artifact".
        unprovisionable = provision.unprovisionable_stacks(abspath)
        if unprovisionable:
            print(f"{repo}\t{kind}\tok\tskipped (no {', '.join(unprovisionable)} to provision)")
            n_skipped += 1
            continue
        _provision_deps(abspath)

        gate_ok, gate_log = _run_gate(abspath)
        gate, kind_of_result = _classify_gate(gate_ok, gate_log, annotations.get(repo))
        if kind_of_result == "pass":
            n_pass += 1
        elif kind_of_result == "stale":
            n_pass += 1
            stale.append(repo)
        elif kind_of_result == "expected":
            n_expected += 1
        else:
            unexpected += 1
        print(f"{repo}\t{kind}\tok\t{gate}")

    print(file=sys.stderr)
    print(
        f"release-verify-fleet: {seen} consumer(s) swept from release@{ref_sha[:12]}: "
        f"{n_pass} pass, {n_expected} annotated-expected FAILs, "
        f"{n_skipped} skipped (un-provisionable here), {unexpected} unexpected.",
        file=sys.stderr,
    )
    if stale:
        print(
            "release-verify-fleet: STALE annotation(s) — the gate passes now; remove "
            f"expect-verify-fail from managed-repos.yaml: {', '.join(stale)}",
            file=sys.stderr,
        )
    if unexpected:
        print(
            "release-verify-fleet: UNEXPECTED failures above. Logs under "
            f"{root}: <repo>/.verify-sync.log (sync), <repo>/.verify-provision.log "
            "(deps), and <repo>/.verify-gate.log (gate).",
            file=sys.stderr,
        )
    else:
        print(
            "release-verify-fleet: no unexpected failures against this revision.",
            file=sys.stderr,
        )
    return 1 if unexpected else 0


def _provision_deps(abspath: str) -> None:
    """Install the consumer's project deps (``provision.dep_caches``) with
    stdout+stderr redirected to a per-repo ``.verify-provision.log`` — the package
    managers inherit the fds, so without this their install output would interleave
    with the tabular report (and leave no log behind). At the OS-fd level so it
    captures the subprocess output, not just Python prints."""
    if not os.path.isdir(abspath):
        provision.dep_caches(abspath)  # no log dir (tests / odd path) → run plainly
        return
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.path.join(abspath, ".verify-provision.log"), "w") as logf:
        out_fd, err_fd = os.dup(1), os.dup(2)
        os.dup2(logf.fileno(), 1)
        os.dup2(logf.fileno(), 2)
        try:
            provision.dep_caches(abspath)
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(out_fd, 1)
            os.dup2(err_fd, 2)
            os.close(out_fd)
            os.close(err_fd)


def _classify_gate(gate_ok: bool, _gate_log: str, reason: str | None) -> tuple[str, str]:
    """The gate column value + result class for one consumer (#594).

    Returns ``(column_text, kind)`` with kind ∈ {pass, stale, expected,
    unexpected}. The caller has already PROVISIONED the deps (or skipped an
    un-provisionable repo before gating), so the only remaining excuse for a FAIL
    is an ``expect-verify-fail`` annotation (a sibling-repo checkout the sweep
    can't fetch). Any other FAIL — on a provisioned tree, unannotated — is REAL
    and unexpected. A passing repo that carries an annotation is STALE."""
    if gate_ok:
        if reason is not None:
            return "pass (STALE expect-verify-fail annotation)", "stale"
        return "pass", "pass"
    if reason is not None:
        return f"expected-FAIL (annotated: {reason})", "expected"
    # No npm-deps "artifact" excuse: the caller PROVISIONED the deps before gating
    # (or honestly SKIPPED when the toolchain was absent), so a non-annotated FAIL
    # here is a REAL failure — fail toward visibility.
    return "FAIL", "unexpected"


def _detect_kind(abspath: str) -> str:
    """`(cd "$abspath" && detect-kind 2>/dev/null || echo "?")` — the kind, or '?'."""
    result = proc.run(["detect-kind"], cwd=abspath, check=False)
    out = result.stdout.strip()
    if result.returncode != 0 or not out:
        return "?"
    return out


def _run_sync(abspath: str, ref_sha: str, release_home: str) -> bool:
    """Install the managed files into the consumer clone, env-pinned to the
    candidate SHA. Runs ``release-core init --no-commit`` (the standalone
    ``release-sync`` verb was retired in WS4, release#521); init's full install
    honors the same RELEASE_HOME/RELEASE_REF pinning. --no-commit: the verify
    sweep only lints the installed files, it never mutates the clone's git state.

    Combined stdout+stderr is written to <abspath>/.verify-sync.log. Returns True
    on a zero exit."""
    result = proc.run(
        [*_SELF_CLI, "init", "--no-commit"],
        cwd=abspath,
        env={"RELEASE_REF": ref_sha, "RELEASE_HOME": release_home},
        check=False,
    )
    _write_log(os.path.join(abspath, ".verify-sync.log"), result)
    return result.returncode == 0


def _run_gate(abspath: str) -> tuple[bool, str]:
    """Run the shared lint gate in the consumer clone.

    Invokes ``release-core gate`` (NOT a bare ``lefthook run``): since WS3
    (release#524) the consumer no longer tracks a root lefthook.yml for lefthook
    to discover — the gate definition lives in the just-installed
    ``.release/lefthook.yml``, and ``release-core gate`` is what points lefthook at
    it (LEFTHOOK_CONFIG) the same way the local hook + CI do. Spawned via
    ``_SELF_CLI`` (this interpreter, ``-m release_core``), never a PATH lookup
    (release#534).

    Combined stdout+stderr is written to <abspath>/.verify-gate.log. Returns
    ``(passed, combined_log)`` — the log feeds the expected-artifact
    classification (#594) without re-reading the file."""
    result = proc.run(
        [*_SELF_CLI, "gate"],
        cwd=abspath,
        check=False,
    )
    _write_log(os.path.join(abspath, ".verify-gate.log"), result)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def _write_log(path: str, result) -> None:
    """Mirror the bash `>LOG 2>&1`: stdout then stderr, combined, into LOG."""
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(result.stdout or "")
        fh.write(result.stderr or "")
