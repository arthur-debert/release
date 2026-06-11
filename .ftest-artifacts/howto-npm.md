# release-core how-to — this repo (Kind: vscode-ext / npm)

Infrastructure (gate, build, release, PR flow) is managed by `release-core`.
Don't hand-edit managed files (`.release/**`). App code is yours.
Confirm the Kind anytime with `release-core detect-kind`.

## The five verbs
- **lint / quality gate:** `release-core gate` (the one quality gate — runs eslint, prettier, markdownlint, yamllint, typecheck: the SAME set CI runs). Standalone: `npm run lint`. NOTE: needs `node_modules` — run `npm install` once in a fresh checkout.
  Hard gate: a missing tool is a setup failure, never a skip. `--no-verify` is never OK.
  The gate inspects **staged** files — `git add` your change first, or it reports a false green.
- **test:** `npm test` (needs `node_modules` + a prior build)
- **build:** `npm run build`
- **release:** `release-core cut <major|minor|patch>`  (CI builds/signs/publishes — never release locally)
- **run:** `npm run watch`, then F5 (Extension Development Host)

## The dev cycle (the ONE flow — draft-first)
1. Branch off `main`.
2. Make the change.
3. **Add a changelog fragment (required, same PR):** `changelog add <slug> "<one-line summary>"`.
   - `<slug>` is kebab-case (e.g. `peek-doc`). It writes `CHANGELOG/unreleased-<slug>.md`.
   - A release **refuses to cut** without a fragment. Never hand-edit `CHANGELOG.md`.
4. Stage your change and run `release-core gate` until green.
5. Open the PR **as a draft**: `gh pr create --draft`.
6. **Drive the review loop via the `gh-pr-review-loop` skill** (it arms the PR-loop
   guard, requests the required reviews, waits, triages, resolves threads). A bare `gh pr create`
   may be blocked by the guard until the skill arms the loop — that's expected.
7. Flip to **ready** (`gh pr ready`) only when reviewed + CI green + mergeable.
   That hands it to a human. Don't auto-merge. (`gh pr ready --undo` to flip back.)

## When infra (the gate/build/release itself) is broken
Don't patch it here — escalate: `release-core issue file`. The fix belongs in
`arthur-debert/release` and arrives on the next session.
