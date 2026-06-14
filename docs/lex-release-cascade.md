# Lex release cascade

Automates a coordinated release across the six `lex-fmt/*` repos
(`comms`, `lex`, `tree-sitter-lex`, `vscode`, `nvim`, `lexed`).
The system is event-driven: cutting any repo cascades into the
downstream repos automatically — pinned upstream artefacts get
bumped, dependent releases get cut, no human polling required.

See **lex-fmt/lex#640** for the tracking issue, design history, and
all the real-world bugs that surfaced during the rollout.

## The dep chain

```
comms ──┬─→ lex ──┐
        ├─→ tree-sitter-lex ─┬─→ vscode
        │                    ├─→ nvim
        │                    └─→ lexed
        └─→ (editors also submodule comms, but they react to lex /
             tree-sitter releasing, not directly to comms — see
             "Two-hop cascade for editors" below)
```

- **comms**: git submodule for everyone. No upstream deps. Source of the cascade.
- **lex**: Rust workspace publishing crates + `lexd` / `lexd-lsp` binaries. Editors pin `lexd-lsp` via `shared/lex-deps.json`. Submodule of `comms`.
- **tree-sitter-lex**: grammar package publishing a tarball. Editors pin via `shared/lex-deps.json`. Submodule of `comms`.
- **vscode / nvim / lexed**: leaf editor packages. Submodule of `comms`. Pin `lexd-lsp` and `tree-sitter` via `shared/lex-deps.json` (flat schema for vscode/nvim, nested `.deps.<name>.{version,repo}` schema for lexed).

## The two layers

The automation is built in two layers. Each layer is independently useful and can be debugged in isolation.

