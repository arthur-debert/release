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

## The three layers

The automation is built in three layers. Each layer is independently useful and can be debugged in isolation.

### Layer 0 — per-repo primitive scripts

Each of the six repos ships five executable scripts under `scripts/release/`:

| Script | Args | Stdout | Exit | Side effects |
|---|---|---|---|---|
| `get-current-version` | none | bare semver (e.g. `0.14.0`, no `v`) | 0 / non-zero on error | none |
| `get-commits-since-release` | none | one line per commit since the latest `vX.Y.Z` tag (`<short-sha> <subject>`); empty if none | 0 always | none |
| `should-release` | none | `yes: <reason>` or `no: <reason>` | 0=yes, 1=no, 2+=error | none |
| `update-release <new-version>` | bare semver | summary | 0 / non-zero on error | bumps manifests, comms submodule, dep pins, CHANGELOG; runs `git add` |
| `trigger-release <new-version>` | bare semver | summary | 0 / non-zero on error | fires this repo's release CI (tag-push for most repos; `gh workflow run release.yml -f version=...` for lex) |

The orchestrator and the event-cascade handler both compose the same five primitives. Adding a new repo to the chain means writing these five scripts.

#### Per-repo manifest surfaces

| Repo | Version source | Dep updates in `update-release` | Trigger mode |
|---|---|---|---|
| `comms` | git tags (no version file) | none | tag-push (`on: push: tags: [v*]`) |
| `lex` | `[workspace.package].version` in root `Cargo.toml` | `comms` submodule | **`workflow_dispatch`** (delegates to `arthur-debert/release/rust-cli@v1`) |
| `tree-sitter-lex` | `package.json` `"version"` | `comms` submodule | tag-push |
| `vscode` | `package.json` `"version"` | `comms` + `shared/lex-deps.json` (flat) | tag-push |
| `nvim` | `M.version = "..."` in `lua/lex/init.lua` | `comms` + `shared/lex-deps.json` (flat) | tag-push |
| `lexed` | `package.json` `"version"` | `comms` + `shared/lex-deps.json` (nested) | tag-push |

### Layer 1 — local orchestrator (`release-lex`)

`release-lex` in `arthur-debert/release/bin/` walks the dep chain locally and runs each repo's primitives in sequence. Useful for:

- **Local debugging** — surface primitive bugs in isolation (the `--dry-run` mode echoes every step without making changes).
- **Recovery** — re-run from a known-good point if a cascade gets wedged mid-flight.
- **One-button kickoff** — `release-lex patch --comms ... --lex ...` cuts the whole chain locally without involving GH events.

Day-to-day, you don't need it. The event cascade (Layer 2) does the same work hands-off.

```sh
release-lex patch \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed

# Status only — what would cascade if I cut comms now?
release-lex --status \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed
```

### Layer 2 — event-driven cascade (the default)

Every repo's `release.yml` ends with a `notify-downstreams` step that fires `repository_dispatch` events to its direct consumers. Every downstream repo has a `.github/workflows/on-upstream-released.yml` handler that:

1. Receives the dispatch
2. Runs `scripts/release/should-release` → if yes, computes a patch-bump and runs the rest of the chain (update-release → commit → PR + admin-merge → trigger-release)
3. Cutting this repo's release fires its own `notify-downstreams` → fans further down the chain

#### The cascade flow

```
push tag v0.16.3 to comms
  → comms/release.yml runs (specs.tar.gz, assets.tar.gz, GH release)
    → notify-downstreams fires repository_dispatch upstream-released
        → lex/on-upstream-released.yml
            → should-release: yes (comms submodule stale)
            → update-release → commit + PR + admin-merge → workflow_dispatch
                → rust-cli@v1 publishes crates, builds binaries, tags v0.x.y
                    → lex/release.yml notify-downstreams fires upstream-released to editors
        → tree-sitter-lex/on-upstream-released.yml
            → ... same shape ... → tag-push v0.x.y
                → notify-downstreams fires to editors
                    → vscode/nvim/lexed handlers each fire
                        → should-release: yes (multiple pins stale)
                        → cut their own release
```

