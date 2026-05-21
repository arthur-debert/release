# `npm-quality` Component

Stack-default Component for npm-based front-end Stacks: `electron-app`
today, eventually `tauri-app`, `vscode-ext`, anything that ships a
`package.json`. Bundles the frontend hygiene hooks that should run
identically across all of them — ESLint, Prettier, TypeScript
compiler-as-linter.

## What ships

Synced by `release-sync` when a consumer's Stack manifest lists
`npm-quality` (the `electron-app` Stack lists it by default):

- **`lefthook.fragment.yaml`** — three pre-commit hooks (priority 2,
  check-only):
  - `eslint` — globs `**/*.{js,jsx,ts,tsx,vue,mjs,cjs}` plus
    `.eslintrc*` / `eslint.config.*` configs.
  - `prettier` — globs the same plus styling files
    (`.svelte`, `.json`, `.html`, `.css`, `.scss`, `.md`, `.yml`,
    `.yaml`).
  - `typecheck` — fires when any `.ts` / `.tsx` / `tsconfig*.json`
    is staged. Invokes `npm run typecheck` (no per-file argument —
    tsc operates on the project).

  Each hook prefers a consumer-defined npm script alias (`lint`,
  `format:check`, `typecheck`) and falls back to a direct `npx`
  invocation if the alias isn't wired. This matches the bats
  Component's "no script, no failure" pattern at hook level: a
  consumer that hasn't wired prettier yet doesn't block commits.

- **No `bin/` scripts at Component level.** The Stack-level
  `bin/check-fmt`, `bin/check-lint`, `bin/check-tests` (shipped by
  `templates/electron-app/bin/`) consume `npm run` scripts and
  compose them into the umbrella. Splitting frontend-tool wrappers
  across both Component and Stack layers would create duplicate
  shape for no win.

## How a consumer adopts

For an `electron-app` Stack consumer: no action needed. The
manifest at `templates/electron-app/manifest.yaml` lists
`npm-quality` by default — `release-sync` picks it up.

For an `electron-app` consumer that wants to drop it (rare):
override with `.release-sync.yaml`:

```yaml
# .release-sync.yaml — opt OUT of npm-quality (atypical)
components:
  - shell-quality
  # npm-quality omitted on purpose
```

For a Stack that doesn't yet default to `npm-quality` (`tauri-app`,
`vscode-ext` — coming soon): opt IN explicitly:

```yaml
components:
  - shell-quality       # Stack default
  - <other defaults>
  - npm-quality         # opt-in until tauri-app's manifest defaults it
```

## Why no CI workflow

The reusable CI workflow for `electron-app` is
`electron-ci.yml@v1` — see `docs/per-stack/electron-ci.md`. That
workflow runs the same `bin/check` umbrella the Component's hooks
preview locally. Re-running each tool as a separate CI step would
just duplicate work the umbrella already covers.

When `tauri-app` / `vscode-ext` get their own reusable CI workflows,
they will follow the same shape: setup-node + `bin/check` —
`npm-quality` doesn't need its own workflow, just hooks.

## Script-name conventions

The hooks (and the Stack-level `bin/check-*` scripts) assume the
consumer's `package.json` exposes the standard portfolio aliases:

| Alias | Purpose |
|---|---|
| `lint` | ESLint over the project. `eslint . --max-warnings 0` is the canonical shape. |
| `format:check` | Prettier check (no write). `prettier --check .` or a narrower glob. |
| `typecheck` | `tsc --noEmit`. The hook fires only when TS files are staged. |
| `test:unit` | Vitest (or other unit-only runner). The umbrella's `bin/check-tests` forwards `-- --run` so vitest doesn't enter watch mode. |

Consumers that haven't wired one of these get a skip-with-notice
from the corresponding `bin/check-*` script and a silent fallback
to `npx <tool>` at hook level. Wire them in `package.json` to
upgrade the hook from "best effort" to "first-class".

## Why the `bash -c` wrapper in the fragment

Lefthook's default runner doesn't evaluate `||` itself; without
`bash -c` the fallback expression `(npm run ... || npx ...)` would
be passed as literal args to the first command. Wrapping each line
in `bash -c '...'` makes the conditional behave as written.
