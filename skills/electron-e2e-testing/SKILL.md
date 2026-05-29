---
name: electron-e2e-testing
description: |
  Reliable, fast E2E tests for Electron desktop apps with Playwright.
  Prescribes a canonical `window.__e2e` runtime contract and `E2E_*` env vars
  so every electron project's tests look the same. Use when:
  (1) Setting up or fixing E2E tests in an Electron app
  (2) Diagnosing flaky or slow E2E tests (waitForTimeout abuse, magic sleeps,
      DOM-string assertions)
  (3) Migrating a project to the canonical `window.__e2e` / `E2E_*` convention
  (4) Setting up GitHub Actions CI for Electron E2E tests
  Sister skill: `tauri-e2e-testing` (same patterns, different launch).
---

# Electron E2E Testing

Hard-won patterns for an electron desktop app's E2E suite. Worked out across
multiple real apps (Monaco + LSP editor, image gallery + preview server) and
encoded as a **runtime contract** so tests stop being reinvented per project.

## Philosophy: E2E is for glue, not logic

E2E tests are expensive (slow + brittle by nature). They earn their place only
when they verify things unit tests can't.

- **No logic in the renderer.** All meaningful logic — formatting, validation,
  domain rules, even view sizing — belongs in the backend (Rust, main process,
  or compiled-to-WASM core). Renderers are for input gathering and display.
  This is more extreme than typical advice; it's load-bearing for the rest.
- **Unit tests are the quality gatekeeper.** Logic is tested at the source
  (in Rust / core), with property tests and edge cases. Frontend unit tests
  cover orchestration: event handlers, IPC calls, rendering of returned data.
- **E2E tests verify glue + UX.** Two questions only:
  1. *Does the integration path work?* (Did the click reach the LSP? Did the
     IPC call return? Did the result render?)
  2. *Does the user-visible experience match expectations?* (Did the markers
     show up where the model says they should be?)
  E2E tests should NOT re-verify what unit tests already cover. If an E2E test
  duplicates formatter logic ("the output should not equal the input"), delete
  the assertion and assert on the integration path instead.

A test with no `expect()` calls, or with silent skips
(`if (visible) assert; else log("skipping")`), provides false confidence and
is worse than no test. Delete it or make the assertion unconditional.

## The runtime contract: `window.__e2e`

Every electron app exposes a single namespace to tests. This is the contract
the rest of the skill builds on, and the contract that lets shared helpers
be lifted into a reusable library later.

```ts
interface E2EHooks {
  // Lifecycle readiness flags. App sets each true when the named subsystem
  // is FULLY ready (post-handshake, post-load). Tests poll these.
  ready: {
    app: boolean                       // main window booted, renderer loaded
    [subsystem: string]: boolean       // lsp, db, indexer, preview, etc.
  }

  // Append-only event log. App calls signal() to add. Tests read events[]
  // (filter by type) for observation without console parsing.
  events: Array<{ type: string; ts: number; payload?: unknown }>

  // Imperative bridge — project-specific verbs (focus, getValue, setValue,
  // formatDocument, navigateTo, etc.). Bridge methods are documented in the
  // app's tests/e2e/lib/bridge.d.ts.
  bridge: Record<string, (...args: any[]) => any>

  // Helper the app uses to append to events[].
  signal(type: string, payload?: unknown): void
}

declare global {
  interface Window { __e2e: E2EHooks }
}
```

**Setup (preload or earliest renderer entry):**

```ts
window.__e2e = {
  ready: { app: false },
  events: [],
  bridge: {},
  signal(type, payload) {
    this.events.push({ type, ts: Date.now(), payload })
  },
}
```

**App lifecycle hooks:**

```ts
// After main window finishes loading + initial render:
window.__e2e.ready.app = true
window.__e2e.signal('app:ready')

// After LSP handshake completes (initialized notification received):
window.__e2e.ready.lsp = true
window.__e2e.signal('lsp:ready')

// On dispose / connection loss:
window.__e2e.ready.lsp = false
```

