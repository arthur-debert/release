"""how-to — print the task playbook for THIS repo (component-aware).

``release-core how-to`` is the single entry point a fresh agent (or human) runs
to learn how to lint / test / build / release / run *in this specific repo*,
plus the draft-first dev cycle. It is the ONE source of that guidance:
the consumer CLAUDE.md is a small managed block (~7 lines) pointing here, so there
is no synced ORIENTATION.md or per-Kind doc to fall out of sync (release#501 —
discovery is the CLI, not docs).

It does NOT guess commands by Kind. The no-arg path reads THIS repo's real
commands via :mod:`release_core.repo_commands` (the component detector) and lists
the uniform ``release-core test-unit|test-e2e|test-all|coverage|build|run`` verbs
annotated with what each resolves to — so it can't assert a command the repo
doesn't have (release#507). The ``how-to <kind>`` arg path is the abstract
"explain this Kind" documentation mode (no specific repo to read).

The dev-cycle section is the runtime-rendered statement of the draft-first
lifecycle — the §1 single-PR cycle (kept in lockstep with the `gh-pr-review-loop`
skill) plus the §2 coordinator discipline for a complex / multi-PR feature
(condensed from `dev-cycle.lex` §2). `dev-cycle.lex` is the model; this renders
the operational form, and the CLAUDE.md stub points here. Keep all three in
lockstep — there is deliberately NO second distributed skill for §2; coordination
is taught here, on the reliable orient-time channel, not via a spotty skill
trigger.

Usage:
  release-core how-to            (playbook for the current repo — real commands)
  release-core how-to <kind>     (abstract playbook for a named Kind)
"""

from __future__ import annotations

import subprocess

from .. import manifest, repo_commands
from ..repo_commands import Cmd

USAGE = __doc__ or ""


def _repo_root() -> str:
    """The git work-tree root, so detection works from any subdirectory
    (mirrors gate._repo_root)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "."


# Abstract per-Kind-family hints — used ONLY by the `how-to <kind>` documentation
# path, where there is no specific repo to read. These describe where the real
# command lives, never asserting a literal command about a real repo (that is the
# no-arg path's job, which reads the manifest). "→" text is intentionally generic.
_KIND_HINT: dict[str, dict[str, str]] = {
    "rust-cli": {
        "test": "cargo test",
        "coverage": "cargo llvm-cov",
        "build": "cargo build --release",
        "run": "cargo run -- <args>",
    },
    "zed-extension": {
        "test": "cargo test",
        "coverage": "cargo llvm-cov",
        "build": "cargo build --release",
        "run": "(install as a dev extension)",
    },
    "go-cli": {
        "test": "go test ./...",
        "coverage": "go test -coverprofile + go tool cover -func",
        "build": "go build ./...",
        "run": "go run . <args>",
    },
    "vscode-ext": {
        "deps": "npm install",
        "test": "your package.json test* scripts",
        "coverage": "your test script with --coverage",
        "build": "your package.json build script",
        "run": "watch script, then F5",
    },
    "electron-app": {
        "deps": "npm install",
        "test": "your package.json test:unit / test:e2e scripts",
        "coverage": "your test script with --coverage",
        "build": "your package.json build script",
        "run": "dev/start script",
    },
    "tauri-app": {
        "deps": "<pm> install",
        "test": "frontend test* scripts + cargo test (src-tauri/)",
        "coverage": "frontend --coverage + cargo llvm-cov (src-tauri/)",
        "build": "<pm> tauri build",
        "run": "dev / tauri dev script",
    },
    "nvim-plugin": {
        "test": "app-bin/test-all (or busted tests/)",
        "coverage": "(no coverage tool for this Kind)",
        "build": "(none — interpreted)",
        "run": "(open Neovim with the plugin on the runtimepath)",
    },
}

_GENERIC_HINT = {
    "test": "(see this repo's manifest / CI)",
    "coverage": "(see this repo's manifest / CI)",
    "build": "(see this repo's manifest / CI)",
    "run": "(see this repo's README)",
}


def _resolved(cmds: list[Cmd]) -> str:
    """Join a fan-out's resolved commands for display, or a not-wired note."""
    if not cmds:
        return "(none wired in this repo)"
    return "; ".join(c.display for c in cmds)


