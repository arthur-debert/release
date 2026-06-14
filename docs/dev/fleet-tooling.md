# Fleet tooling: `release-core admin repos list` + `release-core admin repos verify`

Two tools for operating across the whole managed portfolio, both built on
one rule: **`managed-repos.yaml` is the only source of truth, and on-disk
layout is data, not logic.** Both live under `release-core admin repos`.

## The manifest contract

`managed-repos.yaml` lists every managed repo as
`{ repo: <owner>/<name>, path: <dir-relative-to-REPOS_ROOT> }`, grouped by
project. Two hard rules:

- **No discovery.** There is no ruleset / `gh api` auto-discovery path. It
  produced recurring bugs (repos slipping in/out of scope silently). The
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
release-core admin repos list --clone               # clone missing; refresh existing (see below)
release-core admin repos list --paths lex-fmt/lex   # trailing owner/name args restrict the set
```

`--clone` clones every missing repo and **unconditionally** fetches+resets
every existing clone to origin's default branch (resolved from `origin/HEAD`,
so `master` or a slashed name like `release/v1` are honored — not assumed to
be `main`), then names the `ref@sha` each clone now sits at
(`→ <repo>: refreshed to <branch>@<sha> (<abspath>)`). There is no
opt-out: the old `--refresh` opt-in was **removed** (release#624). Reusing a
clone without fetching was a quiet-wrong default — the managed surface syncs
from the candidate ref either way, so a stale clone's consumer-authored half
made a sweep *look* faithful while it lied. A genuinely frozen-clone use case
is a manual clone, not a list/verify mode.

> **Destructive to UNCOMMITTED work in a non-disposable root.** The refresh is
> a `git reset --hard`, so point `$REPOS_ROOT` at a **disposable** dir (e.g.
> `/tmp/...`) for hermetic clones. As a data-loss guard, a clone with
> uncommitted changes is detected (`git status --porcelain`) and
> **skipped-with-warning** (`⚠ <repo>: uncommitted changes — skipping refresh
> …`) rather than reset, and the sweep continues — so clean/hermetic clones
> refresh safely while a live `~/h` checkout's uncommitted work is never
> silently discarded. (This guard is universal on dirtiness, not the forbidden
> `--refresh` opt-out: the verify path's `/tmp` clones are always clean, so
> they always refresh.)

`release-core admin repos audit` reads the same manifest (the only other
consumer).

## `release-core admin repos verify`

The pre-flight lint sweep — the realization of "checkout all repos,
build their managed tree (`release-core init`), try to commit," using real consumer files instead of
synthetic fixtures (this is why per-Kind fixtures, release#298, were closed
won't-do).

```sh
release-core admin repos verify                       # sync whole fleet from HEAD, run the gate
release-core admin repos verify --ref main            # verify what @v2 is about to point at
release-core admin repos verify --only arthur-debert/padz   # one repo (scopes the clone too)
```

It is **hermetic**: clones into a throwaway root (default
`/tmp/release-fleet-verify-$USER`), **unconditionally fetches+resets every
existing clone** to the consumer's default branch (resolved from
`origin/HEAD`, naming the `ref@sha` per repo in the
`==> cloning/refreshing fleet` phase — release#624, so the pre-flight is
faithful by default and never lints frozen-at-clone-time content; the
throwaway clones are always clean, so the dirty-tree data-loss guard above
never fires here), syncs each consumer from the candidate revision, runs
`lefthook run pre-commit --all-files`, and reports
`repo / kind / sync / gate`. It never touches your `~/h` checkouts. Run it
before `release-core cut` — the cut auto-advances the floating `@vN`
(release's `release.yml` passes `advance-major: true`), so the sweep must
happen before cutting to catch a commons/lint regression in release's own
tree instead of one consumer at a time after `@vN` moves.
(`release-core admin release advance-major` remains as the manual/recovery
advance for when that workflow job failed.)

**The tool owns the expected-FAIL classification** (release#594) — the
operator only sees deviations. The clones carry no project toolchain (no
`npm install` / `cargo` — out of scope by design; the consumer's own PR CI is
the real gate for project checks), so:

- A gate FAIL whose failed checks are ALL project-toolchain ones (eslint /
  prettier / typecheck, parsed from the pinned lefthook's summary) is an
  **expected toolchain artifact**: reported as
  `expected-FAIL (npm-deps: …)`, no log chase, exit 0.
- A repo whose expected FAIL has an environmental cause the sweep can't
  satisfy (a sibling checkout: lexed's theme, phos-app's parity repos)
  carries an `expect-verify-fail: <reason>` annotation in
  `managed-repos.yaml` → `expected-FAIL (annotated: <reason>)`.
- An annotated repo that PASSES is flagged **STALE** (shrink-only ratchet) —
  remove the annotation.
- Exit is non-zero ONLY on an unexpected failure (missing clone, sync FAIL,
  or a gate FAIL that doesn't classify); logs are pointed at only then.
  (The managed gate itself is HARD — a missing gate tool exits non-zero,
  never skips, per release#498.)

Caveats:

- `--ref` reads templates from a git ref, so commit release changes before
  sweeping (an uncommitted working tree isn't what gets synced).
- **Post-advance verification needs a FRESH consumer event — never
  `gh run rerun`.** A reusable-workflow ref (`…/x.yml@vN`) is resolved once,
  when the run is created; `gh run rerun` re-executes that original snapshot,
  so after a cut advances `@vN` a rerun still exercises
  the pre-advance release and proves nothing about the fix (caught live on
  padz, epic #583). That fresh event is one command now:
  `release-core admin repos poke` (below).

## `release-core admin repos poke` — the one-command fresh event

The rerun trap as a verb (release#595): instead of the five-step hand ritual
(throwaway clone, empty commit, push, find the run, watch + classify), one
command creates the fresh consumer event and reports the classified verdict.

```sh
release-core admin repos poke arthur-debert/padz --watch   # poke + classified verdict
release-core admin repos poke arthur-debert/padz \
  --reason "ci: re-resolve @v2 after the hotfix"           # custom commit message