## The 4 reusable patterns

Every electron e2e test reduces to combinations of these four. If you find
yourself reaching for `waitForTimeout` or parsing console output, you're
working around the absence of one of them — add it instead.

### 1. Readiness signals — `window.__e2e.ready.X`

Boolean flags set by the app the moment a subsystem becomes usable. Tests poll
them with `page.waitForFunction`. **Never** approximate readiness via a sleep.

```ts
// In tests/e2e/lib/wait.ts
export async function waitForApp(page: Page, timeout = 15_000) {
  await page.waitForFunction(() => window.__e2e?.ready?.app === true,
    null, { timeout })
}
export async function waitForReady(page: Page, key: string, timeout = 15_000) {
  await page.waitForFunction(
    (k) => window.__e2e?.ready?.[k] === true,
    key, { timeout })
}
```

#### The signal timing contract

**Set the flag AFTER the effect is observable, never before the async op.**

This is the most important rule. A signal that fires before the async round-trip
completes will produce tests that pass locally (fast) but fail on CI (slow) —
the worst kind of flake because it's invisible during development.

```ts
// WRONG — signal fires before LSP responds; test thinks it's done
window.__e2e.signal('format:start')
const result = await connection.sendRequest('textDocument/formatting', params)

// RIGHT — signal fires after LSP responds; test sees ready edits
const result = await connection.sendRequest('textDocument/formatting', params)
window.__e2e.signal('format:done', { params, edits: result })
```

When in doubt: ask "at the moment this signal fires, can the test immediately
observe the effect?" If no, move it later.

### 2. Event log — `window.__e2e.events`

The app calls `signal(type, payload)` at every interesting point: ipc-call-out,
ipc-response-in, lsp-request-out, lsp-response-in, file-loaded, model-changed,
etc. Tests read the array (or wait for a specific entry) to verify the
integration path was exercised.

```ts
// In tests:
async function expectEvent(page: Page, type: string, timeout = 5_000) {
  return await page.waitForFunction(
    (t) => window.__e2e.events.find(e => e.type === t),
    type, { timeout })
}

async function eventsOfType(page: Page, type: string) {
  return await page.evaluate(
    (t) => window.__e2e.events.filter(e => e.type === t), type)
}
```

This replaces *all* `console.log` parsing. Console-log assertions are fragile
(format changes break them silently). Events are structured: `{type, ts, payload}`.

#### Reset-then-act-then-assert

For events that accumulate (every keystroke, every IPC), reset before the
action so stale state doesn't pollute the assertion:

```ts
await page.evaluate(() => { window.__e2e.events.length = 0 })   // reset
await formatButton.click()                                       // act
const evt = await expectEvent(page, 'format:done')              // assert
```

The reset must happen BEFORE the action. If the assertion helper resets
internally, the action's result gets cleared before it can be checked.

### 3. Test bridge — `window.__e2e.bridge`

Project-specific verbs the app exposes for tests: `focusEditor()`,
`setValue(text)`, `getValue()`, `openFile(path)`, `selectImages([...])`, etc.
The bridge is **the** way tests cause state changes that aren't natural
user interactions (or where simulated input is unreliable).

```ts
// App side: register bridge verbs after subsystems are ready
window.__e2e.bridge.focusEditor = () => editor.focus()
window.__e2e.bridge.getValue   = () => editor.getModel().getValue()
window.__e2e.bridge.setValue   = (v: string) => editor.getModel().setValue(v)
window.__e2e.bridge.openFile   = (p: string) => fileManager.open(p)
```

```ts
// Test side:
await page.evaluate(() => window.__e2e.bridge.setValue('hello'))
const value = await page.evaluate(() => window.__e2e.bridge.getValue())
expect(value).toBe('hello')
```

Bridge methods are documented in `tests/e2e/lib/bridge.d.ts` — one type
declaration per project listing what's available.

### 4. Deep assertions — assert on the data model, not DOM artifacts

