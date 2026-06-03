# release-core init `--commit` / `--push` — closing the pull-model commit-hygiene crux

> Status: implemented (`feat/init-auto-commit`). Scope: the CONFIG subset
> `release-core init` already materializes — NOT the full `.release/` tree
> (that is `release-sync`; the sync→init migration is a separate effort).

## The gap

`release/` distributes dev infrastructure under a **pull model**: at
SessionStart, `templates/commons/bin/setup-dev-env.sh` (§0.2) →
`install-release-core` → resolves the `release_core` wheel → runs
**`release-core init`** in the consumer, which re-materializes the managed
CONFIG subset (`lefthook.yml`, the managed lint/format configs) from the release
tip.

`init` updates the **working tree** but never **commits**. So the refreshed
managed files sit uncommitted until some unrelated feature PR accidentally
absorbs them. That already caused a real mess: a consumer's `.release/`-adjacent
managed drift rode along inside a feature PR and had to be surgically stripped
out before review.

Because the managed config tree is **fully generated** (composed from
`templates/commons/` fragments; never hand-authored in the consumer), there is
nothing in it to review. It can — and should — **auto-commit itself** rather
than ride along, uncommitted, waiting to pollute the next human PR.

## The design

Two opt-in flags on `release-core init`. A plain `release-core init` is
unchanged (non-committing); committing is strictly opt-in.

### `--commit`

After a materialization that **actually changed files** (and not `--dry-run`):

1. Stage **only the exact files `init` wrote** — the created + overwritten +
   repaired paths it already tracks. **Never `git add -A`; never stage anything
   else.**
2. Commit with a deterministic message:
   `chore(release): sync managed config to <ref>` — `<ref>` is the resolved
   source (the short tip SHA on the release-dev git-engine path, or
   `release-core <version>` on the default wheel-bundle path). The ` to <ref>`
   suffix is omitted if no ref is known.
3. The commit is **pathspec-scoped** (`git commit -- <managed paths>`), so a
   user's other in-progress staged/unstaged work is **never folded in** — it is
   left exactly as it was, uncommitted.

Conservative by construction:

- `changed == 0` → no commit (idempotent; a second SessionStart is a clean
  no-op).
- `--dry-run` → never commits (and never writes).
- Not a git repo / git unavailable → quiet no-op; **init still succeeds**.
- If the managed paths can't be committed cleanly (any git error, or nothing
  actually differs in the index), it prints a notice and **skips** the commit —
  it never fails `init`.

### `--push` (implies `--commit`, guarded)

Fast-forward push the managed commit **only when ALL hold**:

- `--push` was given, **and**
- the current branch **is the repo's default branch** (resolved from
  `origin/HEAD` — never guessed as `main`/`master`), **and**
- the working tree is **otherwise clean** (no non-managed changes).

Otherwise the commit stays **local**. On a feature branch the managed commit
just rides along — visible in the branch, and excluded from review as a managed
change. **Never** force-pushes; **never** auto-merges a PR. A push failure (e.g.
a non-fast-forward) is a notice, not an init failure.

## Wiring (the SessionStart opt-in)

`install-release-core` now invokes `release-core init --commit` (not bare
`init`). It passes `--commit` but **not** `--push`:

- The generated config is committed locally, closing the hygiene gap at the
  moment it is refreshed.
- SessionStart should not push on the consumer's behalf; `init`'s own `--push`
  guard would only fast-forward on a clean default branch anyway, so the safe
  default is commit-only and let the commit ride the branch.

`setup-dev-env.sh` reaches `init` only through `install-release-core`, so the
single edit there propagates to both local and cloud SessionStart.

## Scope boundary

This covers the **CONFIG subset** `init` materializes (`init.CONFIG_FILES`:
`lefthook.yml` + the managed lint/format configs). It does **not** cover the
full `.release/` tree — that is `release-sync`'s job, and folding sync into init
(so the whole managed tree auto-commits) is a separate, later migration.

## Test coverage

`templates/commons/lib/release_core/tests/test_core_init.py` (drives real
throwaway git repos — git is already a release dependency):

- commits only managed files when changed; commit subject carries the source ref
- no commit when unchanged (`changed == 0`)
- does NOT stage unrelated working-tree changes
- does NOT fold in pre-staged unrelated changes
- `--dry-run` never commits (and never writes)
- non-git dir is a safe no-op (init still succeeds)
- `--push` only on the default branch; skipped on a feature branch
- `--push` skipped when the tree is otherwise dirty; pushes on a clean default
  branch (verified against a bare origin)
- a commit failure does not fail init (best-effort)

`tests/install-release-core/install-release-core.bats` asserts the resolver now
runs `init --commit`.
