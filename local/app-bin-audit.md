# `app-bin/` audit across the fleet

Classification key:
- **a** = canonical-able — script is generic enough to move to a shared `templates/<kind>/bin/`
- **b** = hook — legitimate per-repo lifecycle hook called by CI or packaging convention
- **c** = generator — one-off manual / dev-tool script unique to the repo
- **d** = dead — no callers anywhere; safe to delete
- **d (DEAD?)** = flagged for manual verification; we found no caller at all

Search scope per file: `package.json`, `lefthook*`, `.github/workflows/`, all `*.sh`, `*.mjs`, `*.ts`, `*.tsx`, `*.js`, `*.py`, `Makefile`, `*.md`, `*.yml`, `*.yaml`, `*.toml`, `*.json`. Excludes `node_modules/`, `.git/`, `target/`, `dist/`, `out/`, `build/`. CHANGELOGs ignored (historical noise).

---

### arthur-debert/arami-app

| file | callers | class | note |
| --- | --- | --- | --- |
| compare-ui.ts | self-doc only: `app-bin/compare-ui.ts:5` (`npx playwright test app-bin/compare-ui.ts`) | **d (DEAD?)** | Playwright spec; no script in package.json or workflow wires it. Manual debug tool at best — verify before delete. |
| dev-tauri.sh | `package.json:12` (`"dev:tauri"`) | c | Local dev launcher behind `pnpm dev:tauri`. |
| dev-web.sh | `package.json:11` (`"dev:web"`) | c | Local dev launcher behind `pnpm dev:web`. |
| fetch-wasm | `package.json:8` (`fetch:wasm`), `package.json:9` (`fetch:model`), `CLAUDE.md:35`, `docs/dev/setup.md:16,64` | c | Repo-specific WASM bootstrapping. Heavily documented as the dev setup entrypoint. |
| smoke-hook.sh | `.release-sync-state.yaml:24`, header self-identifies as "Convention hook ... release/'s tauri-app.yml detects this via hashFiles('app-bin/smoke-hook.sh')" | b | Canonical convention hook for `tauri-app.yml`. |

### arthur-debert/arami-core

| file | callers | class | note |
| --- | --- | --- | --- |
| setup-dev.sh | `CLAUDE.md:219`, `tools/golden-gen/README.md:13`, `goldens/README.md:63` | c | Repo-specific dev-env bootstrap (Python venv + golden-gen tool). Docs point to it as the entrypoint. |

### arthur-debert/dodot

| file | callers | class | note |
| --- | --- | --- | --- |
| demo-errors | self-doc only (usage in own header) | **d (DEAD?)** | No `package.json` / Makefile / workflow caller. Dev sandbox demo — possibly invoked by hand. Verify before delete. |
| e2e | self-doc only (usage in own header) | c | Local dev convenience wrapper around bats Docker harness. Documented usage `app-bin/e2e [opts]`. Manual but useful — keep as a dev tool. (No CI caller; CI runs bats directly.) |

### arthur-debert/simple-gal

| file | callers | class | note |
| --- | --- | --- | --- |
| local-build-and-serve.sh | `Cargo.toml:11` (only as exclude pattern) | **d (DEAD?)** | Only referenced as a `cargo package` exclude. Header says `./local-build-and-serve.sh <up|down>` — manual dev script. Verify before delete. |

### arthur-debert/simple-gal-ui

| file | callers | class | note |
| --- | --- | --- | --- |
| fetch-simple-gal.mjs | `package.json:11` (`build`), `package.json:26` (`postinstall`), `README.md:41`, `.github/workflows/test.yml:44` | c | Repo-specific binary fetcher; pre-build dep. Pinned versions per-platform. |
| smoke-hook.sh | `.release-sync-state.yaml:24`; header self-identifies as "Convention hook ... release/'s electron-app.yml detects this via hashFiles('app-bin/smoke-hook.sh')" | b | Canonical convention hook for `electron-app.yml`. Identical shape to lexed's. |

### arthur-debert/standout

| file | callers | class | note |
| --- | --- | --- | --- |
| docs-book | none found (README badge link `docs-book` matched literal alt-text, unrelated) | **d (DEAD?)** | mdbook is built via `release/mdbook.yml@v1` (`.github/workflows/docs.yml`); workflow doesn't call this script. Likely a manual `mdbook serve` wrapper. Verify before delete. |
| docs-spellcheck | none found | **d (DEAD?)** | No caller anywhere. Standalone hunspell-over-docs script. Verify before delete. |