DOM-string assertions are fragile (CSS class renamed → test breaks; copy
deck change → test breaks). Assert on the underlying data the UI is
displaying.

```ts
// BAD: checks for CSS class. Doesn't tell you what the diagnostic says.
await expect(page.locator('.squiggly-error')).toBeVisible()

// GOOD: checks the actual marker data via the bridge / Monaco's API.
const markers = await page.evaluate(() =>
  window.__e2e.bridge.getMarkers())
expect(markers).toContainEqual(expect.objectContaining({
  message: 'Unknown word: mispelled',
  source: 'spellcheck',
  severity: 'error',
}))
```

For collection assertions, prefer `expect.arrayContaining` /
`objectContaining` over array-length checks — those break on unrelated
additions to the collection.

#### Don't duplicate domain logic

E2E tests should verify the integration path, not re-verify what the
formatter / linter / etc. produces. Domain output has its own tests.

```ts
// BAD: duplicates formatter logic — breaks when formatter behavior changes
await page.evaluate(() => window.__e2e.bridge.setValue(UNFORMATTED))
await formatButton.click()
const after = await page.evaluate(() => window.__e2e.bridge.getValue())
expect(after).not.toEqual(UNFORMATTED)   // asserts on formatter output

// GOOD: verifies the LSP integration path was exercised with right options
await formatButton.click()
const evt = await expectEvent(page, 'format:done')
expect(evt.payload.params.options).toMatchObject({ tabSize: 4 })
```

When an E2E test breaks on content, check upstream tests first. If domain
logic changed legitimately, update the E2E expectation OR (better) stop
asserting on domain output and assert on the integration path.

## Standardized environment variables

All E2E env vars use the `E2E_` prefix (project-agnostic — these only fire at
electron launch, and `E2E` is namespace enough to avoid collisions).

| Var | Purpose |
|---|---|
| `E2E=1` | Top-level signal that the app is running under tests. Always set. |
| `E2E_HIDE_WINDOW=1` | Suppress the BrowserWindow show on launch. **macOS:** also hide the dock icon (see "Hiding the macOS dock icon" below) — `show: false` alone doesn't prevent dock entry. |
| `E2E_DISABLE_PERSISTENCE=1` | Don't read/write user-settings store. Tests get clean state. |
| `E2E_DISABLE_SINGLE_INSTANCE_LOCK=1` | Allow parallel test runs without single-instance contention. |
| `E2E_USE_BUILD=1` | Load from built renderer (`dist/`) instead of dev server. |
| `E2E_SKIP_BUILD=1` | Skip Playwright's global-setup rebuild (CI already built). |
| `E2E_USER_DATA_DIR=<path>` | Override Electron's userData dir per test for isolation. |
| `E2E_FIXTURES=<path>` | Path to test fixtures (the app reads this at boot). |

Project-specific config (LSP paths, log levels, custom paths) keeps its own
prefix (`LEX_LSP_PATH`, `GAL_PATH`, etc.) — those aren't E2E-only.

### Hiding the macOS dock icon

`E2E_HIDE_WINDOW=1` only suppresses the BrowserWindow's `show()`. On macOS,
Electron still registers the app in the Dock the moment `app.whenReady()`
fires — and that dock icon bounces and steals focus, even with no window
visible. Without an explicit dock-hide, running the suite locally on a Mac
interrupts whatever you were doing every time a test launches the app.

The fix lives in the main process, before any `app.whenReady()` work or
window creation:

```ts
// Run as early as possible — top of main.ts / electron/app.ts, before the
// app is "ready" so the dock entry never appears in the first place.
if (process.env.E2E_HIDE_WINDOW === '1' && process.platform === 'darwin') {
  app.dock?.hide()
  // Equivalent and arguably cleaner intent:
  // app.setActivationPolicy('accessory')
}
```

Either call hides the dock icon and removes the menu-bar entry. Use
`app.dock?.hide()` (optional chaining) so the same line is safe to run on
any platform — it's a no-op on Linux/Windows where `app.dock` is undefined.
The platform guard is still worth keeping for clarity.

