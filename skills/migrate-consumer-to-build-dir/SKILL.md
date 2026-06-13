---
name: migrate-consumer-to-build-dir
description: "Migrate one consumer repo from the old in-place release-sync model to the new .release/ build-dir + symlinks model (ADR-0001). Performs the full sequence: checkout main, branch, run release-sync --migrate, install lefthook, commit with the standard message, push, open a PR titled 'Adopt release-sync build-dir + symlinks', and hand off to gh-pr-review-loop. STOPS at ready-to-merge — user does the final merge. Encodes the guardrails learned across 17 consumer migrations: never git add -A, never paper over shared-lint failures consumer-side, distinguish modified-tracked (stop) from untracked-scratch (ignore), and handle the re-sync (PR already exists) case via reset + force-with-lease. Use when: user asks to migrate a consumer to the build-dir model, adopt ADR-0001 on a repo, run release-sync --migrate on a consumer, port a repo to the symlinks layout, or any phrasing meaning 'pull repo X into the .release/ era'."
---

# migrate-consumer-to-build-dir

Drives one consumer repo through the ADR-0001 migration in one shot. The shape of the change is identical for every consumer; the per-repo work is just running the sequence cleanly and triaging CI.

The design rationale lives in `docs/adr/0001-release-sync-build-dir-with-symlinks.md`. Read that first if you don't know why `.release/` + symlinks exists.

## When to use

- User points at a consumer repo and asks for any of: "migrate to build-dir", "adopt ADR-0001", "run release-sync --migrate on X", "port X to the .release/ model", "do the symlinks migration on X".
- A previously-opened migration PR needs to be re-synced because release/main has moved (re-sync path below).
- User is doing a batch and asks you to do "the next one" from a list — same sequence, one repo at a time.

## When NOT to use

- The repo is not yet onboarded to release-sync at all (no `.release-sync.yaml`). That's `gh-repo-setup` territory first.
- The user wants to *change* what release-sync syncs (template edits) — that's upstream work in release/.
- A shared-lint regression is failing CI across many consumers — fix it upstream in release/, then re-run this skill on the affected consumers.

## Prerequisites

- `release-sync` on PATH (lives at `bin/release-sync` in the release repository; PATH usually already has it via dodot).
- `lefthook` installed (`brew install lefthook` if missing).
- `gh` authenticated.
- The local `release` repository checkout exists and is on `main`.

Set `RELEASE_HOME` once and reuse it in the steps below. `release-sync` already honors this env var; pick whichever resolution path matches your setup:

```sh
# If your shell already has it (e.g. via dodot), keep what's there:
: "${RELEASE_HOME:=$HOME/release}"
# Or derive from where release-sync is installed (only sets RELEASE_HOME
# if release-sync was actually found — otherwise the `&&` short-circuit
# leaves it unset rather than landing on cd ./.. → parent of $PWD):
#   RELEASE_BIN=$(command -v release-sync) && RELEASE_HOME=$(cd "$(dirname "$RELEASE_BIN")/.." && pwd)
[ -x "$RELEASE_HOME/bin/release-sync" ] || { echo "RELEASE_HOME=$RELEASE_HOME is not a release checkout" >&2; exit 1; }
export RELEASE_HOME
```

## Inputs

The user gives you a consumer repo identifier. Resolve to a path:

- `simple-gal-action` → `~/h/arthur-debert/simple-gal-action` (or wherever it lives — see Locate the repo).
- `lex-fmt/nvim` → `~/h/lex-fmt/nvim`.
- A bare repo name → search `~/h/*/` and `~/h/` one level.

## The sequence

Each numbered block is one logical step. Run them in order. Stop at the first hard failure and surface it to the user.

### 0. Locate the repo

```sh
# User said "X". Find it.
CONSUMER="<repo-name-or-org/name>"
for cand in \
  "$HOME/h/$CONSUMER" \
  "$HOME/h/arthur-debert/$CONSUMER" \
  "$HOME/h/lex-fmt/$CONSUMER"; do
  if [ -d "$cand/.git" ]; then CONSUMER_PATH="$cand"; break; fi
done
[ -n "${CONSUMER_PATH:-}" ] || { echo "could not locate $CONSUMER under ~/h" >&2; exit 1; }
echo "consumer: $CONSUMER_PATH"
```

If still not found, ask the user for the absolute path. Do not guess.

### 1. Confirm the git remote matches expectation

```sh
cd "$CONSUMER_PATH"
REMOTE=$(gh repo view --json nameWithOwner -q .nameWithOwner)
echo "remote: $REMOTE"
```

If `$REMOTE` doesn't match what the user said (e.g. they said `lex-fmt/nvim` but the remote is `someone-else/nvim`), STOP and confirm.