#### Two-hop cascade for editors

Editors submodule `comms` AND pin `lexd-lsp` + `tree-sitter`. When comms releases, the editors *could* react directly — but their `lexd-lsp` and `tree-sitter` pins would still be on the old upstream version. Better to wait for lex + tree-sitter to release first; their `update-release` will pull a current comms anyway. So:

- comms only emits to **lex + tree-sitter-lex** (NOT editors).
- lex + tree-sitter-lex emit to **vscode + nvim + lexed**.
- Editors get two events (one from lex, one from tree-sitter). Each handler re-checks all pins via `should-release`. Whichever event arrives later wins.

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
release-lex --status --comms ../comms --lex ../lex \
  --tree-sitter ../tree-sitter-lex --vscode ../vscode \
  --nvim ../nvim --lexed ../lexed
```

Runs `should-release` against each repo, prints a one-line answer per repo. Read-only. No state changes.

### Local one-button cut (Layer 1)

If you want to drive the cascade locally instead of using GH events (e.g. debugging primitive changes), use the orchestrator:

```sh
release-lex patch \
  --comms ../comms --lex ../lex --tree-sitter ../tree-sitter-lex \
  --vscode ../vscode --nvim ../nvim --lexed ../lexed
```

Add `--dry-run` to echo every step without doing anything. Add `--only repo1,repo2` to restrict.

### Recovery: a handler failed mid-cascade

The cascade is idempotent at the should-release level — if a handler failed before tagging, you can re-fire the same dispatch and it'll resume cleanly. If a handler failed after tagging but before the release CI finished, the next attempt will see `should-release: no` (everything's caught up) and exit cleanly; you only need to re-fire the *release CI* (e.g. `gh workflow run release.yml --repo lex-fmt/lex -f version=X.Y.Z`).

Manual dispatch fire (handler will re-evaluate):

```sh
gh api repos/lex-fmt/<downstream>/dispatches --method POST \
  -f event_type=upstream-released \
  -F 'client_payload[source]=<upstream>' \
  -F 'client_payload[version]=v<X.Y.Z>'
```

## Gotchas (real bugs found during the rollout)

These all appeared at least once during the cascade's first cut. Documented so future sessions don't re-discover them.

### Workflow gotchas

1. **`GH_TOKEN` on every `gh`-using step.** The handler's `Decide` and `Branch + bump + commit` steps invoke `should-release` / `update-release`, both of which call `gh release view` etc. Without `env: GH_TOKEN: ${{ secrets.RELEASE_TOKEN }}` on those steps, `gh` fails auth and the handler bails.
2. **Shell-injection on payload reads.** `${{ github.event.client_payload.X }}` interpolated directly into a `run:` is a code-execution vector if the upstream is compromised. Always route through `env:`.
3. **Annotated tags, not lightweight.** `release.yml` workflows read the tag message via `git tag -l --format='%(contents)'`. A lightweight tag (`git tag v...`) has an empty body; the release notes come out blank. The `trigger-release` primitive uses `git tag -a` annotated; manual cuts must too.
4. **`--allow-same-version` on `npm version`.** vscode's release.yml runs `npm version <tag>`. The orchestrator/handler pre-bumps package.json, so `npm version` errors with "Version not changed" unless `--allow-same-version` is set.
5. **`--admin` flag bypasses ruleset.** Release PRs are pure chore (version + deps + CHANGELOG); driving them through full Copilot review is overhead. The handler/orchestrator uses `gh pr merge <pr> --admin --squash --delete-branch` since the workflow has admin scope via `RELEASE_TOKEN`.
6. **`git reset --hard origin/main` after the admin-merge.** Some repos' pre-commit hooks regenerate state during the commit step (`tree-sitter-lex`'s hook regenerates `src/parser.c`; lexed's husky touches build artifacts). The regenerated state is unstaged, so a subsequent `git pull --ff-only` fails. Reset discards the side effects and fast-forwards in one step.

### Versioning gotchas

7. **Manifest may drift behind tags.** `get-current-version` reads the manifest; if past releases were cut without bumping the manifest, the next computed version may collide with a real existing tag. `tree-sitter-lex` hit this — `package.json` said `0.8.0` while the highest tag was `v0.10.1`, so `patch` bumped to a long-since-released `v0.8.1`. Workaround: pass an explicit `X.Y.Z` to the orchestrator. Long-term: `get-current-version` should max(manifest, latest GH release).
8. **Resume-on-existing-tag in `arthur-debert/release/.github/actions/prepare-release`.** When lex's reusable workflow sees an existing tag matching the requested version, it validates the manifest matches and skips the bump+commit+tag. That's how the local orchestrator's "I already pre-bumped" pre-cut composes cleanly with lex's workflow-driven model.

### CHANGELOG / UNRELEASED.md gotchas

9. **CHANGELOG format must be Keep-a-Changelog.** `update-release` for comms/lex requires `## [Unreleased]` at the top. Repos with older `## vX.Y.Z (date)` format need a one-time migration.
10. **Empty `UNRELEASED.md` is a hard-fail in some repos.** nvim's `update-release` errors if `UNRELEASED.md` is empty; cascade runs have no human-authored notes. The handler seeds a bullet (`- Triggered by upstream release of X@Y.`) before calling `update-release`.

