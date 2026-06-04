# Propagating to consumers without disturbing local checkouts (worktrees)

`orc propagate <repo-path>...` re-syncs `.release/**` from `release@<ref>` into
each consumer, opens a PR per repo. Its **pre-flight refuses to run** unless the
target working tree is **clean** _and_ **on a branch literally named `main`**
(`git branch --show-current` must equal the base branch). It also leaves the
checkout on the created `chore/release-sync-update-<sha>` branch on success.

In day-to-day work your local consumer clones are usually **dirty** or **on a
feature branch** — so a naive `orc propagate ~/h/<repo>` either errors out or
moves your working checkout off your WIP. The fix is to propagate through an
**ephemeral git worktree pinned to `main`**, so your primary checkout is never
touched. Two cases:

## Case A — consumer is OFF `main` (on a feature branch): use a worktree

`main` is free, so a second worktree can check it out. This is the common case.

```bash
REPO=~/h/dodot                      # your consumer clone (on some feature branch)
WT=/tmp/release-propagate-wt/dodot  # ephemeral, must not exist yet

git -C "$REPO" fetch origin main -q
git -C "$REPO" worktree add "$WT" main      # clean tree, on `main` (shared ref)

orc propagate --ref main \
  --pr-title "..." --pr-body "..." --commit-msg "..." \
  "$WT"

git -C "$REPO" worktree remove --force "$WT"  # tear down; primary untouched
```

- The worktree shares `.git` with your primary clone, so it's cheap (no reclone)
  and the pushed branch/PR land on the same remote.
- `orc` runs `git pull --ff-only main` inside the worktree, then branches from it.
  Your primary checkout stays on its feature branch with its uncommitted changes
  intact — only the shared `main` ref may fast-forward (harmless; your tree
  isn't on it).
- Batch many at once: create all the worktrees, pass every `$WT` to a single
  `orc propagate` call, then remove them all. `orc` captures per-repo failures,
  so one bad repo doesn't abort the rest.

## Case B — consumer is clean AND on `main`: run directly, then restore

`main` is occupied by the primary checkout, so a second worktree **cannot** host
it (`git worktree add … main` → "already checked out"). The clone already meets
`orc`'s preconditions, so run `orc` directly and restore afterward:

```bash
orc propagate --ref main --pr-title "..." ... ~/h/burgertocow
git -C ~/h/burgertocow checkout main    # orc left it on the propagate branch
```

## Case C — consumer is on `main` but DIRTY: not propagatable as-is

`main` is occupied (no worktree) and the tree is dirty (`orc` refuses). Resolve
the local state first — commit/stash/clean your changes, or land the branch —
then it becomes Case B. Do **not** auto-clean; those are the owner's changes.

## Resolving paths

`REPOS_ROOT=~/h release-core admin repos list --paths` prints `owner/name <TAB> abspath
<TAB> found|missing` for the whole fleet. Filter to taste (e.g. exclude `phos`,
pick `found` only) to build the path list.

## Pre-flight

Always run `release-core admin repos verify --ref <candidate>` before a fleet
propagate. It clones the fleet hermetically and runs the gate per consumer.
Expect npm/frontend repos (electron/tauri/vscode) to FAIL on `eslint`/`typecheck`
with no `node_modules` in the toolchain-less clone — that's a **missing-deps
artifact, not real debt** (the gate passes in real consumer CI where deps are
installed). A FAIL in a hook that doesn't need project deps (markdownlint,
yamllint, shellcheck) IS real and must be fixed upstream before propagating.