### 1b. Check the in-flight lock (parallel-agent coordination)

Before doing any work, check whether another session already owns this consumer's migration. Two sessions racing the same repo produced a duplicate stacked PR that had to be closed as CONFLICTING (release#302). The lock is an open PR carrying the `migration-in-flight` label.

```sh
cd "$CONSUMER_PATH"
INFLIGHT=$(gh pr list --state open --label migration-in-flight \
  --json number,url,headRefName --jq '.[] | "\(.number)\t\(.url)\t\(.headRefName)"')
if [ -n "$INFLIGHT" ]; then
  echo "STOP: a migration is already in flight on $REMOTE:" >&2
  echo "$INFLIGHT" >&2
  exit 1
fi
```

If a labelled PR exists, **STOP and surface it** — another agent/session is on it. The only exception is when the user explicitly says "re-sync"/"take over" this repo; in that case the existing PR *is* yours and you proceed down the re-sync path (step 4 detects the branch). Do not open a second PR.

### 2. Get main clean and current

```sh
cd "$CONSUMER_PATH"
git fetch origin
git checkout main
git pull --ff-only
```

If `git checkout main` fails because of uncommitted changes on the current branch, inspect them (next step). Don't delete branches — just switching to main is enough.

### 3. Sanity gate: modified tracked files block; untracked do NOT

```sh
cd "$CONSUMER_PATH"
MODIFIED=$(git status --porcelain --untracked-files=no || true)
if [ -n "$MODIFIED" ]; then
  echo "STOP: modified tracked files present:" >&2
  echo "$MODIFIED" >&2
  exit 1
fi
# Untracked is FINE — leave it alone. Things commonly seen and to be ignored:
#   - nested clones like ./<repo>/
#   - "Alternatives — *.txt" scratch files
#   - assets/*.png, assets/*.svg work-in-progress
# Do NOT git clean these. Do NOT git add them.
git status --porcelain | awk '/^\?\?/{print}' | sed 's/^/  ignoring untracked: /' || true
```

### 4. Create (or reset) the migration branch

The required branch name is **`chore/adopt-release-sync-build-dir`** — use exactly this. The gh-pr-review-loop tooling and any future audit will rely on it.

Check **both** the local branch and the remote-tracking ref. A previous attempt from another clone / cloud session can leave `origin/chore/adopt-release-sync-build-dir` published while this clone has no local copy; without the remote check we'd create a fresh branch from main, push without `--force-with-lease`, and the existing remote would reject the push.

```sh
BRANCH=chore/adopt-release-sync-build-dir
cd "$CONSUMER_PATH"
# Step 2 already ran `git fetch origin` for the whole remote, so the
# remote-tracking ref below is fresh. (Don't `git fetch origin $BRANCH
# 2>/dev/null || true` here — that silently swallows real failures
# like auth/network errors and corrupts the detection downstream.)
LOCAL_EXISTS=0
REMOTE_EXISTS=0
if git show-ref --verify --quiet "refs/heads/$BRANCH";          then LOCAL_EXISTS=1; fi
if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then REMOTE_EXISTS=1; fi

if [ "$LOCAL_EXISTS" = 1 ]; then
  # Re-sync path with local branch present: reuse it, reset to fresh main.
  git checkout "$BRANCH"
  git reset --hard origin/main
  RESYNC=1
elif [ "$REMOTE_EXISTS" = 1 ]; then
  # Re-sync path with only the remote branch present (PR opened from
  # another clone or a prior cloud session). Create a fresh local
  # branch from main; push will use --force-with-lease.
  git checkout -b "$BRANCH" origin/main
  RESYNC=1
else
  git checkout -b "$BRANCH"
  RESYNC=0
