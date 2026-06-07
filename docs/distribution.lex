Distribution:
How release ships managed files into consumer repos

Source location: arthur-debert/release, path docs/. Describes the mechanism `release-sync` implements (the `release_core.sync` engine; ADR-0001, ADR-0002) — validated against the code and the `tests/release-sync/` suite, not just intent. Read this before adding anything that consumers should receive.

1. The build-directory model

    Every managed file a consumer receives lives in a single build directory, `.release/`, checked into the consumer's git with real file content. The file at its expected working-tree location (`lefthook.yml`, `bin/check-shell`, …) is a *symlink* into `.release/`. Both the build dir and the symlinks are committed.

    `.release/` is rebuilt from scratch on every sync — there is no state file and no removal manifest. The filesystem is the state. A template deleted upstream simply stops appearing in the rebuilt `.release/`; its symlink breaks; broken-symlink cleanup removes it. See ADR-0001.

    The symlink at the working-tree location is the signal: *this file is managed by release — don't edit it here.* Edits belong upstream, in the template.

2. What gets distributed

    Sync composes three template subtrees, low to high precedence (last write wins):

    Subtrees:
        - `templates/commons/` — the universal set, every consumer gets it regardless of Kind or Capabilities.
        - `templates/components/<capability>/` — one per Capability the consumer declares (Kind manifest, or a `.release-sync.yaml` override).
        - `templates/<kind>/` — the consumer's Kind subtree.

    A file's destination in `.release/` is its path with the subtree prefix stripped: `templates/commons/bin/check-shell` becomes `.release/bin/check-shell`. `lefthook.yml` is the one composed file — assembled from each subtree's `lefthook.fragment.yaml` in the same precedence order, not copied from a single source.

3. The materialize-then-mirror cycle

    For each sync (`release-sync`, i.e. `release-core sync run`):

    a. Resolve Kind (`detect-kind`) and Capabilities.
    b. Build the new `.release/` tree in a tempdir by `git show`-ing every file from the composed subtrees at the selected ref.
    c. For each file in `.release/<dest>`, ensure a *relative* symlink exists at `<dest>` in the working tree, pointing into `.release/` (e.g. `bin/check-shell -> ../.release/bin/check-shell`).
    d. Walk the repo for symlinks pointing into `.release/` that are now broken, and delete them.

    Sync reads templates from a git ref, not the working tree (`git show "$ref:$path"`). So a change is only distributed once committed. The ref is chosen as: `$RELEASE_REF` if set, else a per-repo or per-Kind `release/beta/*` branch, else `origin/main`.

4. Two exceptions to the symlink rule

    Most managed files are symlinks. Two cases are not:

    4.1. Real-file copies — `needs_real_file`

        Some consumers of a file don't dereference symlinks. The known case is `.github/workflows/*`: GitHub reads workflow YAML directly from the git tree and treats a symlink blob as the literal target string, which fails to parse and silently breaks every workflow in the directory. release-sync writes these as *real-file copies* carrying a managed-marker header comment, so stale copies are still detectable and removable on later syncs.

    4.2. Release-internal content — `is_release_internal`

        Some content must live in `.release/` but must *not* be mirrored out to a working-tree location. It is part of the tree (so `--check` sees it change) but no symlink/copy is created for it. Two kinds:

        - The provenance marker (`.release-sync-source`) — records the source revision (ADR-0002); read by `release-drift-check`, never used at a consumer location.
        - `lib/release_core/*` — the Python core package (the verb layer plus the folded PR state engine, `release_core.prstate`; release#459). It ships to consumers by **pip wheel**, not sync — but when present in the tree it must exist in `.release/lib/` as a real-file internal dependency, never mirrored out to a working-tree location. The match is scoped to `lib/release_core/`, *not* all of `lib/`: other `lib/` paths are consumer-facing and must mirror (the bats Capability ships `lib/bats-harness.bash`, which consumer test files source).

5. The canonical-home pattern for tools

    A tool that ships to consumers has its single source of truth *inside* `templates/commons/bin/` (or the relevant subtree) — not at repo-root `bin/`. The maintainer gets it on `$PATH` because repo-root `bin/<tool>` is a *symlink* into the template:

    Example:
        bin/install-release-core -> ../templates/commons/bin/install-release-core

    :: text ::

    There is exactly one copy (the template); the maintainer symlink and the consumer's `.release/` copy both point at the same source. A tool authored as a real file at repo-root `bin/` is, by definition, *not* distributed — it is maintainer-only until moved under a template.

6. The changelog / semver family: pip console-scripts, not shims

    `changelog`, `changelog-add`, `changelog-cut`, `changelog-render` and `semver` are NO LONGER distributed as `bin/` sys.path shims. They are `release_core` **pip console-scripts**, declared in the package's `[project.scripts]` and installed when the wheel is installed (`install-release-core` at SessionStart, or `bin-internal/install-release-core-pkg.sh` in release CI).

    History: until release#476 they were thin Python shims at `templates/commons/bin/{changelog*,semver}` that put `release_core` on `sys.path` via their own realpath (`../lib/release_core`). The shims survived the first console-script cutover only because the release composite actions + `gh-action.yml` + `bin-internal/*` exec'd them BY FILE PATH from the action checkout, where the wheel was not pip-installed. release#476 fixed those call-sites to pip-install `release_core` and call the console-scripts by name, and deleted the shims.

    Consequence for `.release/lib`: with the changelog/semver shims gone, NO file synced to a consumer resolves `.release/lib/release_core` anymore — `release_core` reaches consumers purely by pip wheel. (The remaining `release_core` sys.path shims — `bin/release-sync`, `bin/release-core`, `bin/detect-kind`, `bin/release-drift-check` — are maintainer-only repo-root `bin/` tools that resolve `../templates/commons/lib/release_core` in the release checkout; they are never synced into a consumer's `.release/`.) This unblocks stripping `lib/release_core/*` from the synced `.release/` tree.

7. Provenance and drift

    Each `.release/` carries `.release-sync-source` — the exact release revision that generated it (ADR-0002). `release-drift-check` rebuilds against that recorded revision, so it distinguishes real drift (a consumer hand-edited a managed file) from mere staleness (the consumer simply hasn't re-synced).

Notes

    1. To make something reach consumers: put its canonical copy under a template subtree (usually `templates/commons/`), and — if it is a runtime dependency rather than a used-at-a-location file — shield it with `is_release_internal`. Validate with a sync into a throwaway consumer; add a `tests/release-sync/` case.