```

It resolves the repo through `managed-repos.yaml`, shallow-clones it under
the hermetic root (`<root>/poke/`, never `~/h`), pushes an EMPTY commit to
its default branch (message defaults to
`ci: fresh-event verify of release <latest tag>`), resolves the run(s) that
push triggered **by HEAD SHA** — never `gh run rerun` — and, with `--watch`,
polls them to conclusion and prints the per-job classified report (the shared
classifier, `release_core.classify`): **INFRA** = release/upstream (arm-gate
tree-build/provision, boot, init) vs **PROJECT** = consumer-side
(build/test/deps). Exit 0 green / 1 failures / 2 setup error.

## `release-core admin canary run` — the pre-ship consumer-life round

Where `repos verify` is the fast, fleet-wide *gate* sweep, the canary round
is the slow, deep *workflow* test (release#587, epic #583): it makes a real
synthetic consumer live its full life — boot from source, build the managed tree,
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
   and a `0.0.<n>-canary.<runid>` prerelease cut, in parallel. Every
   family is seeded + dispatched *before any polling starts*, so a
   multi-family round (release#605: rust + vscode-ext) runs concurrently
   off the same `canary/<sha12>` branch and costs one round's wall time.
4. Polls both runs to conclusion (transient-tolerant backoff, `--timeout`).
5. Prints a per-job classified report — **INFRA** (arm-gate
   tree-build/provision, install-release-core, init, prepare internals —
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

## `release-core admin canary init` — the canary-repo lifecycle as a verb

Where `canary run` exercises an existing canary, `canary init`
(release#604) creates or resets one — idempotently, from the authored
per-kind seed under `tests/fixtures/<kind>/`:

```sh
release-core admin canary init --family rust                  # converge
pbpaste | release-core admin canary init --family rust        # + set/rotate RELEASE_TOKEN
release-core admin canary init --family rust --reset          # force-push the authored seed
```

Run from inside release. One run converges everything: `gh repo create`
(PUBLIC, owner decision OQ2; skipped when it exists), seed of main from the
fixture (skipped when already seeded, unless `--reset`), the pull-model
boot from THIS checkout (`install-release-core --from-source` + a sandboxed
`release-core init`, same sandbox as `canary run`), secrets, ruleset, and
the `canaries:` registry entry (appended once).

- **Fixture = source of truth.** Each `tests/fixtures/<kind>/` dir carries
  a `.canary-family` marker naming its family (`rust-cli` → `rust`); it
  holds exactly the canary's *authored* content — project tree, thin
  callers (their `@vN` ref is rewritten to the current floating major at
  seed time), `.release-sync.yaml` — never what init generates (bootstrap
  quartet, copilot-review.yml, CLAUDE.md). Adding a family (release#605:
  vscode-ext) = a new fixture dir + one `canary init` run; the verb never
  changes.
- **Fail-closed secrets (#587).** The canary gets only what its family
  needs: `RELEASE_TOKEN` via the per-repo-targeted token verb (#601; pipe
  the release PAT on stdin to set/rotate), plus whatever the fixture's
  optional `.canary-secrets` marker declares — the rust family declares
  the cert-only Apple signing pair (`APPLE_CERTIFICATE_P12_BASE64`,
  `APPLE_CERTIFICATE_PASSWORD`, sourced from `--auth-dir`, the same
  operator auth files `install-release-secrets` reads), so every canary cut
  exercises sign-mac for real (OQ3). The publish trio
  (`CRATES_IO_KEY`, `HOMEBREW_TAP_TOKEN`, `NPM_TOKEN`) is never installed
  and its presence on the repo *fails the run* until removed; the same
  goes for the `ASC_*` notarization trio (cert-only signing — Apple's
  5-15 min notarytool round-trip stays out of the pre-cut loop). A canary
  with no `RELEASE_TOKEN` and no piped PAT also fails — no skip flags.
- **`--reset` is the wedge escape** — a fresh orphan seed force-pushed to
  main. This is the ONLY repo class where a force-push is sanctioned, and
  the verb hard-refuses anything not in the `canaries:` registry, anything
  in `projects:`, and any repo not named `release-canary-*`.

## Onboarding a new repo

Onboarding (GitHub-side policy + repo-side files) is driven by the
`release-core admin` tree, not by a skill:
`release-core admin policy ruleset|sweep|dependabot` for the GitHub-side
state, `release-core admin secrets token` for `RELEASE_TOKEN`, then add the
repo to `managed-repos.yaml` and verify with
`release-core audit --repo <owner/repo>` /
`release-core admin smoke-test <owner/repo>`.
See `release-core admin --help` for the current commands and flags.

## Sanctioned-bespoke consumer workflows: the `# UNMANAGED` marker