### lex-fmt/lex

| file | callers | class | note |
| --- | --- | --- | --- |
| dev (directory: `sandbox-test-linux.sh` + `Dockerfile.sandbox`) | self-doc only (header of `sandbox-test-linux.sh:7-8`) | **d (DEAD?)** | No `package.json` / Makefile / workflow caller. Manual Docker sandbox for `nextest` on linux from macOS dev host. Verify before delete (may still be live as a hand-run dev tool). |

### lex-fmt/lexed

| file | callers | class | note |
| --- | --- | --- | --- |
| build-quicklook.sh | `package.json:25` (`build:quicklook`), pulled in by `prebuild` (`package.json:24`) | c | macOS QuickLook appex build step; ties into electron-builder prebuild. |
| electron-after-pack.mjs | `package.json:251` (`build.afterPack`) | b | electron-builder lifecycle hook (`afterPack`). Repo-specific (signs QuickLook appex). |
| generate-dictionary-supplement.py | `package.json:40-45` (`dictionaries:*`, `supplement:*` scripts; many) | c | Repo-specific spellcheck data generator (cspell-dicts pull). |
| generate-icons.mjs | `package.json:20` (`icons`), pulled in by `prebuild` | c | App icon generator. |
| generate-tries.mjs | `package.json:40-48` (`dictionaries:*`, `tries:*`; many) | c | Repo-specific trie generator consuming the supplement above. |
| smoke-hook.sh | `.release-sync-state.yaml:24`; header self-identifies as `electron-app.yml` convention hook | b | Canonical convention hook for `electron-app.yml`. |

### lex-fmt/nvim

| file | callers | class | note |
| --- | --- | --- | --- |
| ci-fetch-deps.sh | `.github/workflows/test.yml:22` (`pre-check: app-bin/ci-fetch-deps.sh`) | b | CI pre-check hook — `release/`'s nvim-plugin (or rust-cli) test workflow invokes it. Repo-specific dep fetch (lexd-lsp + tree-sitter-lex tarball). |

### lex-fmt/tree-sitter-lex

| file | callers | class | note |
| --- | --- | --- | --- |
| bump-grammars.sh | `.github/workflows/quarterly-grammar-bump.yml:37`, `CLAUDE.md:23` | b | Scheduled CI hook for quarterly grammar refresh. |
| bundle-extras.sh | self-doc only (`bundle-extras.sh:2`: "convention hook for arthur-debert/release/.github/workflows/tree-sitter.yml") | b | Canonical convention hook for `tree-sitter.yml` (hashFiles-detected). Also internally execs `smoke-grammars.sh`. |
| parity-ignored.txt | `test/generate-tests.sh:16` (`IGNORE_LIST="$REPO_DIR/app-bin/parity-ignored.txt"`), `CLAUDE.md:21,58` | c | Data file consumed by parity test generator. Sibling of `parity-print.js`. |
| parity-print.js | `app-bin/parity-print.js:16` (usage in own header: `npx tree-sitter parse ... | node app-bin/parity-print.js`), `CLAUDE.md:20` | c | Manual debug converter (tree-sitter XML → parity format) used during parity-divergence triage. |
| pre-commit | `README.md:80` (`ln -sf ../../app-bin/pre-commit .git/hooks/pre-commit`) | b | Git hook (manually symlinked into `.git/hooks/`). |
| smoke-grammars.sh | `.github/workflows/test.yml:22` (`check-command: 'bin/check && app-bin/smoke-grammars.sh'`), `.github/workflows/quarterly-grammar-bump.yml:54`, `shared/embedded-grammars.json:2`, exec'd from `app-bin/bundle-extras.sh:29` | b | CI gating hook — HEAD-checks every grammar manifest entry. |

### lex-fmt/vscode

