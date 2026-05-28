Release Repo: How It Works

This was an agent-generated walkthrough of the codebase. While informative, the README.lex and docs/status.lex are the authoritative sources. CONTEXT.md is the authoritative vocabulary.

This document uses old terminology throughout: "Stack" (now Kind), "Component" (now Capability). It has not been updated to reflect the 2026-05-27 vocabulary reset. Read it for structural understanding but trust CONTEXT.md for terms.

What it is

  release/ is a single-developer infrastructure repo that standardizes how ~22 projects (across two GitHub orgs: arthur-debert/* and lex-fmt/*) are built, tested, and released. The projects span Rust CLIs,
  Rust libraries, Electron apps, Tauri apps, VS Code extensions, Neovim plugins, tree-sitter grammars, Zed extensions, Go CLIs, Python packages, and GitHub Actions.

  The core idea: one canonical implementation per concern, fix once, propagate everywhere. Before this existed, each project had its own slightly different hand-rolled CI/release setup. A bug fix meant
  grepping 20 repos and opening dozens of PRs.

  Key vocabulary

  - Stack — the language/runtime profile of a consumer repo (e.g. rust-cli, electron-app, tauri-app, vscode-ext). Detected automatically by bin/detect-stack based on filesystem markers (Cargo.toml,
  package.json contents, grammar.js, etc.).
  - Component — a reusable capability module orthogonal to Stack (e.g. shell-quality, rust-quality, npm-quality, bats, mkdocs). Any Stack composes any Components.
  - Task — an atomic verb exposed as bin/<verb> in the consumer: bin/check, bin/check-fmt, bin/check-lint, bin/check-tests, bin/build, bin/release. Same interface regardless of Stack; behavior varies.
  - Flow — a user-level goal implemented as a GitHub Actions workflow (ci.yml, release.yml, docs.yml). Each is a thin caller of a reusable workflow in this repo.

  Directory structure

  .github/
    workflows/         29 reusable workflows — one release + one CI per Stack
                       (rust-cli.yml, rust-ci.yml, electron-app.yml, electron-ci.yml,
                        tauri-app.yml, tauri-ci.yml, go-cli.yml, go-ci.yml, etc.)
                       Plus: copilot-review.yml, cascade-handler.yml, etc.
    actions/           19 composite actions — atomic units shared across workflows:
                       prepare-release, prepare-release-go, prepare-release-npm,
                       publish-crate, push-brew-tap, render-brew-formula,
                       build-deb, sign-mac, notarize-mac, setup-rust, setup-go,
                       fetch-artifact, changelog-check, run-precommit-gate, etc.

  bin/                 ~30 CLI tools on $PATH (via dodot's profile.zsh export):
                       - Policy/setup:   apply-ruleset, sweep-github-policy,
                                         install-release-{secrets,token},
                                         enable-dependabot-security, detect-stack
                       - PR loop:        gh-copilot-{on,off,review,wait},
                                         gh-pr-checks-wait, gh-pr-resolve-thread
                       - Release:        release-cut, release-lex, release-beta-list
                       - Sync/audit:     release-sync, audit-repo, audit-portfolio,
                                         audit-smoke-test, done-check
                       - Changelog:      changelog, changelog-add, changelog-cut,
                                         changelog-render (symlinks → templates/)
                       - Multi-repo:     clone-lex-repos, clone-lex-stack

  scripts/             CI scripts exec'd by composite actions (not user-facing):
                       ci-publish-crate.sh, render-brew-formula.py,
                       build-tauri.sh, roll-changelog.sh, prepare-nvim-release.sh, etc.

  templates/           Path-mirror layout for file sync:
    commons/           → synced to EVERY consumer (setup-dev-env.sh,
                         .claude/settings.json, changelog tools, lint ignores)
    components/<c>/    → synced to consumers whose Stack declares <c>
                         (lefthook fragments, lint configs)
    <stack>/           → synced to consumers of that Stack
                         (manifest.yaml declaring default Components,
                          stack-specific bin/ scripts like bin/build)
    render/            → NOT synced — used by CI to produce per-release artifacts
                         (e.g. Homebrew formula templates)

  env/                 Cloud-session bootstrap:
    setup.sh           Pasted into claude.ai/code Setup Script field; installs
                       OS-level tooling, clones release/, seeds skills + CLAUDE.md
    CLAUDE.md          User-level agent instructions for portfolio-wide rules

  orchestrator/        Python harness for multi-repo work via Claude Agent SDK

  skills/              Claude Code skills distributed to all sessions:
                       gh-pr-review-loop, pr-review-respond, gh-repo-setup,
                       release-issue-relay, lex-primer, electron-e2e-testing,
                       macos-signing-notarization, padz-for-agents, etc.

  rulesets/            Branch-protection JSON templates (main-protection.json.tmpl)
  tests/               Fixtures per Stack + cloud-env-check Docker harness
  examples/            Paste-ready consumer release.yml files
  docs/                Per-category, per-component, per-stack guides + proposals

  How updates propagate to consumers

  Two mechanisms, covering two surfaces:

  1. Reusable workflows — via @v1 floating tag

  Consumer repos have a thin release.yml that calls a reusable workflow:

  jobs:
    release:
      uses: arthur-debert/release/.github/workflows/rust-cli.yml@v1
      secrets: inherit
      with:
        crate-name: my-cli

  The v1 branch always points at the latest non-breaking tag. Patch a workflow in release/, tag it, advance v1 — every consumer picks up the fix on their next CI run with zero edits.

  2. In-tree files — via release-sync at session start

  Some files must live in the consumer's tree (CODEOWNERS, lefthook config, bin/check-*, setup-dev-env.sh, .claude/settings.json). Every consumer carries scripts/setup-dev-env.sh which runs release-sync on
  session start. It pulls canonical files from ~/h/release into the working tree using git show <ref>:<path> (never touching the release repo's working tree).

  The sync uses a path-mirror convention: destination path = source path with the templates/commons/, templates/components/<c>/, or templates/<stack>/ prefix stripped.

  Branch dial for testing: push changes to release/beta/<consumer-name> or release/beta/<stack> — release-sync picks that branch for matching consumers instead of main. Merge to main to ship fleet-wide.

  Drift detection: CI runs release-sync --check; if any managed file has drifted, the build fails.

  The Component model

  Each Stack declares its default Components in templates/<stack>/manifest.yaml:

  # templates/rust/manifest.yaml
  stack: rust
  components:
    - shell-quality
    - rust-quality

  Each Component provides its own files (lint configs, lefthook fragments, bin/ scripts). During sync, release-sync merges:
  1. templates/commons/** (base for everyone)
  2. Each declared Component's files from templates/components/<c>/
  3. Stack-specific files from templates/<stack>/

  lefthook.yml is generated (not stored in templates) by deep-merging a base fragment + each Component's lefthook.fragment.yaml + the Stack's own fragment.

  Consumers can override the Component list with a .release-sync.yaml at their repo root.

  What every onboarded repo gets

  Regardless of Stack, every consumer gets:

  - Branch protection — bin/apply-ruleset applies the ruleset from rulesets/main-protection.json.tmpl
  - Automated code review — Copilot + Gemini review every PR via copilot-review.yml
  - Policy files — CODEOWNERS, PR template, copilot-instructions.md, dependabot.yml
  - Pre-commit hooks — lefthook composed from Component fragments, calling bin/check-fmt / bin/check-lint / bin/check-tests
  - Session bootstrap — scripts/setup-dev-env.sh installs deps, wires lefthook, imports certs
  - Agent guidance — per-repo CLAUDE.md + portfolio-wide rules from env/CLAUDE.md
  - Release secrets — propagated via bin/install-release-secrets (7 secrets) and bin/install-release-token

  The release flow

  From a developer's perspective, cutting a release is:

  cd ~/h/my-project
  release-cut minor     # or: release-cut 1.8.0

  release-cut reads the current version from the manifest (Cargo.toml by default), computes the new version, and dispatches the consumer's release.yml workflow via gh workflow dispatch. Everything runs in
  CI:

  1. Bump version files (Cargo.toml, package.json, etc.)
  2. Roll changelog (## [Unreleased] → ## [vX.Y.Z])
  3. Commit + tag
  4. Build (per-platform binaries, installers, wasm packages)
  5. Create GitHub Release with artifacts
  6. Stack-specific publishing: crates.io, npm, PyPI, Homebrew tap push, VS Code Marketplace, macOS code signing + notarization

  The agentic PR loop

  Every repo follows the same review-and-merge flow:

  1. Agent opens PR (live, not draft)
  2. Copilot + Gemini auto-review
  3. Agent waits for both reviews, triages comments (address or push back)
  4. Agent resolves threads, pushes fixups
  5. CI green + threads resolved → PR is mergeable
  6. Human reviews and merges

  This is supported by bin/gh-copilot-review, bin/gh-copilot-wait, bin/gh-pr-resolve-thread, and the gh-pr-review-loop skill.

  Audit and compliance tools

  - bin/audit-repo — checks a single repo against the portfolio baseline: ruleset present, RELEASE_TOKEN set, copilot-review wired, CODEOWNERS present, dependabot enabled, CI green, release-sync state
  present, scripts inventory, workflow canonicity 
  - bin/audit-portfolio — runs audit-repo across the entire fleet (from managed-repos.yaml)
  - bin/audit-smoke-test — quick validation pass
  - bin/done-check — reports the state of each (Stack, Repo) pair against the contract

  The states contract

  Each (Stack, Repo) pair progresses through four states:

  ┌───────────────┬──────────────────────────────────────────────────────────────────┐
  │     State     │                             Meaning                              │
  ├───────────────┼──────────────────────────────────────────────────────────────────┤
  │ planned       │ Identified but no work landed                                    │
  ├───────────────┼──────────────────────────────────────────────────────────────────┤
  │ implemented   │ Workflow wired + bin/<verb> synced + passes locally              │
  ├───────────────┼──────────────────────────────────────────────────────────────────┤
  │ pilot-running │ All of implemented + successful end-to-end CI run for every Flow │
  ├───────────────┼──────────────────────────────────────────────────────────────────┤
  │ fleet-adopted │ ≥80% of consumers for this Stack are pilot-running               │
  └───────────────┴──────────────────────────────────────────────────────────────────┘

  The rule: "Nothing that handles releases is really done if it isn't releasing things." Code that hasn't done its job in CI is implemented at best.

  Cloud and local environments

  The same infrastructure works in three environments:

  - Local dev — bin/ is on $PATH via dodot; release-sync runs on session start
  - Claude Code Cloud — env/setup.sh bootstraps the Ubuntu container with OS tooling + a clone of release/; per-session setup-dev-env.sh does the rest
  - GitHub Actions — reusable workflows + composite actions handle everything; consumers just call uses: arthur-debert/release/.github/workflows/<stack>.yml@v1

  Current fleet status

  As of May 2025, 11 Stacks cover 22 repos. Most Stacks are fleet-adopted: rust-cli (7/7), rust-lib (2/2), electron-app (2/2), tauri-app (1/1), vscode-ext (1/1), nvim-plugin (1/1), tree-sitter (1/1),
  zed-extension (1/1), go-cli (1/1), gh-action (1/1). The CI-reusable half (*-ci.yml) is partially adopted — rust, electron, and tauri have canonical CI workflows; the rest still use bespoke CI in the
  consumer.

Consumer Repos Executables:

    In consumer repos, we keep three questions in mind:
    1. Does a canonical bin/<verb> already handle this? → Delete the script. It's redundant.
    2. Is this a core dev capability release/ should provide but doesn't yet? → Track it as a gap. Don't dump it in app-bin/ and forget about it.
    3. Is it genuinely app-specific? (theme generators, model fetchers, UI comparison tools) → app-bin/
