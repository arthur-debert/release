# Fleet tooling: `release-core admin repos list` + `release-core admin repos verify`

Two tools for operating across the whole managed portfolio, both built on
one rule: **`managed-repos.yaml` is the only source of truth, and on-disk
layout is data, not logic.** Both live under `release-core admin repos`.

## The manifest contract

`managed-repos.yaml` lists every managed repo as
`{ repo: <owner>/<name>, path: <dir-relative-to-REPOS_ROOT> }`, grouped by
project. Two hard rules:

- **No discovery.** There is no ruleset / `gh api` auto-discovery path. It
  produced recurring bugs (repos drifting in/out of scope silently). The
  manifest is edited by hand; that's the feature.
- **Zero layout logic.** A repo's location is `$REPOS_ROOT/<path>` — a pure
  join. No single-vs-multi-repo heuristics, no org-vs-project-name guessing,
  no probing. The `path` is non-derivable from `repo` on purpose (the `lex`
  project's `lex-fmt/*` repos live under `lex-fmt/`; phos's
  `phos-editor/*` repos live under `phos/` (as `phos-app`/`phos-core`);
  single-repo projects collapse to a bare dir), so it is written down
  rather than computed.

`$REPOS_ROOT` defaults to `~/h` (a dev machine). The same manifest + the
same join describe both the real `~/h` and a throwaway synthetic checkout —
only the root changes.

## `release-core admin repos list`

The accessor. Reads the manifest, applies the join, nothing else.

```sh
release-core admin repos list                       # owner/name, one per line
release-core admin repos list --paths               # owner/name <TAB> abspath <TAB> found|missing
release-core admin repos list --clone [--refresh]   # clone missing repos into their paths
release-core admin repos list --paths lex-fmt/lex   # trailing owner/name args restrict the set
```

`release-core admin repos audit` reads the same manifest (the only other
consumer).

## `release-core admin repos verify`

The pre-flight lint sweep — the realization of "checkout all repos,
release-sync them, try to commit," using real consumer files instead of
synthetic fixtures (this is why per-Kind fixtures, release#298, were closed
won't-do).

```sh
release-core admin repos verify                       # sync whole fleet from HEAD, run the gate
release-core admin repos verify --ref main            # verify what @v2 is about to point at
release-core admin repos verify --only arthur-debert/padz   # one repo (scopes the clone too)
```

It is **hermetic**: clones into a throwaway root (default
`/tmp/release-fleet-verify-$USER`), syncs each consumer from the candidate
revision, runs `lefthook run pre-commit --all-files`, and reports
`repo / kind / sync / gate`. It never touches your `~/h` checkouts. Run it
before `release-core cut` — the cut auto-advances the floating `@vN`
(release's `release.yml` passes `advance-major: true`), so the sweep must
happen before cutting to catch a commons/lint regression in release's own
tree instead of one consumer at a time after `@vN` moves.
(`release-core admin release advance-major` remains as the manual/recovery
advance for when that workflow job failed.)

Caveats:

- The clones carry no project toolchain (no `npm install` / `cargo`), so
  npm/frontend kinds FAIL typecheck/eslint/prettier as expected missing-deps
  artifacts, not regressions — classify by failing step before chasing
  (release#594 tracks making verify classify these itself); the consumer's
  own PR CI is the real gate for project checks. (The managed gate itself is
  HARD — a missing gate tool exits non-zero, never skips, per release#498.)
- `--ref` reads templates from a git ref, so commit release changes before
  sweeping (an uncommitted working tree isn't what gets synced).
- **Post-advance verification needs a FRESH consumer event — never
  `gh run rerun`.** A reusable-workflow ref (`…/x.yml@vN`) is resolved once,
  when the run is created; `gh run rerun` re-executes that original snapshot,
  so after a cut advances `@vN` a rerun still exercises
  the pre-advance release and proves nothing about the fix (caught live on
  padz, epic #583). Push an empty commit to the consumer's main
  (`git commit --allow-empty -m "ci: re-resolve @vN" && git push`) or
  `gh workflow run` to create a run that resolves the new `vN` tip.

## `release-core admin canary run` — the pre-ship consumer-life round

Where `repos verify` is the fast, fleet-wide *gate* sweep, the canary round
is the slow, deep *workflow* test (release#587, epic #583): it makes a real
synthetic consumer live its full life — boot from source, materialize,
`bin/check`, e2e/bats, and a genuine prerelease cut — against an
**unreleased** candidate ref, before `release-core cut` moves the fleet.
Different instruments; run both before cutting (the canary half is
enforced — see the gate below).

```sh
release-core admin canary run --ref main            # the round to run before a cut
release-core admin canary run --ref my-branch --json
```

Per registered family (the top-level `canaries:` block of
`managed-repos.yaml` — deliberately NOT under `projects:`, so the
verify/migrate/inbox sweeps never include the canary repos) it:

1. Publishes `canary/<sha12>` — a branch of release at the candidate SHA
   with every `uses: arthur-debert/release/...@vN` self-ref rewritten to the
   branch, so the canary's reusable workflow resolves its composites AND its
   wheel (arm-gate's non-`vN` from-source path) at the candidate tree.
2. Seeds the canary repo from source in a sandboxed venv (`XDG_*` under
   `--root`, default `/tmp/release-canary-$USER`), points its thin callers
   at `canary/<sha12>`, adds a changelog fragment, commits the seed.
3. Dispatches **fresh events** (never `gh run rerun`): the seed push (→ CI)
   and a `0.0.<n>-canary.<runid>` prerelease cut, in parallel.
4. Polls both runs to conclusion (transient-tolerant backoff, `--timeout`).
5. Prints a per-job classified report — **INFRA** (arm-gate
   materialize/provision, install-release-core, init, prepare internals —
   a release bug) vs **PROJECT** (bin/check, cargo, bats, compilation —
   canary-content rot) — and posts a `canary/<family>` commit status on
   `release@<sha>`. Exit 0 green / 1 failures / 2 setup error.
6. Prunes canary prereleases beyond `--keep` (default 5). `canary/*`
   branches on release are kept (owner decision).

All cut artifacts land on the canary repo only: prerelease tags + GH
prerelease assets; crates/brew/npm are fail-closed fenced (`publish-crates:
false`, `brew: false`, and the matching secrets are never installed there).

The commit status is a prescriptive gate (release#606): `release-core cut`
refuses unless EVERY registered `canary/<family>` context is a green commit
status on the exact main-HEAD sha it dispatches (the remote default-branch
head — what the workflow_dispatch actually cuts). Exact-sha binding makes
freshness mechanical: any new commit on main invalidates the previous round
by construction, so the recipe is verify → canary run → cut. There is **no
skip flag and no env-var escape** (owner decision, #587 — escape hatches
shrink); the refusal names the one next action,
`release-core admin canary run --ref main`. The gate is registry-driven:
no `canaries:` registered (every consumer repo — they carry no
managed-repos.yaml) ⇒ no gate, mechanically, not via a skip.

## Onboarding a new repo

Onboarding (GitHub-side policy + repo-side files) is driven by the
`release-core admin` tree, not by a skill:
`release-core admin policy ruleset|sweep|dependabot` for the GitHub-side
state, `release-core admin secrets token` for `RELEASE_TOKEN`, then add the
repo to `managed-repos.yaml` and verify with
`release-core audit --repo <owner/repo>` /
`release-core admin smoke-test <owner/repo>`.
See `release-core admin --help` for the current commands and flags.