## Adding a new repo to the cascade

Three pieces. None is hard individually; the order matters.

1. **Write the five `scripts/release/` primitives.** Each one is ~30-80 lines of bash. Copy a similar repo's implementation as a starting point. Test each in isolation.
2. **Add `.github/workflows/on-upstream-released.yml`.** Use the
   reusable workflow (recommended) — a 6-line thin caller:

   ```yaml
   name: On upstream released

   on:
     repository_dispatch:
       types: [upstream-released]

   jobs:
     cascade:
       uses: arthur-debert/release/.github/workflows/cascade-handler.yml@v1
       secrets:
         RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}
   ```

   The reusable workflow folds in every gotcha listed earlier
   (GH_TOKEN on every primitive step, admin-merge, post-merge
   `git reset --hard`, submodule restore, manifest-vs-tag drift guard,
   stale-release-branch cleanup, UNRELEASED.md seed, shell-injection
   guard on dispatch payload reads). New repos onboard without
   re-deriving the gotcha list.

   Optional inputs:

   - `bump-kind` — `patch` (default) | `minor` | `major`.
   - `git-author-name` — defaults to `release-bot`.
   - `git-author-email` — defaults to `release-bot@users.noreply.github.com`.

   Run lookups happen under the **caller's** filename
   (`on-upstream-released.yml` by convention) — not under the reusable
   workflow's filename. `gh run list --repo <repo>
   --workflow=on-upstream-released.yml` is the canonical way to see
   handler activity. See [`.github/workflows/cascade-handler.yml`](../.github/workflows/cascade-handler.yml).

   The older copy-per-repo `on-upstream-released.yml` shape (~120 lines)
   still works during the Wave-3 migration sweep but is being phased
   out.
3. **Add `notify-downstreams` step to `.github/workflows/release.yml`.** Fires `repository_dispatch upstream-released` to the new repo's direct consumers (if any).

Then add the repo to `release-lex`'s `ORDER` array and dep-chain validation. Done.

## References

- Tracking issue: [lex-fmt/lex#640](https://github.com/lex-fmt/lex/issues/640)
- Orchestrator: [`arthur-debert/release/bin/release-lex`](../bin/release-lex)
- Bootstrap (for fresh CI machines): [`arthur-debert/release/bin/clone-lex-repos`](../bin/clone-lex-repos)
- Reusable rust-cli workflow (lex consumes via `@v1`): [`arthur-debert/release/.github/workflows/rust-cli.yml`](../.github/workflows/rust-cli.yml)