def _verbs_section_repo(root: str) -> list[str]:
    """The verbs section for the CURRENT repo — uniform verbs annotated with the
    repo's REAL resolved commands (read from its manifests)."""
    rc = repo_commands.resolve(root)
    lines: list[str] = []
    lines.append("The task verbs (release-core runs THIS repo's real command)")
    lines.append("  lint / quality gate : release-core gate")
    lines.append(_GATE_BLURB)
    if rc.deps:
        lines.append(f"  deps (first run)    : {rc.deps}")
    lines.append(f"  test (unit)         : release-core test-unit   → {_resolved(rc.unit)}")
    e2e = _resolved(rc.e2e) if rc.e2e else "(no e2e suite wired)"
    lines.append(f"  test (e2e)          : release-core test-e2e    → {e2e}")
    lines.append("  test (unit + e2e)   : release-core test-all")
    cov = _resolved(rc.coverage) if rc.coverage else "(no coverage tool for this repo)"
    lines.append(f"  coverage            : release-core coverage    → {cov}")
    build = rc.build.display if rc.build else "(no build command detected)"
    lines.append(f"  build               : release-core build       → {build}")
    lines.append(
        "  release             : release-core cut <major|minor|patch>"
        "   (CI builds/signs/publishes — never release locally)"
    )
    run = rc.run.display if rc.run else "(no run command detected)"
    lines.append(f"  run                 : release-core run         → {run}")
    if rc.docs:
        lines.append(f"  docs (mkdocs)       : {rc.docs.build.display}")
    return lines


def _verbs_section_kind(kind: str) -> list[str]:
    """The verbs section for an abstract Kind (the `how-to <kind>` doc path).
    No repo to read, so describe where the real command lives."""
    hint = _KIND_HINT.get(kind, _GENERIC_HINT)
    lines: list[str] = []
    lines.append(
        "The task verbs (abstract — run `release-core how-to` IN the repo for real commands)"
    )
    lines.append("  lint / quality gate : release-core gate")
    lines.append(_GATE_BLURB)
    if "deps" in hint:
        lines.append(f"  deps (first run)    : {hint['deps']}")
    lines.append(f"  test (unit)         : release-core test-unit   → {hint['test']}")
    lines.append("  test (e2e)          : release-core test-e2e")
    lines.append("  test (unit + e2e)   : release-core test-all")
    cov = hint.get("coverage", _GENERIC_HINT["coverage"])
    lines.append(f"  coverage            : release-core coverage    → {cov}")
    lines.append(f"  build               : release-core build       → {hint['build']}")
    lines.append(
        "  release             : release-core cut <major|minor|patch>"
        "   (CI builds/signs/publishes — never release locally)"
    )
    lines.append(f"  run                 : release-core run         → {hint['run']}")
    return lines


_GATE_BLURB = (
    "      The fast lint/format/static gate (the same lefthook gate CI runs). "
    "Hard gate: a missing tool is a setup failure, never a skip; --no-verify "
    "is never OK. Runs over the whole tree, so it won't false-green on an "
    "unstaged change. NOTE: it does NOT run tests (see `test` below) — a green "
    "gate is necessary but not sufficient for CI green; CI runs the test suite "
    "as a separate check, so run `test-unit` (and `test-e2e`) yourself before "
    "pushing."
)