fi
```

### 5. Ensure release/main is current

The migration's correctness depends on the templates the local release checkout sees right now.

```sh
( cd "$RELEASE_HOME" && git pull --ff-only )
```

If release has uncommitted changes, surface that and stop — running with a dirty templates tree means non-reproducible output.

### 6. Run the migration

```sh
cd "$CONSUMER_PATH"
release-sync --migrate
```

(`$RELEASE_HOME` is already exported from Prerequisites.)

Expected output ends with a summary like `0 conflicts`. If there are conflicts, STOP — that means a managed file in the consumer was hand-edited and differs from the template. Surface the conflict list to the user.

### 7. Wire pre-commit hooks

```sh
cd "$CONSUMER_PATH"
lefthook install
```

### 8. Stage — carefully

```sh
cd "$CONSUMER_PATH"
# Modifications + deletions of already-tracked files.
git add -u
# The new build directory.
git add .release/
# Workflow files that release-sync wrote as real files (not symlinks) because
# GitHub Actions does not dereference symlinks for workflows.
git add .github/
# Verify nothing else snuck in.
git status --short
```

**HARD RULE: NEVER `git add -A` or `git add .`.** Both will hoover up the untracked scratch from step 3 (nested clones, "Alternatives — *.txt", in-progress assets). The three explicit adds above cover everything release-sync produces.

If `git status --short` shows anything staged that's not under `.release/`, `.github/`, or a previously-tracked file, STOP and investigate before committing.

### 9. Commit with the standard message

Use a HEREDOC verbatim. The message is the same on every consumer — agents downstream (`release-core admin repos audit`, future migration verifiers) match on these exact words.

```sh
cd "$CONSUMER_PATH"
git commit -m "$(cat <<'EOF'
Adopt release-sync build-dir + symlinks (ADR-0001)

release-sync --migrate moves all managed files into .release/ and
replaces the previously-checked-in real files with relative symlinks.
Also rewrites .release-sync.yaml: components: -> capabilities:.

.github/workflows/copilot-review.yml is written as a real file (with
a managed-by-release-sync marker) because GitHub Actions does not
dereference symlinks for workflow files.

No behavior changes — bin/check, bin/build, etc. resolve to the same
content through the symlinks.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### 10. Push

`release-sync` does not switch branches, but if any earlier step (e.g. a `release-core admin policy ruleset` invocation in the same session) flipped HEAD to main, an unguarded push would land on main. The branch-current guard runs **before** the push so it actually prevents the unsafe operation.

```sh
cd "$CONSUMER_PATH"
[ "$(git branch --show-current)" = "$BRANCH" ] || { echo "wrong branch — aborting"; exit 1; }
if [ "$RESYNC" = 1 ]; then
  git push --force-with-lease -u origin "$BRANCH"
else
  git push -u origin "$BRANCH"
fi
```

### 11. Open the PR

Skip the `gh pr create` if `--force-with-lease` push went to an existing PR — gh-pr-review-loop will pick it up. Either way, **set the in-flight lock** (step 11b) so a parallel session backs off.

```sh
cd "$CONSUMER_PATH"
gh pr create --title "Adopt release-sync build-dir + symlinks" --body "$(cat <<'EOF'
## Summary

Migrates this repo to the `.release/` build-dir + symlinks model (ADR-0001).
`release-sync --migrate` moved all managed files into `.release/` and replaced
the previously-checked-in real files with relative symlinks pointing into
`.release/`. The `.release-sync.yaml` schema was rewritten: `components:` →
`capabilities:`.

`.github/workflows/copilot-review.yml` is written as a real file (not a
symlink) because GitHub Actions does not dereference symlinks for workflow
files. The release-sync managed-by marker is preserved at the top.

## Verified locally

- [x] `release-sync --migrate` ran with 0 conflicts
- [x] `lefthook install` wired pre-commit hooks
- [x] `git status` showed only `.release/`, `.github/`, and tracked-file
      modifications/deletions before commit
- [x] No behavior change — `bin/check`, `bin/build`, etc. resolve through
      symlinks to the same content as before

Closes the ADR-0001 migration for this repo.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### 11b. Set the in-flight lock

Label the PR `migration-in-flight` (the lock other sessions check in step 1b). Create the label first if the consumer repo doesn't have it yet — `--add-label` fails on an unknown label. Idempotent: re-running on an already-labelled PR is a no-op.

```sh
cd "$CONSUMER_PATH"
PR=$(gh pr view --json number --jq .number)   # works for both freshly-created and re-sync PRs
gh label create migration-in-flight \
  --color FBCA04 --description "ADR-0001 migration owned by an active agent/session" \
  2>/dev/null || true
gh pr edit "$PR" --add-label migration-in-flight
```

The label is released naturally when the PR merges or closes — no explicit teardown. gh-pr-review-loop leaves it in place while driving the PR.

### 12. Hand off to gh-pr-review-loop

Invoke the `gh-pr-review-loop` skill. Let it drive the PR through Copilot review, fixes, and into the ready-to-merge state.

**STOP at "ready to merge".** Do NOT merge. The user does the final merge themselves — this is a session-wide rule, not specific to this migration.

## Triage rules for CI failures

When the review loop surfaces a CI failure, decide which bucket it falls into:

### Bucket A: known shared-lint regression (do NOT fix consumer-side)

Examples:

- `shellcheck` false positive on a vendored file, a `completions/` script, a test fixture, or a file release-sync itself put in place.
- A new `lefthook` step that's overly strict and would need a per-consumer disable.
- Anything where the fix would mean editing files inside `.release/` (you can't — they're rebuilt every sync) or adding consumer-side overrides to shared files.

**Action:** STOP. Surface the failure to the user with a short note: "this is a shared file out of sync — fix belongs in release/, not here." Do NOT:

- add `lefthook.yml` overrides
- add per-file `# shellcheck disable=...` to symlinked files
- replace a symlink with a hand-edited real file
- ignore a `.release/`-managed file via `.gitignore`