> **Historical note.** Earlier revisions of this cascade had a "Layer 0" of five
> per-repo `scripts/release/*` primitives (`get-current-version`,
> `get-commits-since-release`, `should-release`, `update-release`,
> `trigger-release`). Those were **retired**. There are no per-repo release
> scripts anymore: release is driven by the managed `bin/` tooling that the
> wheel pull builds into every consumer (`release-core init`) —
> [`bin/diff-since-release`](#binbin-diff-since-release) and the Kind-aware
> [`release-core cut`](#release-core-cut-formerly-the-binrelease-shim-retired-in-476).
> The orchestrator drives those; the event-cascade handler decides via plain git
> and dispatches `release.yml` directly.

### The managed release tools

Each repo carries these two executables under `bin/` (built from the
`arthur-debert/release` wheel by `release-core init` — not hand-authored per repo):

#### `bin/diff-since-release`

| | |
|---|---|
| Args | none |
| Stdout | a `Changes since <tag>:` / `---` header, then `git log --oneline <last-final-tag>..HEAD` (one line per commit; empty log section if nothing new) |
| Exit | `0` normally; `1` if no release tags exist yet |
| Side effects | none |

It is the source for both "**is there anything to release?**" (a non-empty log
section after the `---`) and "**what changed?**". Pre-release tags (`-rc.N`) are
skipped, so the diff is against the last *final* release.

#### `release-core cut` (formerly the `bin/release` shim, retired in #476)

`release-core cut` is the Kind-aware release entry point. The thin `bin/release`
shim that used to exec it was retired in #476 — `release-core cut` is reached
directly as the pip console-script (on the maintainer's PATH for the local
orchestrator) or, in the event cascade, the handler dispatches `release.yml`
itself with the explicit version. It is **Kind-aware**: it reads the current
version from the consumer's manifest source (`Cargo.toml`,
`package.json`, `extension.toml`, or the latest git tag for manifest-less
Kinds), computes the new version from a bump shortcut (`patch`/`minor`/`major`)
or a literal `X.Y.Z[-PRERELEASE]`, and **dispatches
`.github/workflows/release.yml`** with that version.

Everything that mutates state then runs **in CI**: the reusable per-Kind release
workflow (`rust-cli.yml`, `tauri-app.yml`, …) does the version bump, the
CHANGELOG roll, the commit, the tag, the build, and the GitHub Release. There is
no longer any local "bump files + `git add` + commit + PR + admin-merge" step —
that responsibility moved entirely into CI.

| | |
|---|---|
| Args | `patch` \| `minor` \| `major` \| `X.Y.Z[-PRERELEASE]` |
| Stdout | the computed version + a `gh workflow run release.yml` dispatch |
| Exit | `0` on a successful dispatch; non-zero on bad version / missing `release.yml` / `gh` failure |
| Side effects | dispatches `release.yml` (workflow_dispatch). CI does all mutation. |

##### Per-repo manifest surfaces

`release-core cut` reads each repo's current version from its manifest source;
the dep-pin updates (comms submodule, `shared/lex-deps.json`) and the build
itself all happen in CI via the reusable workflow.

| Repo | Version source (read by `release-core cut`) | CI trigger |
|---|---|---|
| `comms` | git tags (no version file) | tag-push (`on: push: tags: [v*]`) |
| `lex` | `[workspace.package].version` in root `Cargo.toml` | **`workflow_dispatch`** (delegates to `arthur-debert/release/rust-cli@v2`) |
| `tree-sitter-lex` | `package.json` `"version"` | tag-push |
| `vscode` | `package.json` `"version"` | tag-push |
| `nvim` | `lua/lex/init.lua` `M.version` (via `version-file` input) | tag-push |
| `lexed` | `package.json` `"version"` | tag-push |

### Layer 1 — local orchestrator (`release-core admin release lex`)

`release-core admin release lex` walks the dep chain locally and drives each repo's release via the managed tools (`bin/diff-since-release` to decide, `release-core cut` to cut) in sequence. Useful for:

- **Local debugging** — surface primitive bugs in isolation (the `--dry-run` mode echoes every step without making changes).
- **Recovery** — re-run from a known-good point if a cascade gets wedged mid-flight.
- **One-button kickoff** — `release-core admin release lex patch --comms ... --lex ...` cuts the whole chain locally without involving GH events.

Day-to-day, you don't need it. The event cascade (Layer 2) does the same work hands-off.

```sh
release-core admin release lex patch \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed

# Status only — what would cascade if I cut comms now?
release-core admin release lex --status \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed
```

### Layer 2 — event-driven cascade (the default)

Every repo's `release.yml` ends with a `notify-downstreams` step that fires `repository_dispatch` events to its direct consumers. Every downstream repo has a `.github/workflows/on-upstream-released.yml` handler that:

1. Receives the dispatch
2. Decides whether to release (commits since the last final release?) → if yes, dispatches `release.yml` with the derived version; CI does the bump + CHANGELOG roll + commit + tag + build + release
3. Cutting this repo's release fires its own `notify-downstreams` → fans further down the chain

#### The cascade flow

```
push tag v0.16.3 to comms
  → comms/release.yml runs (specs.tar.gz, assets.tar.gz, GH release)
    → notify-downstreams fires repository_dispatch upstream-released
        → lex/on-upstream-released.yml
            → diff-since-release: commits present (comms submodule stale)
            → dispatch release.yml patch (workflow_dispatch)
                → rust-cli@v2 bumps + rolls CHANGELOG + commits + tags + publishes
                  crates + builds binaries v0.x.y
                    → lex/release.yml notify-downstreams fires upstream-released to editors
        → tree-sitter-lex/on-upstream-released.yml
            → ... same shape ... → release.yml CI tags v0.x.y
                → notify-downstreams fires to editors
                    → vscode/nvim/lexed handlers each fire
                        → diff-since-release: commits present (multiple pins stale)
                        → dispatch release.yml patch → cut their own release
```

#### Two-hop cascade for editors

Editors submodule `comms` AND pin `lexd-lsp` + `tree-sitter`. When comms releases, the editors *could* react directly — but their `lexd-lsp` and `tree-sitter` pins would still be on the old upstream version. Better to wait for lex + tree-sitter to release first; their release CI will pull a current comms anyway. So:

- comms only emits to **lex + tree-sitter-lex** (NOT editors).
- lex + tree-sitter-lex emit to **vscode + nvim + lexed**.
- Editors get two events (one from lex, one from tree-sitter). Each handler re-checks via `bin/diff-since-release`. Whichever event arrives later wins.

#### `repository_dispatch` mechanics

- Event type: `upstream-released`
- Payload: `{ source, version, ts, run_url }` (form-encoded as `client_payload[key]=value`)
- Authentication: handlers use the default `GITHUB_TOKEN`; emit steps use `${{ secrets.RELEASE_TOKEN }}` (the default `GITHUB_TOKEN` cannot fire cross-repo dispatches — silent 403).
- Concurrency: every handler has `concurrency: { group: layer2-handler }` so a flurry of upstream events doesn't race on the release branch.

## Day-to-day usage

### Cutting a release (the happy path)

Push a tag to the upstream you want to release. Everything else cascades.

```sh
# Cut a new comms release; cascade flows through lex, tree-sitter, editors.
cd comms
git tag v0.17.0 -a -m "v0.17.0"   # annotated! release.yml reads %(contents)
git push origin v0.17.0
```

Watch the cascade:

```sh
gh run watch --repo lex-fmt/comms
# When that finishes, the dispatches have fired. Check downstream activity:
gh run list --repo lex-fmt/lex --workflow=on-upstream-released.yml --limit 1
gh run list --repo lex-fmt/tree-sitter-lex --workflow=on-upstream-released.yml --limit 1
# ... and so on as the cascade reaches editors
```

### What would cascade right now?

```sh
release-core admin release lex --status --comms ../comms --lex ../lex \
  --tree-sitter ../tree-sitter-lex --vscode ../vscode \
  --nvim ../nvim --lexed ../lexed
```

Runs `bin/diff-since-release` against each repo, prints a one-line answer per repo. Read-only. No state changes.

### Local one-button cut (Layer 1)

If you want to drive the cascade locally instead of using GH events (e.g. debugging a stuck cascade), use the orchestrator:

```sh
release-core admin release lex patch \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed
```

Add `--dry-run` to echo every step without doing anything. Add `--only repo1,repo2` to restrict.

### Recovery: a handler failed mid-cascade

The cascade is idempotent at the decide level — if a handler failed before its `release.yml` CI committed the tag, you can re-fire the same dispatch and it'll resume cleanly (the decide step still sees commits). If a handler failed after the tag landed, the next attempt sees no new commits (everything's caught up) and exits cleanly; you only need to re-fire the *release CI* (e.g. `gh workflow run release.yml --repo lex-fmt/lex -f version=X.Y.Z`, or `release-core cut X.Y.Z` from a clone).