`skipTaskbar: true` on each `BrowserWindow` is the analogous fix for the
Windows taskbar / Linux taskbar — independent from `app.dock.hide()` and
worth setting on the same windows for parity. macOS ignores it (the Dock
is controlled by the `app`-level setting, not per-window).

## Test helper library structure

Every electron project's `tests/e2e/lib/` looks the same:

```text
tests/e2e/lib/
  app.ts          # Playwright fixture (electronApp + page, with cleanup)
  wait.ts         # waitForApp, waitForReady(key), expectEvent, expect.poll wrappers
  locators.ts     # centralized DOM selectors (data-testid > role > title > text)
  bridge.d.ts     # TypeScript declarations for window.__e2e.bridge methods
  assertions.ts   # deep assertion helpers (markers, model state, etc.)
  fixtures/       # test data files (sample inputs, expected outputs)
```

### `app.ts` — Playwright fixture

```ts
export const test = base.extend<AppFixtures>({
  appLaunchOptions: [({}, use) => use(defaultOptions), { option: true }],

  electronApp: async ({ appLaunchOptions }, use) => {
    const app = await launchApp(appLaunchOptions)
    await use(app)
    await app.close()                  // guaranteed even if test throws
  },

  page: async ({ electronApp }, use) => {
    const page = await electronApp.firstWindow()
    await page.waitForLoadState('domcontentloaded')
    await waitForApp(page)             // window.__e2e.ready.app === true
    await use(page)
  },
})
```

**Fixture conventions:**

- Fixture name `appLaunchOptions` (not `launchOptions` — Playwright reserves that).
- Default `E2E_DISABLE_PERSISTENCE=1`; tests that need persistence override via
  `test.use({ appLaunchOptions: { env: { E2E_DISABLE_PERSISTENCE: '0' } } })`.
- Tests that call `location.reload()` mid-test can't use the fixture cleanly
  (page reference changes). Keep those with manual launch + try/finally.
- Pages that use a splash window pattern: `firstWindow()` returns the splash;
  poll `app.windows()` for a non-`data:` URL to find the real window.

### `wait.ts` — deterministic waits only

```ts
export async function waitForApp(page: Page, timeout = 15_000) {
  await page.waitForFunction(() => window.__e2e?.ready?.app === true,
    null, { timeout })
}

export async function waitForReady(page: Page, key: string, timeout = 15_000) {
  await page.waitForFunction(
    (k) => window.__e2e?.ready?.[k] === true,
    key, { timeout })
}

export async function expectEvent(page: Page, type: string,
                                  predicate?: (e: any) => boolean,
                                  timeout = 5_000) {
  // Serialize the predicate across the page boundary — Playwright's
  // page.waitForFunction runs in the browser context. Two constraints:
  //
  //   1. Predicate must be self-contained (a pure function over its
  //      argument). It can't close over test-process variables —
  //      they don't exist in the browser context. Capture values
  //      into the function body instead:
  //          // ❌  const expected = 42; expectEvent(p, 't', e => e.n === expected)
  //          // ✅  expectEvent(p, 't', e => e.n === 42)
  //
  //   2. Strict CSP (unsafe-eval disallowed) blocks `new Function`.
  //      If your app sets such a CSP for tests, switch this helper
  //      to a structural-match form (predicate as a `{key: value}`
  //      object rather than a function).
  //
  // Both branches return the LATEST matching event (.findLast for the
  // predicate path) — consistent with the no-predicate path's
  // evts[evts.length-1] semantic.
  return await page.waitForFunction(
    ({ t, pStr }) => {
      const evts = window.__e2e.events.filter(e => e.type === t)
      if (!pStr) return evts[evts.length - 1]
      // eslint-disable-next-line no-new-func — predicate is author-controlled
      const fn = new Function('e', 'return (' + pStr + ')(e)') as (e: unknown) => boolean
      return evts.findLast(fn)
    },
    { t: type, pStr: predicate?.toString() }, { timeout })
}
```