def _ci_jobs_section() -> list[str]:
    """Consumer-authored CI jobs vs the ephemeral managed tree (release#581).

    Kind-agnostic, so it renders on both the repo path and the abstract path —
    the trap is the same everywhere: managed paths do not exist on a fresh CI
    checkout."""
    lines: list[str] = []
    lines.append("Writing your own CI job that uses a managed tool?")
    lines.append(
        "  Managed paths (bin/check*, lib/release_core/, .release/**) are "
        "EPHEMERAL — recomposed by `release-core init`, untracked, so they DO "
        "NOT exist on a fresh CI checkout. Any job step that invokes one must "
        "build the managed tree first:"
    )
    lines.append("    - uses: arthur-debert/release/.github/actions/arm-gate@v2")
    lines.append("      with:")
    lines.append("        toolset: 'false'   # build-only (skips the lint toolset)")
    lines.append(
        "  Drop the `toolset` input (default 'true') when the job also runs the "
        "gate. Never hand-copy the build recipe — invoke the composite."
    )
    return lines


def _dev_cycle_section() -> list[str]:
    lines: list[str] = []
    lines.append("The dev cycle (the ONE flow — draft-first)")
    lines.append(
        "  1. Branch off origin/<default-branch> (`git fetch origin && "
        "git switch -c <branch> origin/<default-branch>`), NOT the local "
        "default branch: the SessionStart boot may have auto-committed a "
        "managed sync there, and branching from it carries that alien "
        "commit into your PR diff."
    )
    lines.append("  2. Make the change.")
    lines.append(
        "  3. Add a changelog fragment (required, same PR): "
        'release-core changelog add <slug> "<one-line summary>"'
    )
    lines.append(
        "       <slug> is kebab-case; it writes CHANGELOG/unreleased-<slug>.md. "
        "A release refuses to cut without one. Never hand-edit CHANGELOG.md."
    )
    lines.append("  4. Run `release-core gate` until green.")
    lines.append("  5. Open the PR as a DRAFT: gh pr create --draft")
    lines.append(
        "  6. Drive the review loop via the `gh-pr-review-loop` skill, off the "
        "state machine: `release-core pr status` reports one lifecycle state "
        "plus the single next action — do it (request the required reviews "
        "via `release-core pr review request` — reviewer-agnostic, human or "
        "bot alike — triage threads, fix CI), then re-read. Wait by blocking "
        "in-turn on `release-core pr wait` (never a detached background "
        "wait). The skill arms the PR-loop guard; a bare `gh pr create` may "
        "be blocked until it does — that's expected."
    )
    lines.append(
        "  7. Flip to READY with `release-core pr ready` — the guarded flip; "
        "it refuses unless the engine says READY (never raw `gh pr ready`). "
        "That hands it to a human; don't auto-merge. "
        "(`release-core pr ready --undo` flips back for re-work.)"
    )
    lines.append("")
    lines.append("When infra itself is broken (the gate/build/release tooling)")
    lines.append(
        "  Don't patch it here — escalate: `release-core issue file`. The fix "
        "belongs in arthur-debert/release and arrives on the next session."
    )
    return lines


