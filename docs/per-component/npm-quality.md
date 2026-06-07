# `npm-quality` Capability

Kind-default Capability for npm-based front-end Kinds: `electron-app`
today, eventually `tauri-app`, `vscode-ext`, anything that ships a
`package.json`. Bundles the frontend hygiene hooks that should run
identically across all of them — ESLint, Prettier, TypeScript
compiler-as-linter.

## What ships

Synced by `release-sync` when a consumer's Kind manifest lists
`npm-quality` (the `electron-app` Kind lists it by default):

- **`lefthook.fragment.yaml`** — three pre-commit hooks (priority 2,
  check-only):
  - `eslint` — globs `**/*.{js,jsx,ts,tsx,vue,mjs,cjs}` plus
    `.eslintrc*` / `eslint.config.*` configs. Runs
    `npx --no-install eslint {staged_files}` (per-file scope).
  - `prettier` — globs the same plus styling files
    (`.svelte`, `.json`, `.html`, `.css`, `.scss`, `.md`, `.yml`,
    `.yaml`). Runs `npx --no-install prettier --check
    {staged_files}` (per-file scope).
  - `typecheck` — fires when any `.ts` / `.tsx` / `tsconfig*.json`
    is staged. Invokes `npm run --silent typecheck --if-present`
    (no per-file argument — tsc operates on the project).

  Hooks call the tool directly via `npx --no-install`, NOT via
  `npm run lint` / `npm run format:check`. The portfolio convention
  is that those npm scripts target the WHOLE project (the right
  shape for CI's `bin/check-lint` and `bin/check-fmt`); plumbing
  `{staged_files}` through them either widens the scope back to
  "everything" because the project glob is hard-coded in the
  script, or appends staged files to that glob rather than
  restricting it. Two layers, two scopes: pre-commit = per-file
  via npx; umbrella = whole-project via npm script.

- **No `bin/` scripts at Capability level.** The Kind-level
  `bin/check-fmt`, `bin/check-lint`, `bin/check-tests` (shipped by
  `templates/electron-app/bin/`) consume `npm run` scripts and
  compose them into the umbrella. Splitting frontend-tool wrappers
  across both Capability and Kind layers would create duplicate
  shape for no win.

## How a consumer adopts

For an `electron-app` Kind consumer: no action needed. The
manifest at `templates/electron-app/manifest.yaml` lists
`npm-quality` by default — `release-sync` picks it up.

For an `electron-app` consumer that wants to drop it (rare):
override with `.release-sync.yaml`:

```yaml
# .release-sync.yaml — opt OUT of npm-quality (atypical)
capabilities: []
  # npm-quality omitted on purpose. The shell/markdown/yaml lint gate
  # still applies — it ships universally from commons (release#320),
  # not as a listed Capability.
```

For a Kind that doesn't yet default to `npm-quality` (`tauri-app`,
`vscode-ext` — coming soon): opt IN explicitly:

```yaml
capabilities:
  - <other defaults>
  - npm-quality         # opt-in until tauri-app's manifest defaults it
```

## Why no CI workflow

The reusable CI workflow for `electron-app` is
`electron-ci.yml@v2` — see `docs/per-stack/electron-ci.md`. That
workflow runs the same `bin/check` umbrella the Capability's hooks
preview locally. Re-running each tool as a separate CI step would
just duplicate work the umbrella already covers.

When `tauri-app` / `vscode-ext` get their own reusable CI workflows,
they will follow the same shape: setup-node + `bin/check` —
`npm-quality` doesn't need its own workflow, just hooks.

## Script-name conventions

The hooks (and the Kind-level `bin/check-*` scripts) assume the
consumer's `package.json` exposes the standard portfolio aliases:

| Alias | Purpose |
|---|---|
| `lint` | ESLint over the project. `eslint . --max-warnings 0` is the canonical shape. |
| `format:check` | Prettier check (no write). `prettier --check .` or a narrower glob. |
| `typecheck` | `tsc --noEmit`. The hook fires only when TS files are staged. |
| `test:unit` | Vitest (or other unit-only runner). The umbrella's `bin/check-tests` forwards `-- --run` so vitest doesn't enter watch mode. |

The pre-commit hooks DON'T use these aliases — they call the tool
directly via `npx --no-install` against staged files (see "What
ships" above). The aliases are consumed by the umbrella scripts
(`bin/check-lint`, `bin/check-fmt`, `bin/check-tests`):

- `bin/check-lint` / `bin/check-fmt` skip-with-notice if neither
  the npm script nor the tool dep is present. Wire the alias in
  `package.json` to upgrade from "skipped" to "checked".
- `bin/check-tests` skips if neither `test:unit` nor `test` is
  present.

A project staging `.ts` files without `eslint` declared as a dep
will fail the pre-commit hook (npx --no-install errors). That's
the correct signal — fix the missing dep, don't paper over.

## Why the `bin/check-*` scripts still probe for npm scripts

The Kind-level `bin/check-fmt` / `bin/check-lint` /
`bin/check-tests` (shipped by `templates/electron-app/bin/`)
**do** prefer the consumer's npm script alias when present —
because those scripts run the whole-project check the consumer
defines (their lint config, their prettier glob, their test
selection). The umbrella's job is "run the project's check the
way the project wants it run." The pre-commit hook's job is
"check these N staged files, fast." Different jobs, different
mechanisms.

If a `bin/check-*` script falls back to `npx` (no npm-script
alias wired), it's a graceful default — but adding the alias is
the recommended setup.
