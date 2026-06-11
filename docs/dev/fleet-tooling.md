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

## Onboarding a new repo

Onboarding (GitHub-side policy + repo-side files) is driven by the
`release-core admin` tree, not by a skill:
`release-core admin policy ruleset|sweep|dependabot` for the GitHub-side
state, `release-core admin secrets token` for `RELEASE_TOKEN`, then add the
repo to `managed-repos.yaml` and verify with
`release-core audit --repo <owner/repo>` /
`release-core admin smoke-test <owner/repo>`.
See `release-core admin --help` for the current commands and flags.