A fleet conformance sweep flags hand-rolled `.github/workflows/*` that bypass
the shared reusable workflows. Most are debt to normalize onto the spine, but
a few are **genuinely repo-domain** — a case the shared workflows do not (and
should not) cover. release#630's gap analysis blessed four: phos-app's
self-hosted GPU E2E lane (`e2e-gpu.yml`), tree-sitter-lex's quarterly
grammar-bump cron, supage's Cloud Run `deploy.yml`, and phos-core's `corpus`
extra-asset release job. (The fifth gap, phos-core's PR-time `wasm.yml`, went
the other way — it re-implemented logic the spine owns, so it was folded into
`rust-ci.yml` as the opt-in `wasm-packages` companion; thin callers pass the
same wasm member list they pass `rust-cli.yml` at release time and drop the
hand-rolled file.)

To keep a blessed workflow from being re-flagged every sweep, the convention
is a **`# UNMANAGED`** line in the workflow's top-of-file comment block. It
declares the file sanctioned-bespoke and exempts it from the
hand-rolled-bypass finding. phos-app's `e2e-gpu.yml` already self-declares it.

The marker is ONLY the bypass signal — it does NOT suppress the assumption
lint (`release-core admin contract lint`): an `# UNMANAGED` workflow that
references a managed ephemeral path still must build the managed tree
first. There is currently no automated fat-workflow linter in this repo (the
release#569/#630 litmus sweep was a one-time manual analysis), so this is a
documented convention any sweep must honor; see
`docs/references/consumer-contract.md` for the full scope/non-scope.
