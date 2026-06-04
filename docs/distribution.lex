Distribution:
How release ships managed files into consumer repos

Source location: arthur-debert/release, path docs/. Describes the mechanism `bin/release-sync` implements (ADR-0001, ADR-0002) — validated against the script and the `tests/release-sync/` suite, not just intent. Read this before adding anything that consumers should receive.

1. The build-directory model

    Every managed file a consumer receives lives in a single build directory, `.release/`, checked into the consumer's git with real file content. The file at its expected working-tree location (`lefthook.yml`, `bin/changelog`, …) is a *symlink* into `.release/`. Both the build dir and the symlinks are committed.

    `.release/` is rebuilt from scratch on every sync — there is no state file and no removal manifest. The filesystem is the state. A template deleted upstream simply stops appearing in the rebuilt `.release/`; its symlink breaks; broken-symlink cleanup removes it. See ADR-0001.

    The symlink at the working-tree location is the signal: *this file is managed by release — don't edit it here.* Edits belong upstream, in the template.

2. What gets distributed

    Sync composes three template subtrees, low to high precedence (last write wins):

    Subtrees:
        - `templates/commons/` — the universal set, every consumer gets it regardless of Kind or Capabilities.
        - `templates/components/<capability>/` — one per Capability the consumer declares (Kind manifest, or a `.release-sync.yaml` override).
        - `templates/<kind>/` — the consumer's Kind subtree.

    A file's destination in `.release/` is its path with the subtree prefix stripped: `templates/commons/bin/changelog` becomes `.release/bin/changelog`. `lefthook.yml` is the one composed file — assembled from each subtree's `lefthook.fragment.yaml` in the same precedence order, not copied from a single source.

3. The materialize-then-mirror cycle

    For each sync (`bin/release-sync`):

    a. Resolve Kind (`detect-kind`) and Capabilities.
    b. Build the new `.release/` tree in a tempdir by `git show`-ing every file from the composed subtrees at the selected ref.
    c. For each file in `.release/<dest>`, ensure a *relative* symlink exists at `<dest>` in the working tree, pointing into `.release/` (e.g. `bin/changelog -> ../.release/bin/changelog`).
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
        bin/changelog -> ../templates/commons/bin/changelog

    :: text ::

    There is exactly one copy (the template); the maintainer symlink and the consumer's `.release/` copy both point at the same source. A tool authored as a real file at repo-root `bin/` is, by definition, *not* distributed — it is maintainer-only until moved under a template.

6. Worked example: the changelog shim

    `changelog` is a thin Python shim plus the `release_core` package it imports. It exercises the canonical-home + sync-distribution rules above:

    Layout:
        - `templates/commons/bin/changelog` — the shim (canonical home).
        - `templates/commons/lib/release_core/` — the package (canonical, single copy; also the uv-workspace member and pytest target). NB: the package itself now ships to consumers by **pip wheel**, not sync (the bin/ shim is the action-download / no-pip path).
        - `bin/changelog -> ../templates/commons/bin/changelog` — the maintainer symlink.

    The shim finds its package by its own realpath:

        sys.path.insert(0, realpath(__file__)/../lib/release_core)

    :: text ::

    That one relative path resolves correctly from both sides, because both entry points are symlinks resolving into the same layout:

    Resolution:
        | Caller                          | realpath of the shim        | ../lib/release_core resolves to        |
        | maintainer `bin/changelog`      | `templates/commons/bin/...` | `templates/commons/lib/release_core`   |
        | consumer `.release/bin/changelog` | `.release/bin/...`        | `.release/lib/release_core`            |
    :: table align=lll ::

    The package is shielded by `is_release_internal` (`lib/release_core/*` — scoped to the package, so other `lib/` paths like the bats harness still mirror), so a consumer gets `.release/lib/release_core/` (real files the shim loads) but no `lib/release_core/...` symlinks in its tree.

    (Historical note: the PR state engine `gh-task-status` was originally distributed exactly this way — a `release_gh` package synced into `.release/lib/release_gh/`. It was folded into `release_core` and now ships purely as a pip console-script, retiring its sync shim entirely; release#459.)

7. Provenance and drift

    Each `.release/` carries `.release-sync-source` — the exact release revision that generated it (ADR-0002). `release-drift-check` rebuilds against that recorded revision, so it distinguishes real drift (a consumer hand-edited a managed file) from mere staleness (the consumer simply hasn't re-synced). See `docs/proposals/301-consumer-drift-gate-rollout.md`.

Notes

    1. To make something reach consumers: put its canonical copy under a template subtree (usually `templates/commons/`), and — if it is a runtime dependency rather than a used-at-a-location file — shield it with `is_release_internal`. Validate with a sync into a throwaway consumer; add a `tests/release-sync/` case.