Manual dispatch fire (handler will re-evaluate):

```sh
gh api repos/lex-fmt/<downstream>/dispatches --method POST \
  -f event_type=upstream-released \
  -F 'client_payload[source]=<upstream>' \
  -F 'client_payload[version]=v<X.Y.Z>'
```

## Gotchas (real bugs found during the rollout)

These all appeared at least once during the cascade's first cut. Documented so future sessions don't re-discover them.

### Handler gotchas (the parts the cascade-handler still owns)

1. **`GH_TOKEN` on every `gh`-using step.** The handler's `Decide` and `Cut release` steps run `gh` (the `Cut release` step's `gh workflow run` dispatch needs auth). Without `env: GH_TOKEN: ${{ secrets.RELEASE_TOKEN }}` on those steps, `gh` fails auth and the handler bails.
2. **Shell-injection on payload reads.** `${{ github.event.client_payload.X }}` interpolated directly into a `run:` is a code-execution vector if the upstream is compromised. Always route through `env:`.
3. **The handler only *dispatches*; CI owns the mutation.** The handler no longer bumps files, commits, opens a PR, or admin-merges. It fires `release.yml`; the reusable per-Kind workflow does the bump + CHANGELOG roll + commit + tag + build + release. So all the old "local pre-bump" gotchas (annotated tags, `git reset --hard` after merge, stale-branch cleanup, UNRELEASED.md seed) now live in the reusable workflow / `prepare-release` action, not in the handler.