| file | callers | class | note |
| --- | --- | --- | --- |
| ci-build-shared-hook.sh | `.github/workflows/test.yml:20` (`pre-check: app-bin/ci-build-shared-hook.sh`) | b | CI pre-check hook for the canonical `vscode-ext-ci.yml`. |
| open_dev_vscode.sh | none found | **d (DEAD?)** | No caller anywhere. Standalone "launch VS Code against the local dev workspace + dev LSP binary" helper. Verify before delete. |
| pre-vsce-package-hook.sh | self-doc only (`pre-vsce-package-hook.sh:2`: "convention hook for arthur-debert/release/vscode-ext.yml"), `.release/.github/copilot-instructions.md:57` | b | Canonical convention hook for `vscode-ext.yml` (hashFiles-detected at package time). |
| try-lex-extension-extensions.txt | `app-bin/try-lex-extension.sh:20,29` | c | Data file (one extension ID per line) consumed by sibling `try-lex-extension.sh`. |
| try-lex-extension.sh | self-doc only (usage in own header) | c | Manual end-to-end "build + install + open VS Code with the local extension" dev script. Heavy CLI surface (`--reset`, `--lsp-path`, `--ts-path`, etc.); clearly active dev tool but no automated caller. |

### lex-fmt/zed-lex

| file | callers | class | note |
| --- | --- | --- | --- |
| gen-injections.py | `README.md:337,342`, self `app-bin/gen-injections.py:54,106` | c | Repo-specific generator for the embedded-grammars injection list. Run manually + checked in CI via its `--check` mode (per README). |
| zed-package-extras.sh | none found via grep; header self-identifies as "Consumer extras hook for the canonical zed-extension release workflow ... Runs AFTER ... `zed-extension.yml` has assembled the standard bundle in `$BUNDLE_DIR` ... BEFORE the tarball is created" | b | Canonical convention hook for `zed-extension.yml` (hashFiles-detected, not grep-visible because the release workflow lives in the `release/` repo, not here). |

---

## Roll-up

**Likely dead (verify before deleting), 8 files:**
- `arami-app/app-bin/compare-ui.ts`
- `dodot/app-bin/demo-errors`
- `simple-gal/app-bin/local-build-and-serve.sh`
- `standout/app-bin/docs-book`
- `standout/app-bin/docs-spellcheck`
- `lex/app-bin/dev/` (whole dir: `sandbox-test-linux.sh` + `Dockerfile.sandbox`)
- `vscode/app-bin/open_dev_vscode.sh`

**Canonical-able (class a) candidates:** none found. All `smoke-hook.sh` instances are *already* canonical-via-convention hooks (the canonical lives in `release/` and is copied via `release-sync` into consumer `app-bin/`); the per-repo copy IS the override slot. The repos that haven't customized smoke-hook.sh from the canonical (arami-app, simple-gal-ui, lexed are byte-similar around the header — diff before claiming overlap) could in principle drop their copy and rely on the default, but that's a workflow-default question, not an "extract to template" question.

**Hooks (class b), 11 files:** all the `*-hook.sh` / `*-extras.sh` / `ci-*-hook.sh` / `pre-commit` / `bump-grammars.sh` / `smoke-grammars.sh` / `electron-after-pack.mjs` plus the three `smoke-hook.sh` instances.

**Generators / repo-specific (class c), 14 files:** every `dev-*.sh`, `setup-dev.sh`, `fetch-*`, `generate-*`, `build-quicklook.sh`, `try-lex-extension.sh`, `gen-injections.py`, `parity-*`, `dodot/app-bin/e2e`.

## Notes for the cleanup PRs

1. **Cross-check the "DEAD?" list against the user's shell history / recent run logs before deleting.** `try-lex-extension.sh`, `dodot/app-bin/e2e`, and `arami-app/dev-*.sh` are not on the dead list precisely because docs/package.json wire them; the dead-flagged ones lack even that. But "no static caller" is not "never run" — confirm with the maintainer.
2. **`zed-package-extras.sh` looks dead by local grep** but is wired by the canonical `zed-extension.yml` via `hashFiles()` convention (same pattern as `smoke-hook.sh`, `bundle-extras.sh`, `pre-vsce-package-hook.sh`). Do not delete.
3. **`standout/app-bin/docs-book`** and **`docs-spellcheck`** look like remnants from before the project moved to the canonical `release/mdbook.yml` workflow. If kept, they're manual local helpers; if deleted, no CI breaks.
4. **`compare-ui.ts`** in arami-app appears to be a one-off Playwright comparison spec; it lives under `app-bin/` rather than `tests/` but isn't wired into any test runner config.
