# Domain Language

## Kind

The type of deliverable a repo ships. Answers: "when I run `bin/build`, what am I building? When I run `bin/release`, what am I publishing?" Each repo has exactly one Kind. The Kind determines the build pipeline, release pipeline, and which reusable workflow the repo calls.

Examples: `rust-cli`, `rust-lib`, `electron-app`, `tauri-app`, `vscode-ext`, `nvim-plugin`, `tree-sitter`, `zed-extension`, `go-cli`, `gh-action`.

A repo may contain multiple languages (e.g. a Tauri app has both Rust and TypeScript), but the Kind is determined by what it produces, not what languages it uses.

Replaces the former term "Stack", which pulled thinking toward language/toolchain rather than artifact.

## Capability

A discrete, composable concern a repo can opt into, orthogonal to its Kind. A Kind's `manifest.yaml` declares its default Capabilities; repos can add more via `.release-sync.yaml`.

The same logical concern (linting, testing, formatting) is implemented differently per language — `cargo test` vs `npm test` vs `go test`. That's why Capabilities are language-scoped: the concern is universal, the tooling is not.

Quality Capabilities provide lint/format/test rules (as lefthook fragments and lint configs). Broader Capabilities can provide scripts and configuration for docs, e2e testing, or other cross-cutting concerns.

Examples: `rust-quality`, `npm-quality`, `go-quality` (language-scoped quality gates); `bats` (e2e testing); `mkdocs` (documentation site).

The shell/markdown/yaml lint gate (shellcheck + markdownlint + yamllint) is **not** a Capability — it is universal, so it lives in `templates/commons/` and applies to every Consumer structurally, independent of Kind manifests (release#320). It was formerly the `shell-quality` Capability.

Capabilities do NOT own build or release logic — that belongs to the Kind.

Replaces the former term "Component".

## Consumer

A repository/project that uses release as its infrastructure manager. Release provides its build, test, release, and policy infrastructure.

Examples: `arthur-debert/phos-app`, `lex-fmt/lex`, `arthur-debert/padz`.

## Task

An atomic verb exposed as `bin/<verb>` in the consumer repo. Same interface regardless of Kind; behavior varies. Tasks are the unit of work a developer or agent invokes.

Examples: `bin/check`, `bin/check-fmt`, `bin/check-lint`, `bin/check-tests`, `bin/build`, `bin/release`.

## Tool

A small, composable executable provided by release as `bin/<name>`. Tools can be combined — quality check is a composition of check-fmt, check-lint, check-tests. Used in workflows, pre-commit hooks, or manually.

## Flow

A user-level goal implemented as a GitHub Actions workflow. Each consumer's Flow is a thin caller of a reusable workflow in the release repo.

Examples: `ci.yml` (run checks on push), `release.yml` (cut and publish a release).

## Development Cycle

The process of developing features and fixes in release, then ensuring adoption in consumer repos. Includes testing locally, creating PRs, and verifying changes in consumers before merging. A release change is not done until it's adopted.

## PR Review Addressed

All comments (if any) submitted via reviews have been resolved. Each comment is either addressed (valid, fixed) or pushed back (insufficient context from reviewer). Either way, the rationale is commented and the thread resolved in GitHub.

## PR Ready

A PR where all reviews are submitted and addressed, CI checks pass, and there are no merge conflicts. This is when the human gate enters — the PR can be merged after final verification.