**The `waitForTimeout` rule:** never. The only acceptable use is `< 100ms` in
tight keyboard navigation loops where the UI needs a frame to process each
keystroke — and even there, `expect.poll` on resulting state is preferred.

### `locators.ts` — selector centralization

One file, one place to update when selectors change:

```ts
export const editor      = (p: Page) => p.locator('.monaco-editor').first()
export const formatBtn   = (p: Page) => p.locator('button[title="Format Document"]')
export const fileItem    = (p: Page, name: string) =>
  p.locator('[data-testid="file-tree-item"]', { hasText: name })
```

Selector preference: `data-testid` > `getByRole` > `[title=…]` > text. Library-
internal selectors (`.suggest-widget`, `.squiggly-error`) are acceptable for
stable-library internals — but centralize them here.

### `bridge.d.ts` — bridge type declarations

```ts
declare global {
  interface Window {
    __e2e: {
      ready: { app: boolean; lsp: boolean; preview: boolean }
      events: Array<{ type: string; ts: number; payload?: unknown }>
      bridge: {
        focusEditor(): void
        getValue(): string
        setValue(v: string): void
        getMarkers(): Marker[]
        openFile(path: string): Promise<void>
      }
      signal(type: string, payload?: unknown): void
    }
  }
}
export {}
```

This is the project's e2e-public API. Adding a verb means adding it here +
implementing in the app + documenting in the test file using it.

### `assertions.ts` — deep helpers

```ts
export async function expectMarkers(page: Page, expected: Partial<Marker>[]) {
  const markers = await page.evaluate(() => window.__e2e.bridge.getMarkers())
  for (const m of expected) {
    expect(markers).toContainEqual(expect.objectContaining(m))
  }
}
```

## Playwright config

```ts
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,                    // electron + LSP startup needs headroom
  retries: process.env.CI ? 1 : 0,    // catch transient flakes; trace on retry
  workers: 1,                         // electron doesn't parallel well
  use: {
    trace: 'on-first-retry',          // only useful with retries >= 1
  },
})
```

## CI setup (GitHub Actions + Ubuntu)

### The sandbox problem

Electron on Linux CI fails with:
`The SUID sandbox helper binary was found, but is not configured correctly.`

Fix: detect CI and pass `--no-sandbox`:

```ts
const ciArgs = process.platform === 'linux' && process.env.CI
  ? ['--no-sandbox']
  : []
const app = await electron.launch({ args: ['.', ...ciArgs, ...extra], env })
```

### Workflow shape

```yaml
e2e:
  name: E2E Tests
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with: { submodules: true }
    - uses: actions/setup-node@v4
      with: { node-version: '20', cache: 'npm' }
    - run: npm ci
    - run: bash scripts/download-deps.sh        # any external binaries (LSP, etc.)
      env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
    - run: npx tsc && npx vite build            # build renderer + main; skip packaging
    - run: xvfb-run --auto-servernum npx playwright test
      env:
        E2E: '1'
        E2E_USE_BUILD: '1'
        E2E_SKIP_BUILD: '1'
        E2E_HIDE_WINDOW: '1'
        E2E_DISABLE_PERSISTENCE: '1'
    - if: failure()
      uses: actions/upload-artifact@v4
      with:
        name: playwright-traces
        path: test-results/
        retention-days: 7
```

Key points:

- `xvfb-run --auto-servernum` — virtual display for Electron.
- Skip `electron-builder` packaging — `tsc && vite build` is enough for the
  renderer. The packaged-binary smoke test (if any) is a separate job.
- `E2E_USE_BUILD=1` + `E2E_SKIP_BUILD=1` — load built files; skip rebuild in
  global-setup since CI already built.
- Upload traces on failure only — they're large but essential for debugging
  CI-only flakes (use `trace: 'on-first-retry'` to capture them).

## Common pitfalls

