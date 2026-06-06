Injected Files

    `release/` manages a set of files inside each consumer repo: lint configs,
    the gate definition, the bin/ tooling, the CI workflow callers, and the
    agent-orientation block. This document maps every managed file — where it
    comes from, where it lands, and how it gets there.

    The guiding rule: a consumer repo should contain as little mixed-ownership
    state as possible. Mixed ownership (a file half-owned by release, half by
    the consumer) is the single biggest source of confusion, so the mechanism
    is built to keep the managed surface small, obvious, and non-overlapping.

1. The Mechanism: build-dir + symlinks

    `release-sync` (consumer alias for `release-core sync run`) is the
    materializer. ADRs 0001 and 0002 define it. It works in three moves:

    - Materialize: it writes ALL managed files, from scratch, into a fresh
      `.release/` directory at the consumer root. `.release/` is committed, so
      the consumer is self-contained and works offline.
    - Mirror: at each managed path in the working tree it creates a relative
      symlink pointing into `.release/` (e.g. `bin/changelog` →
      `.release/bin/changelog`). The exception is `.github/workflows/*.yml`,
      written as real copies because GitHub Actions cannot follow symlinks.
    - Reconcile removals: a managed symlink whose `.release/` target no longer
      exists is a broken link and gets swept; a stale managed workflow copy
      (identified by its managed-marker header) is removed.

    There is no state file. The filesystem is the truth.
    `.release/.release-sync-source` records the release commit the tree was
    built from — provenance only, informational (ADR-0002). It is what
    `release-drift-check` rebuilds against to tell real drift from mere
    staleness.

    Sources read by sync, in layers:
        - `templates/commons/` — every consumer gets these.
        - `templates/{kind}/` — per-Kind (e.g. `rust-cli`, `go-cli`,
          `electron-app`); selected by the repo's detected Kind.
        - `templates/components/{capability}/` — opt-in capabilities declared
          in the consumer's `.release-sync.yaml` (or defaulted by the Kind
          manifest).

2. What Lands Where

    The map below is source (in this repo) → destination (in the consumer) →
    kind. "Symlink" means a relative link into `.release/`; "copy" means a
    real file; "internal" means it lives only inside `.release/` and is never
    mirrored to a working-tree path.

    From templates/commons (every consumer):
        | Source | Destination | Kind |
        | bin/setup-dev-env.sh | bin/setup-dev-env.sh | symlink |
        | bin/install-release-core | bin/install-release-core | symlink |
        | bin/check-shell | bin/check-shell | symlink |
        | bin/check-gate | bin/check-gate | symlink |
        | bin/changelog, changelog-add/-cut/-render | bin/changelog* | symlink |
        | bin/semver | bin/semver | symlink |
        | .markdownlint.json, .markdownlintignore | (root) | symlink |
        | .yamllint, .shellcheckrc, .prettierignore | (root) | symlink |
        | .editorconfig | .editorconfig | symlink |
        | .claude/settings.json | .claude/settings.json | symlink |
        | lefthook.fragment.yaml | merged into .release/lefthook.yml | fragment |
        | ORIENTATION.md | .release/ORIENTATION.md | internal |
        | lib/release_core/** | (not synced) | wheel |
    :: table ::

    From templates/{kind} (per-Kind):
        | Source | Destination | Kind |
        | .github/workflows/*.yml | .github/workflows/*.yml | copy |
        | .github/CODEOWNERS | .github/CODEOWNERS | symlink |
        | .github/dependabot.yml | .github/dependabot.yml | symlink |
        | .github/pull_request_template.md | (same) | symlink |
        | bin/* (build/check tools) | bin/* | symlink |
        | lefthook.fragment.yaml | merged into .release/lefthook.yml | fragment |
        | manifest.yaml | (not a file; capability defaults) | source |
    :: table ::

    Skills:
        | Source | Destination | Kind |
        | skills/gh-pr-review-loop/SKILL.md | .claude/skills/gh-pr-review-loop/SKILL.md | symlink |
    :: table ::

    The gate (`lefthook.yml`) is never copied verbatim. It is composed by
    deep-merging the fragments in order — base, commons, each capability, then
    the Kind — into one `.release/lefthook.yml`. To change a check, edit a
    fragment, never a consumer's gate. See the "ONE gate, run everywhere"
    rule in CLAUDE.md.

3. Two Files That Are Not Symlinks

    The CLAUDE.md managed block:
        Orientation reaches the consumer agent through a small managed block
        injected at the top of the consumer's `CLAUDE.md`.

        The block:

            <!-- BEGIN release-managed orientation -->
            @.release/ORIENTATION.md
            <!-- END release-managed orientation -->

        :: text ::

        It is a block, not a whole file, because half the fleet already owns a
        `CLAUDE.md` with its own project content; sync injects or refreshes the
        block and leaves the rest untouched. If `CLAUDE.md` is itself a
        symlink, sync leaves it alone. The block `@`-imports
        `.release/ORIENTATION.md`, which is the actual orientation text
        (what's managed, how to open an issue, where to escalate). That file
        lives only inside `.release/`.

    The workflow callers:
        `.github/workflows/*.yml` are real copies with a managed-marker header,
        because GitHub Actions resolves workflow files before any checkout
        symlinks could be followed. They are thin callers — a `uses:` line
        into this repo's reusable workflows (see workflows.lex) — so the copy
        is small and the real logic stays upstream.

4. Session-Start Ordering

    `.claude/settings.json` wires a `SessionStart` hook that runs
    `bin/setup-dev-env.sh`. That script is the single definition of how a repo
    boots, for both local dev and cloud sessions. Order:

    - §0 — install the gate toolset (lefthook, ruff, yamllint, shellcheck,
      markdownlint, and language linters as detected). Best-effort to install,
      but the gate itself is HARD: a missing tool fails the commit, never
      skips.
    - §0.1 — wire the pre-commit hook (`lefthook install`).
    - §0.2 — the pull-model boot (ADR-0003): run `bin/install-release-core`,
      which resolves the `release_core` wheel from a GitHub release,
      `pip install --force-reinstall`s it (deps from PyPI), then runs
      `release-core init` to materialize and commit the managed config subset.
    - §1+ (cloud only) — submodule/tag restore, dependency cache warm-up, venv
      setup, CA cert import, optional per-repo `app-bin/post-setup-hook.sh`.

    What must exist before a session starts: the bootstrap entry points
    themselves — `bin/setup-dev-env.sh`, `bin/install-release-core`, and
    `.claude/settings.json`. They ship in the committed bootstrap (synced from
    `templates/commons/bin/`), so a freshly cloned consumer can boot before
    `release_core` is installed. Everything else (the gate tools, the
    `release_core` package, the cloud dependency caches) is pulled or installed
    at session start.

    :: note :: The `release_core` Python package is NOT a synced file. It ships
    as a wheel from a GitHub release and is pip-installed at boot. The synced
    surface is the thin bootstrap + configs + workflow callers; the engine
    arrives out-of-band. See tooling.lex §4.