### Reusable-workflow (CI) gotchas — now handled where the mutation happens

These were originally handler/primitive concerns; after the `scripts/release` retirement they belong to the reusable release workflow that `release.yml` calls. Listed here so the history is traceable.

4. **Annotated tags, not lightweight.** `release.yml` reads the tag message via `git tag -l --format='%(contents)'`; the workflow's tag step uses `git tag -a`. Manual cuts must too.
5. **`--allow-same-version` on `npm version`.** Where the workflow bumps `package.json` then runs `npm version <tag>`, the flag avoids the "Version not changed" error if the bump already matched.
6. **Manifest may fall behind tags.** `release-core cut` reads the manifest; if past releases were cut without bumping the manifest, a bump-shortcut may collide with an existing tag (tree-sitter-lex hit this: `package.json` said `0.8.0` while the highest tag was `v0.10.1`). Workaround: pass an explicit `X.Y.Z` to `release-core cut` / the orchestrator.
7. **Resume-on-existing-tag in `arthur-debert/release/.github/actions/prepare-release`.** When a reusable workflow sees an existing tag matching the requested version, it validates the manifest matches and skips the bump+commit+tag — making re-dispatch idempotent.
8. **CHANGELOG / UNRELEASED.md.** The CI bump requires Keep-a-Changelog `## [Unreleased]`; the workflow handles the empty-UNRELEASED seed for cascade runs that carry no human-authored notes.

## Adding a new repo to the cascade

Three pieces. None is hard individually; the order matters.

1. **Set up the managed release tooling.** Run `release-core init` in the new repo so the wheel pull builds `bin/diff-since-release` (and add a `.github/workflows/release.yml` thin caller of the right reusable per-Kind workflow). Cutting goes through the `release-core cut` console-script — no per-repo release scripts to author — and it is Kind-aware.
2. **Add `.github/workflows/on-upstream-released.yml`.** Use the
   reusable workflow (recommended) — a 6-line thin caller:

   ```yaml
   name: On upstream released

   on:
     repository_dispatch:
       types: [upstream-released]

   jobs:
     cascade:
       uses: arthur-debert/release/.github/workflows/cascade-handler.yml@v2
       secrets:
         RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}
   ```

   The handler folds in the decide-and-dispatch gotchas (GH_TOKEN on
   every `gh`-using step, shell-injection guard on dispatch payload
   reads); the bump/tag/CHANGELOG gotchas now live in the reusable
   release workflow that `release.yml` calls. New repos onboard without
   re-deriving the gotcha list.

   Optional inputs:

   - `bump-kind` — `patch` (default) | `minor` | `major`.
   - `git-author-name` — defaults to `release-bot`.
   - `git-author-email` — defaults to `release-bot@users.noreply.github.com`.

   Run lookups happen under the **caller's** filename
   (`on-upstream-released.yml` by convention) — not under the reusable
   workflow's filename. `gh run list --repo <repo>
   --workflow=on-upstream-released.yml` is the standard way to see
   handler activity. See [`.github/workflows/cascade-handler.yml`](../.github/workflows/cascade-handler.yml).

   The older copy-per-repo `on-upstream-released.yml` shape (~120 lines)
   still works during the Wave-3 migration sweep but is being phased
   out.
3. **Add `notify-downstreams` step to `.github/workflows/release.yml`.** Fires `repository_dispatch upstream-released` to the new repo's direct consumers (if any).

Then add the repo to the `release-core admin release lex` orchestrator's `ORDER` array and dep-chain validation. Done.

## References

- Tracking issue: [lex-fmt/lex#640](https://github.com/lex-fmt/lex/issues/640)
- Orchestrator: `release-core admin release lex`
- Bootstrap (for fresh CI machines): [`arthur-debert/release/bin/clone-lex-repos`](../bin/clone-lex-repos)
- Reusable rust-cli workflow (lex consumes via `@v2`): [`arthur-debert/release/.github/workflows/rust-cli.yml`](../.github/workflows/rust-cli.yml)