### Console-log-based assertions

Never parse `console.log`. Format changes silently break the test. Use the
event log instead:

```ts
// BAD: parse "Received tokens: 42" out of console
const log = logs.find(l => l.includes('Received tokens:'))
const count = parseInt(log.replace(/[^0-9]/g, ''))

// GOOD: app emits a structured event; test reads it
// App: window.__e2e.signal('semantic-tokens:received', { count: data.length / 5 })
const evt = await expectEvent(page, 'semantic-tokens:received')
expect(evt.payload.count).toBeGreaterThan(0)
```

### Tests with zero assertions

A test that fires actions and "verifies it doesn't crash" passes when the
feature is broken. Either add a concrete assertion (event log, marker, model
state) or delete the test.

### Silent skips

`if (visible) { assert } else { console.log('skipping') }` always passes. Make
the assertion unconditional. If the feature genuinely can't be tested in
headless mode, use `test.skip()` / `test.fixme()` with a reason.

### Test isolation via persistence

If a test enables persistence (`E2E_DISABLE_PERSISTENCE=0`) and writes
settings, subsequent tests inherit stale state. Solutions:

- Default fixture leaves persistence off.
- Tests that need persistence reset settings to defaults at start.
- Settings tests use `test.use()` to override, not env vars in test body.

### Monaco / contenteditable focus

Headless input often doesn't reach Monaco. Use the bridge to focus
programmatically:

```ts
await editor.click()
await page.evaluate(() => window.__e2e.bridge.focusEditor())
await page.keyboard.type('text')
```

### Splash windows

Apps that show a splash window before the editor: `firstWindow()` returns the
splash. Wait for the editor window via Playwright's event API:

```ts
async function getEditorWindow(app: ElectronApplication, timeout = 10_000) {
  // Check existing windows first (the editor may already be open).
  for (const w of app.windows()) {
    if (!w.url().startsWith('data:')) return w
  }
  // Otherwise wait for the next window event with the right URL.
  // Uses Playwright's event-driven wait rather than a setTimeout poll
  // — matches the "no waitForTimeout / sleeps" rule this skill itself
  // teaches.
  return await app.waitForEvent('window', {
    predicate: (page) => !page.url().startsWith('data:'),
    timeout,
  })
}
```

### `editor.action.formatSelection` programmatic firing

Monaco may skip the LSP range formatting provider when triggered
programmatically (the selection isn't "real" from its perspective). Full
document formatting via button/menu click is more reliable for E2E.

## Migration checklist (existing project → canonical conventions)

When migrating a project that has its own e2e setup:

1. Add `window.__e2e` initialization in preload / earliest entry. Keep old
   globals (e.g., `__lexLspReady`) writing through to the new namespace
   during the transition.
2. Move existing readiness flags into `window.__e2e.ready.X`.
3. Add `signal()` calls at integration boundaries (LSP request/response, IPC,
   file load, model change). Verify each fires AFTER its effect.
4. Move imperative test verbs (`lexTest.focusEditor` etc.) to
   `window.__e2e.bridge.focusEditor`. Add a `bridge.d.ts`.
5. Rename env vars to `E2E_*`. Update Playwright config, CI workflow, and any
   env-reading code in main process.
6. Replace remaining `waitForTimeout` calls with `waitForReady`,
   `expectEvent`, or `expect.poll`.
7. Replace `console.log` parsing with event-log reads.
8. Replace DOM-class assertions with bridge-based deep assertions.
9. Run the full suite locally + CI. Compare runtimes — typical migration cuts
   30–60% of total runtime by eliminating sleeps.
10. Delete the legacy globals once all tests pass.

## Sister skill

`tauri-e2e-testing` (planned, when the first Tauri app's e2e suite picks up):
identical philosophy and four patterns. Differences are in launch
(`tauri-driver` / WebDriver instead of Playwright's `electron.launch`) and IPC
event hookup. The `window.__e2e` contract is shared so a single library can
target both stacks later.