def _complex_cycle_section() -> list[str]:
    """The §2 coordinator discipline — a larger feature is a composition of the
    single-PR cycle under ONE coordinating agent. Condensed operational form of
    `dev-cycle.lex` §2; keep the two in lockstep."""
    lines: list[str] = []
    lines.append("When the task spans multiple PRs (coordinating an epic)")
    lines.append(
        "  A larger feature is a composition of the single-PR cycle above under "
        "ONE coordinating agent. The coordinator does NOT implement — it "
        "delegates, integrates, and keeps the whole on track (implementing "
        "itself burns its context and forces compaction)."
    )
    lines.append("  1. Cut ONE epic branch; the feature lands via an umbrella PR at the end.")
    lines.append(
        "  2. Delegate one IMPLEMENTER subagent per workstream — ideally each "
        "scoped by its own issue. The implementer STOPS AT PR-OPEN: implement → "
        "gate → draft PR targeting the EPIC branch, with a `## Context` note in "
        "the PR body carrying the non-obvious reasoning (why this approach, "
        "what's out of scope, what NOT to 'fix') written for a stranger — then "
        "it reports back and terminates. It never shepherds its own review "
        "rounds: an author-shepherd drags the whole implementation context "
        "through every round (expensive — single agents have hit ~700k tokens) "
        "and judges review comments by defending its past choices instead of "
        "on the diff's merits."
    )
    lines.append(
        "  3. The coordinator owns every wait and every flip: block in-turn on "
        "`release-core pr wait` (a subagent that yields to wait terminates and "
        "is never re-woken). When the wait returns ADDRESSING, spawn a FRESH "
        "shepherd subagent — brief: just the PR number + the Context note — to "
        "triage the threads, fix or reply, resolve, push, re-request the "
        "review, then hand the wait back and terminate. One fresh shepherd PER "
        "round; at READY the coordinator runs the guarded `release-core pr "
        "ready`."
    )
    lines.append(
        "  4. Integrate: the coordinator drives the go/no-go merge of each "
        "workstream PR into the epic branch — no user approval for these "
        "intra-epic merges (the user's gate is the umbrella PR, step 7)."
    )
    lines.append(
        "  5. Converge: once the planned workstreams land, open ONE final "
        "workstream for the fallouts — follow-up issues filed during the build "
        "plus things that surfaced mid-implementation. In scope only; the epic "
        "must not merge with a pile of decoupled follow-ups trailing behind it."
    )
    lines.append(
        "  6. Docs pass: delegate a final PR that updates the docs and docstrings "
        "the feature changed (dev/user/reference + module-level design notes)."
    )
    lines.append(
        "  7. Umbrella PR: verify which issues it actually closes, write the "
        "epic-level summary linking the related issues, drive it through the "
        "same split (coordinator waits + flips; fresh shepherd per round), and "
        "flip to READY for the user's final merge."
    )
    lines.append(
        "  8. Release: after the user merges, cut the release — MINOR or PATCH "
        "per what the epic realized (only the user requests a MAJOR). Cascade "
        "dependent repos in dependency order if the project has chained deps."
    )
    return lines


def _render_repo(root: str, kind: str) -> str:
    """Playbook for the current repo, with real resolved commands."""
    lines: list[str] = []
    lines.append(f"release-core how-to — this repo (Kind: {kind})")
    lines.append("")
    lines.append(
        "Infrastructure (quality gate, build, release, PR flow) is managed by "
        "release-core. Don't hand-edit managed files (.release/**); app code is "
        "yours. Confirm the Kind anytime with `release-core detect-kind`."
    )
    lines.append("")
    lines.extend(_verbs_section_repo(root))
    lines.append("")
    lines.extend(_ci_jobs_section())
    lines.append("")
    lines.extend(_dev_cycle_section())
    lines.append("")
    lines.extend(_complex_cycle_section())
    return "\n".join(lines)


def _render(kind: str) -> str:
    """Abstract playbook for a named Kind (no repo read)."""
    lines: list[str] = []
    lines.append(f"release-core how-to — Kind: {kind}")
    lines.append("")
    lines.append(
        "Infrastructure (quality gate, build, release, PR flow) is managed by "
        "release-core. Don't hand-edit managed files (.release/**); app code is "
        "yours. Confirm the Kind anytime with `release-core detect-kind`."
    )
    lines.append("")
    lines.extend(_verbs_section_kind(kind))
    lines.append("")
    lines.extend(_ci_jobs_section())
    lines.append("")
    lines.extend(_dev_cycle_section())
    lines.append("")
    lines.extend(_complex_cycle_section())
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    if argv:
        # Abstract "explain this Kind" mode — no specific repo to read.
        print(_render(argv[0]))
        return 0

    root = _repo_root()
    try:
        kind = manifest.detect_kind(root)
    except manifest.KindError:
        # Intentional: how-to is an ORIENTATION command. When the Kind can't be
        # detected we still render the playbook reading whatever manifests the
        # repo does have (real commands), plus the Kind-agnostic dev cycle —
        # generic help beats a hard error for a fresh agent.
        kind = "unknown"
    print(_render_repo(root, kind))
    return 0