Any of those put a shared file out of sync, which is the exact thing this migration is meant to prevent.

### Bucket B: genuine consumer-specific failure

Examples:

- An actual test failure in the consumer's own test suite.
- A content bug in a consumer-owned file (not a symlink into `.release/`).
- A missing tool/dependency unique to this consumer.

**Action:** Surface the failure to the user. Offer to fix it in this PR or as a follow-up — let the user pick. If you fix it in this PR, the fix must NOT touch `.release/` or any symlink into `.release/`.

## Stack-specific notes

- **`simple-gal-action`** (Kind=github-action) and **`lex-fmt/nvim`** (Kind=nvim-plugin) both rely on release@main having the github-action template + the lua-only nvim detection (released as #296). If you see "could not detect stack" or "no templates for kind 'github-action'", step 5 (refresh release/main) probably caught a stale checkout — re-pull and retry.
- **Go stack consumers** have a `bin/` gitignore quirk: their `.gitignore` is `/bin/*` + `!/bin/check*` so the release-sync-managed `bin/check`, `bin/check-lint`, etc. symlinks stay visible while the Go-built binary (`bin/<name>`) stays ignored. `release-sync --migrate` knows this; if `git status` after step 6 shows missing `bin/check*` symlinks, the gitignore pattern is wrong — surface to user.
- **Rust / Electron / brew-tap / static-site / tree-sitter / vsce-ext** consumers: no special handling. Run the sequence as written.

## Re-sync path (PR already exists)

If the user says "re-sync repo X" or "the PR for X is stale because release moved":

1. Steps 0-1 unchanged. Step 1b: the lock check finds *your own* open PR — that's expected for a re-sync, so proceed (the user explicitly asked to re-sync this repo).
2. Step 2 unchanged.
3. Step 4 detects the existing branch and resets to `origin/main` (`RESYNC=1`).
4. Steps 5-9 unchanged.
5. Step 10 uses `--force-with-lease`.
6. Step 11's `gh pr create` is skipped — the PR is already open. `gh pr view` to confirm; the new commit just replaces the old one. **Still run step 11b** to ensure the `migration-in-flight` label is present (idempotent).
7. Step 12 hands back to `gh-pr-review-loop`.

Do NOT `git commit --amend` an existing migration commit. Always replace it with a fresh commit on top of `origin/main` — the diff against `main` must reflect exactly what release-sync produces today, not a hand-merged amalgam.

## Pitfalls

- **`git add -A` is the single biggest footgun.** Untracked scratch files are everywhere in working consumer repos (nested clones, drafts, screenshots). The three-step explicit add in step 8 is non-negotiable.
- **`release-core admin policy ruleset` (retired flat: `apply-ruleset`) and other release scripts can change HEAD.** If you ran one earlier in the same session, it may have left HEAD on `main` in the consumer. Always re-check `git branch --show-current` before push.
- **`release-sync --migrate` is destructive within its scope.** It removes `.release/` and recreates it from templates. If a previous migration's `.release/` was hand-edited, those edits are gone — by design.
- **Symlinks vs real files for workflows.** `.github/workflows/copilot-review.yml` is special-cased as a real file. Don't "fix" it to be a symlink — GitHub Actions won't follow it.
- **Don't merge.** The skill ends at ready-to-merge. The user does the merge.
- **Don't batch silently.** If the user asks for multiple consumers, do them one at a time and surface each PR URL before starting the next. Don't open 17 PRs in a loop without the user confirming each.

## Output expected at the end of a successful run

- New branch `chore/adopt-release-sync-build-dir` pushed to the consumer's remote.
- PR open at `https://github.com/<owner>/<repo>/pull/N` with the standard title.
- `gh-pr-review-loop` driven the PR to mergeable (CI green, threads resolved).
- Short status reported back: "PR #N on `<owner>/<repo>` is ready to merge — handing back to you."

## Related

- `gh-pr-review-loop` — the next skill in the chain; drives review/fix loop.
- `gh-repo-setup` — onboarding skill; run *before* this one if the repo isn't on release-sync yet.
- ADR-0001 at `docs/adr/0001-release-sync-build-dir-with-symlinks.md` — design rationale for `.release/` + symlinks.
