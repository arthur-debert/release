"""how-to — print the task playbook for THIS repo (Kind-aware).

``release-core how-to`` is the single entry point a fresh agent (or human) runs
to learn how to lint / test / build / release / run *in this specific repo*,
plus the canonical draft-first dev cycle. It is the ONE source of that guidance:
the consumer CLAUDE.md is a 2-line stub pointing here, so there is no synced
ORIENTATION.md or per-Kind doc to drift (release#501, "invoke, don't discover").

The dev-cycle section is the single canonical statement of the draft-first
lifecycle — keep it in lockstep with the `gh-pr-review-loop` skill; this is the
text the stub and the skill both defer to.

Usage:
  release-core how-to            (playbook for the current repo)
  release-core how-to <kind>     (playbook for a named Kind)
"""

from __future__ import annotations

import subprocess

from .. import manifest

USAGE = __doc__ or ""


def _repo_root() -> str:
    """The git work-tree root, so Kind detection works from any subdirectory
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


# Per-Kind verb commands. lint and release are Kind-agnostic (the gate and the
# release-cut path are uniform), so they live in the shared template; only the
# test/build/run/deps commands vary by Kind.
_VERBS: dict[str, dict[str, str]] = {
    "rust-cli": {
        "test": "cargo test",
        "build": "cargo build --release",
        "run": "cargo run -- <args>",
    },
    "go-cli": {
        "test": "go test ./...",
        "build": "go build ./...",
        "run": "go run . <args>",
    },
    "vscode-ext": {
        "deps": "npm install   (node_modules is not committed)",
        "test": "npm test   (needs deps + a prior build)",
        "build": "npm run build",
        "run": "npm run watch, then F5 (Extension Development Host)",
    },
    "electron-app": {
        "deps": "npm install",
        "test": "npm test   (E2E is the primary suite for a GUI app)",
        "build": "npm run build",
        "run": "npm start",
    },
    "zed-extension": {
        "test": "cargo test",
        "build": "cargo build --release",
        "run": "install as a dev extension in Zed",
    },
    "tauri-app": {
        "deps": "npm install",
        "test": "npm test   (E2E is the primary suite for a GUI app)",
        "build": "npm run tauri build",
        "run": "npm run tauri dev",
    },
    "nvim-plugin": {
        "test": "make test   (busted/plenary)",
        "build": "(none — interpreted)",
        "run": "open Neovim with the plugin on the runtimepath",
    },
    # Keys must stay aligned to what manifest.detect_kind can actually produce —
    # an entry for an undetectable Kind only renders via an explicit `how-to
    # <kind>` arg and misleads about auto-detection (e.g. python-pkg, removed).
}

# Shown when a Kind has no specific entry — the verbs an agent should discover.
_GENERIC = {
    "test": "(see this repo's build files / CI)",
    "build": "(see this repo's build files / CI)",
    "run": "(see this repo's README)",
}


def _render(kind: str) -> str:
    verbs = _VERBS.get(kind, _GENERIC)
    lines: list[str] = []
    lines.append(f"release-core how-to — this repo (Kind: {kind})")
    lines.append("")
    lines.append(
        "Infrastructure (quality gate, build, release, PR flow) is managed by "
        "release-core. Don't hand-edit managed files (.release/**); app code is "
        "yours. Confirm the Kind anytime with `release-core detect-kind`."
    )
    lines.append("")
    lines.append("The five verbs")
    lines.append("  lint / quality gate : release-core gate")
    lines.append(
        "      The fast lint/format/static gate (the same lefthook gate CI runs). "
        "Hard gate: a missing tool is a setup failure, never a skip; --no-verify "
        "is never OK. Runs over the whole tree, so it won't false-green on an "
        "unstaged change. NOTE: it does NOT run tests (see `test` below) — a green "
        "gate is necessary but not sufficient for CI green; run tests too."
    )
    if "deps" in verbs:
        lines.append(f"  deps (first run)    : {verbs['deps']}")
    lines.append(
        f"  test                : {verbs['test']}"
        "   (run before pushing; CI runs it as a separate check)"
    )
    lines.append(f"  build               : {verbs['build']}")
    lines.append(
        "  release             : release-core cut <major|minor|patch>"
        "   (CI builds/signs/publishes — never release locally)"
    )
    lines.append(f"  run                 : {verbs['run']}")
    lines.append("")
    lines.append("The dev cycle (the ONE flow — draft-first)")
    lines.append("  1. Branch off main.")
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
        "  6. Drive the review loop via the `gh-pr-review-loop` skill (it arms "
        "the PR-loop guard, requests Copilot, waits, triages, resolves threads). "
        "A bare `gh pr create` may be blocked by the guard until the skill arms "
        "the loop — that's expected."
    )
    lines.append(
        "  7. Flip to READY (gh pr ready) only when reviewed + CI green + "
        "mergeable. That hands it to a human; don't auto-merge. "
        "(gh pr ready --undo flips back.)"
    )
    lines.append("")
    lines.append("When infra itself is broken (the gate/build/release tooling)")
    lines.append(
        "  Don't patch it here — escalate: `release-core issue file`. The fix "
        "belongs in arthur-debert/release and arrives on the next session."
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE.strip())
        return 0

    if argv:
        kind = argv[0]
    else:
        try:
            kind = manifest.detect_kind(_repo_root())
        except manifest.KindError:
            # Intentional: how-to is an ORIENTATION command. When the Kind can't
            # be detected we still render the generic playbook — the dev cycle +
            # `release-core gate` guidance is Kind-agnostic and the most valuable
            # part, so generic help beats a hard error for a fresh agent.
            kind = "unknown"
    print(_render(kind))
    return 0
