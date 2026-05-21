# Copilot Instructions

This is a Tauri 2.x desktop application. It has two halves:

- A **Rust core** under `src-tauri/` (Cargo workspace; `tauri build`
  produces the native binary + platform bundles).
- A **frontend** at the repo root (`package.json`, typically a
  TypeScript / Vite stack with a UI framework like Svelte, React, or
  Vue).

Both halves are exercised by every PR. The canonical umbrella is
`bin/check` — it runs frontend checks (prettier, eslint, typecheck,
unit tests) AND rust checks (`cargo fmt --check`, `cargo clippy`,
`cargo test`) scoped to `src-tauri/`.

## Before suggesting a fix

- Run the project's umbrella check script: `bin/check` (the take-iii
  Component model canonical umbrella). The umbrella runs the same
  steps CI runs across both halves. If your suggestion doesn't pass
  it, it won't merge — check `.github/workflows/` for the source of
  truth.
- Never propose changes that leave tests failing. Frontend unit tests
  typically run via vitest; rust tests via `cargo test` (or
  `cargo nextest` if installed). End-to-end tests run via Playwright
  against the packaged Tauri build.
- Update the changelog's `Unreleased` section for user-visible changes
  (`CHANGELOG.md`'s `## [Unreleased]` section). Tauri releases bump
  three version files in sync (`package.json`,
  `src-tauri/Cargo.toml`, `src-tauri/tauri.conf.json`) — don't edit
  any of those three by hand for a feature PR; the release workflow
  does it.

## Style and scope

- Keep changes minimal. Don't add features, refactor, or introduce abstractions
  beyond what the task requires.
- No backwards-compatibility hacks: no `// removed` comments, no renaming unused
  vars to `_var`, no shim modules. If something is unused, delete it.
- No fallbacks, defaults, or feature flags unless the PR explicitly asks for them.
- Default to no comments. Well-named identifiers carry the *what*. Reserve
  comments for non-obvious *why* (hidden constraint, workaround, surprising
  invariant).
- Trust internal code and framework guarantees. Only validate at system
  boundaries (user input, IPC messages, filesystem entry, network).

## Tauri-specific guidance

- Rust IPC commands live under `src-tauri/src/`. Keep the surface
  narrow and typed — every `#[tauri::command]` is part of the
  public IPC contract between renderer and core.
- Frontend talks to the core via `@tauri-apps/api` (`invoke`,
  `event` listeners). Don't reach for raw `window.__TAURI__` from
  app code — the typed wrappers exist for a reason.
- Tauri plugins (`tauri-plugin-fs`, `tauri-plugin-dialog`, etc.) are
  declared in `src-tauri/Cargo.toml` AND in `tauri.conf.json`'s
  `plugins` section AND wired into the frontend via
  `@tauri-apps/plugin-<name>`. All three must stay in sync.
- `src-tauri/tauri.conf.json` controls bundle identifiers, window
  defaults, the bundler's per-platform output. Edits there often
  require a full `tauri build` cycle to validate.

## What will get pushed back on

- Suggestions that ignore content under `docs/`.
- Style nits in code that already follows the project's style.
- Comments that restate what the code does.
- Pinning org-internal reusable workflows (e.g. `arthur-debert/release`)
  to SHA — the reusable pattern is "fix once, propagate", and same-owner
  supply-chain risk is negligible.
- Adding `npm` or `cargo` to `.github/dependabot.yml` —
  application-dependency freshness is deliberately disabled per
  portfolio policy. Only `github-actions` is enabled.
