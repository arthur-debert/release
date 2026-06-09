release-core, the gate, distribution, and workflows

    The machinery layer of `release/`: one CLI an agent drives, one quality
    gate it runs, one sync engine that materializes managed files into a
    consumer, and one set of reusable GitHub Actions workflows. This document
    is the single home for all four — it absorbs the former `tooling`,
    `distribution`, `workflows`, and the mechanism half of `injected-files`,
    plus the directional roadmap.

    The agent-facing surface (orientation, skills, the session boot) lives in
    `harness.lex`. The "why" decisions live under `adr/`.

1. release-core — the one CLI

    `release-core` is the single CLI for every infrastructure and dev-cycle
    task: the quality gate, the changelog, syncing managed files, driving a PR,
    cutting a release. It is a `click` command tree, so the help IS the map —
    an agent learns it by discovery, not by memorizing:

    - `release-core --help` — the top-level groups.
    - `release-core <group> --help` — a group's subcommands.
    - `release-core <group> <command> --help` — one command's flags.

    The tree is rich; you are not expected to know it cold. When in doubt,
    `--help` your way down. The flat maintainer command names that used to
    exist (`release-verify-fleet`, `managed-repos`, `release-cut`, …) were
    retired in the CLI cutover (#468) — use `release-core <group> <command>`.
    A few flat consumer aliases remain (see [#1.3]).

    1.1. Consumer-facing commands

        Run from inside any repo:

        - `release-core how-to` — the kind-aware task playbook (lint / test /
          build / release / run + the draft-first dev cycle). The single source
          of procedural truth — see `harness.lex` and `dev-cycle.lex`.
        - `release-core gate` — run the quality gate (see [#2]).
        - `release-core init` — DEFAULT (post-#476): materialize the WHOLE
          managed tree from the wheel bundle (the `.release/` build dir + every
          working-tree mirror — skills, ORIENTATION, configs, the CLAUDE.md
          block) and auto-commit any managed change. This is "release-sync
          sourced from the wheel"; it is what SessionStart runs, carrying the
          full tree so consumers self-update by pull — there is no push step.
          `--config-only` is the escape hatch — materialize just the config
          subset (lefthook.yml + lint configs). (`--full` is a redundant alias
          of the default.)
        - `release-core sync run` / `sync drift-check` — materialize the
          `.release/` tree ([#5]) / fail if it has drifted.
        - `release-core changelog add|cut|render` — manage the changelog.
        - `release-core semver validate|get` — validate or read a version part.
        - `release-core detect-kind` — report this repo's release Kind.
        - `release-core cut` — cut a release for this repo.
        - `release-core audit` — audit this repo's release posture.
        - `release-core issue file <component> "<symptom>"` — escalate infra
          friction upstream to `arthur-debert/release`.
        - `release-core pr …` — the PR-loop helpers ([#3]).
        - `release-core ci fetch-deps|fetch-artifact` — CI-glue fetch helpers.

    1.2. Maintainer-only commands

        Run from inside `arthur-debert/release`:

        - `release-core admin repos list|prs|audit|verify` — fleet views and
          the hermetic pre-flight sweep.
        - `release-core admin release advance-major|betas|lex` — release-side
          mechanics; `advance-major` fast-forwards the floating major branch.
        - `release-core admin policy ruleset|sweep|dependabot` — GitHub policy.
        - `release-core admin secrets install|token` — provision release
          secrets onto a repo.
        - `release-core admin inbox [notify-source]` — the consumer-filed
          issue triage inbox and the close-the-loop notifier.

    1.3. The flat consumer aliases

        A short list of flat command names is kept on PATH for consumers, each a
        thin alias into the tree:

            | Alias | Does |
            | changelog | manage the changelog (add/cut/render) |
            | semver | validate or extract a version part |
            | detect-kind | detect this repo's release Kind |
            | release-sync | materialize the .release/ tree + symlinks |
            | release-drift-check | fail if .release/ has drifted from source |
            | gh-task-status | the PR state machine (state + next action) |
            | gh-release-issue | file or comment on a release issue upstream |
        :: table ::

        These reach a consumer's PATH as `release_core` pip console-scripts
        (from the installed wheel), not as synced `bin/` shims. The old
        `release` shim was retired in #476 — cut a release with `release-core
        cut`.

2. The quality gate — release-core gate

    `release-core gate` is the ONE quality entry. It wraps `lefthook run
    pre-commit --all-files`, so green here == green in CI, with no false-green
    on unstaged files. It sets `LEFTHOOK_CONFIG=.release/lefthook.yml` when
    present, so it survives dropping the root discovery symlink (the
    footprint-min end state, #501).

    It is a HARD gate: a missing tool exits non-zero (a setup failure, never a
    skip), and `--no-verify` is never an acceptable workaround — CI re-runs the
    same gate on a clean runner where the tools are guaranteed.

    The gate is the fast lint / format / static set, *not* tests. A green gate
    is necessary but not sufficient for CI green: CI runs the test suite as a
    separate required check, so run the repo's `test` verb yourself before
    pushing.

    The gate is ONE definition (`lefthook.yml`, composed from fragments —
    [#5.2]) run everywhere: session start arms it, local commits run it, CI runs
    the same `lefthook run pre-commit --all-files`. To add or change a check,
    edit a fragment — never hand-copy a check into a CI job. See the "ONE gate,
    run everywhere" rule in CLAUDE.md.

3. The PR cycle, driven by the state machine

    The PR loop is not a checklist an agent eyeballs — it is a state machine.
    `release-core pr status` (flat alias `gh-task-status`) reads the live PR and
    returns a state plus the single next action. It never mutates the PR; the
    agent acts on the next action, then re-reads.

    The states:
        - NO_PR — no PR for this branch; open a draft to start.
        - REVIEWS_PENDING — required reviewer(s) not done; request or wait.
        - ADDRESSING — reviews in, open threads remain; fix-or-reply, then
          resolve.
        - REVIEWED — reviews + threads done; mergeability still computing,
          re-check shortly.
        - VALIDATING — reviewed + mergeable; CI checks running, wait.
        - READY — reviewed + CI green + mergeable; flip draft→ready and page
          the human.
        - BLOCKED — merge conflict, failing CI, or the loop's circuit breaker
          fired; stop and surface to the human.

    The happy-path command sequence for one PR:
        - `release-core pr status` — where am I?
        - `release-core pr copilot on` — request the Copilot review
          (REVIEWS_PENDING).
        - `release-core pr copilot wait` — block in-turn until the review lands.
        - `release-core pr status` — now ADDRESSING; triage the threads.
        - `release-core pr resolve-thread` — resolve addressed threads.
        - `release-core pr checks-wait` — if VALIDATING, block until CI is green.
        - `release-core pr status` — READY → flip draft→ready, hand to the
          human.

    This whole loop is the `gh-pr-review-loop` skill's discipline; drive it
    through the skill, not by hand-composing the helpers (a PreToolUse guard
    enforces this — see CLAUDE.md and `dev-cycle.lex`).

    :: warning :: The wait commands (`pr copilot wait`, `pr checks-wait`) block
    in-turn — they are how an agent waits on CI without yielding. A subagent
    that yields to a background monitor terminates and is never re-woken. Drive
    the loop through `pr status` (state + next action) and block with the wait
    commands; do not hand the wait to a detached background process.

4. Install — the pull model

    Install is a pull model (ADR-0003), run by `bin/install-release-core` at
    every session start:

    - It resolves exactly one `release_core-*.whl` asset from a GitHub release —
      `releases/latest` by default, or the latest in a pinned major line with
      `--major vN` (the safety filter, since the wheel's own version string is
      static).
    - It installs with `pip install --force-reinstall` (NOT `-U`, which would
      see the static version as already-satisfied and skip). Dependencies (e.g.
      `click`) resolve from PyPI — the wheel declares real deps.
    - It then runs a bare `release-core init` in the repo (best-effort).

    So a consumer gets a NEW `release_core` automatically on its next session
    start (or next CI run) — there is nothing to commit to update the engine.
    The pull model keeps consumers on the always-stable tip without per-repo
    bump PRs.

    What DOES ride in the consumer's git tree is the managed TREE — the
    `.release/` build dir + every working-tree mirror (skills, ORIENTATION,
    configs, the CLAUDE.md block). A bare `release-core init` materializes that
    whole tree from the wheel bundle and AUTO-COMMITS only the managed paths it
    touched (never `git add -A`), with a deterministic message, iff they
    actually changed — byte-identical → no commit, so churn tracks release
    cadence, not session count. The engine is pulled, the whole tree it
    generates is committed — so the wheel pull alone carries every managed
    change and there is no push step. `--no-commit` skips the commit; `--push`
    additionally fast-forwards on a clean default branch.

    :: note :: The minimal-footprint direction (epic #501, ADR-0005) shrinks
    this tracked tree toward zero — `.release/` becomes gitignored and
    regenerated each session, and orientation moves into `release-core how-to`
    output rather than synced files. This document describes the model as it
    ships today (committed `.release/` + symlinks, ADR-0004); the roadmap is in
    [#8].

5. Distribution — release-sync (build-dir + symlinks)

    `release-sync` (consumer alias for `release-core sync run`) is the
    materializer that writes managed files into a consumer. ADRs 0001 and 0002
    define it; it is validated against the `tests/release-sync/` suite, not just
    intent. Read this before adding anything consumers should receive.

    The guiding rule: a consumer repo should contain as little mixed-ownership
    state as possible. Mixed ownership (a file half-owned by release, half by
    the consumer) is the single biggest source of confusion, so the mechanism
    keeps the managed surface small, obvious, and non-overlapping.

    5.1. The build-directory model

        Every managed file a consumer receives lives in a single build
        directory, `.release/`, checked into the consumer's git with real file
        content. The file at its expected working-tree location (`lefthook.yml`,
        `bin/check-shell`, …) is a *symlink* into `.release/`. Both the build
        dir and the symlinks are committed, so the consumer is self-contained
        and works offline.

        `.release/` is rebuilt from scratch on every sync — there is no state
        file and no removal manifest. The filesystem is the state. A template
        deleted upstream simply stops appearing in the rebuilt `.release/`; its
        symlink breaks; broken-symlink cleanup removes it (ADR-0001).

        The symlink at the working-tree location is the signal: *this file is
        managed by release — don't edit it here.* Edits belong upstream, in the
        template.

    5.2. What gets distributed

        Sync composes three template subtrees, low to high precedence (last
        write wins):

        Subtrees:
            - `templates/commons/` — the universal set; every consumer gets it
              regardless of Kind or Capabilities.
            - `templates/components/<capability>/` — one per Capability the
              consumer declares (Kind manifest, or a `.release-sync.yaml`
              override).
            - `templates/<kind>/` — the consumer's Kind subtree.

        A file's destination in `.release/` is its path with the subtree prefix
        stripped: `templates/commons/bin/check-shell` becomes
        `.release/bin/check-shell`. `lefthook.yml` is the one composed file —
        deep-merged from each subtree's `lefthook.fragment.yaml` in precedence
        order (base, commons, each capability, then the Kind), not copied from a
        single source. To change a check, edit a fragment, never a consumer's
        gate.

        What lands where (source in this repo → destination in the consumer →
        kind; "symlink" = relative link into `.release/`, "copy" = real file,
        "internal" = lives only inside `.release/`, never mirrored out):

        From templates/commons (every consumer):
            | Source | Destination | Kind |
            | bin/setup-dev-env.sh | bin/setup-dev-env.sh | symlink |
            | bin/install-release-core | bin/install-release-core | symlink |
            | bin/check-shell | bin/check-shell | symlink |
            | bin/check-gate | bin/check-gate | symlink |
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

        Skills ride this same mechanism, whole-directory; see `harness.lex` for
        the catalogs and tiers.

    5.3. The materialize-then-mirror cycle

        For each sync:

        a. Resolve Kind (`detect-kind`) and Capabilities.
        b. Build the new `.release/` tree in a tempdir by `git show`-ing every
           file from the composed subtrees at the selected ref.
        c. For each file in `.release/<dest>`, ensure a *relative* symlink
           exists at `<dest>` in the working tree, pointing into `.release/`
           (e.g. `bin/check-shell -> ../.release/bin/check-shell`).
        d. Walk the repo for symlinks pointing into `.release/` that are now
           broken, and delete them.

        Sync reads templates from a git ref, not the working tree (`git show
        "$ref:$path"`). So a change is only distributed once committed. The ref
        is: `$RELEASE_REF` if set, else a per-repo or per-Kind `release/beta/*`
        branch, else `origin/main`.

    5.4. Two exceptions to the symlink rule

        Most managed files are symlinks. Two cases are not:

        Real-file copies — `needs_real_file`:
            Some consumers of a file don't dereference symlinks. The known case
            is `.github/workflows/*`: GitHub reads workflow YAML directly from
            the git tree and treats a symlink blob as the literal target string,
            which fails to parse and silently breaks every workflow in the
            directory. release-sync writes these as real-file copies carrying a
            managed-marker header comment, so stale copies are still detectable
            and removable on later syncs.

        Release-internal content — `is_release_internal`:
            Some content must live in `.release/` but must *not* be mirrored out
            to a working-tree location. It is part of the tree (so `--check`
            sees it change) but no symlink/copy is created for it. Two kinds:

            - The provenance marker (`.release-sync-source`) — records the
              source revision (ADR-0002); read by `release-drift-check`, never
              used at a consumer location.
            - `lib/release_core/*` — the Python core package. It ships to
              consumers by *pip wheel*, not sync — but when present in the tree
              it must exist in `.release/lib/` as a real-file internal
              dependency, never mirrored out. The match is scoped to
              `lib/release_core/`, not all of `lib/`: other `lib/` paths are
              consumer-facing and must mirror (the bats Capability ships
              `lib/bats-harness.bash`, which consumer test files source).

    5.5. The canonical-home pattern for tools

        A tool that ships to consumers has its single source of truth *inside*
        `templates/commons/bin/` (or the relevant subtree) — not at repo-root
        `bin/`. The maintainer gets it on `$PATH` because repo-root `bin/<tool>`
        is a *symlink* into the template:

        Example:
            bin/install-release-core -> ../templates/commons/bin/install-release-core
        :: text ::

        There is exactly one copy (the template); the maintainer symlink and the
        consumer's `.release/` copy both point at the same source. A tool
        authored as a real file at repo-root `bin/` is, by definition, *not*
        distributed — it is maintainer-only until moved under a template.

    5.6. The changelog / semver family: pip console-scripts, not shims

        `changelog`, `changelog-add`, `changelog-cut`, `changelog-render` and
        `semver` are NO LONGER distributed as `bin/` sys.path shims. They are
        `release_core` pip console-scripts, declared in the package's
        `[project.scripts]` and installed when the wheel is installed
        (`install-release-core` at SessionStart, or
        `bin-internal/install-release-core-pkg.sh` in release CI).

        Consequence for `.release/lib`: with the changelog/semver shims gone, NO
        file synced to a consumer resolves `.release/lib/release_core` anymore —
        `release_core` reaches consumers purely by pip wheel (release#476). The
        remaining `release_core` sys.path shims (`bin/release-sync`,
        `bin/release-core`, `bin/detect-kind`, `bin/release-drift-check`) are
        maintainer-only repo-root `bin/` tools, never synced.

    5.7. Provenance and drift

        Each `.release/` carries `.release-sync-source` — the exact release
        revision that generated it (ADR-0002). `release-drift-check` rebuilds
        against that recorded revision, so it distinguishes real drift (a
        consumer hand-edited a managed file) from mere staleness (the consumer
        simply hasn't re-synced).

    :: note :: To make something reach consumers: put its canonical copy under a
    template subtree (usually `templates/commons/`), and — if it is a runtime
    dependency rather than a used-at-a-location file — shield it with
    `is_release_internal`. Validate with a sync into a throwaway consumer; add a
    `tests/release-sync/` case.

6. Reusable workflows

    `release/` ships reusable GitHub Actions workflows: one canonical pipeline
    per artifact category. A consumer doesn't copy a pipeline — it calls one
    with a thin `with:` block. The logic stays here; the consumer's workflow
    file is a few lines.

    6.1. How a consumer uses them

        A consumer's `.github/workflows/*.yml` is a thin caller (synced as a
        real copy — [#5.4]). It references a reusable workflow here by version:

            jobs:
              release:
                uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
                with:
                  version: ${{ inputs.version }}
                secrets: inherit
        :: yaml ::

        The pin is a floating major: `@v2` always points at the latest
        non-breaking tag in that line, so patch and minor fixes here reach every
        consumer on their next run with nothing to edit. An exact pin (`@v2.1.3`)
        is available when a consumer needs to freeze. A breaking change cuts a
        new major and every consumer's thin caller must be bumped deliberately —
        the cost the versioning contract is designed to make rare (see CLAUDE.md
        and the README versioning table).

        :: note :: Cross-org consumers (lex-fmt/*) cannot use `secrets: inherit`
        — that only propagates within one owner. They must list each secret
        explicitly and may need name mapping. `arthur-debert/*` consumers
        inherit.

    6.2. CI gate vs release pipeline

        Most categories come as a pair:

        - `<category>-ci.yml` — the PR/push gate. Runs the test/lint/build
          checks (almost always by invoking the repo's gate, the same lefthook
          gate). This is the required check on a PR.
        - `<category>.yml` — the full release pipeline. Build + sign + publish +
          tag, run on a release trigger, not on every push.

        Examples of the pairing: `rust-ci.yml` / `rust-cli.yml`, `go-ci.yml` /
        `go-cli.yml`, `tauri-ci.yml` / `tauri-app.yml`, `electron-ci.yml` /
        `electron-app.yml`.

    6.3. The catalog

        Runnable CLIs (build, sign, publish binaries):
            - `rust-cli.yml` — Rust CLI binaries across macOS/Linux/Windows;
              crates.io publish + brew formula.
            - `go-cli.yml` — Go CLI binaries, cross-compiled; ships from git
              tags (no registry), optional brew.

        Libraries (registry only, no binaries):
            - `rust-lib.yml` — publish library crates to crates.io,
              workspace-aware in topological order.
            - `python-pkg.yml` — build with `uv` and publish to PyPI / TestPyPI.

        GUI apps (release artifacts, signing):
            - `electron-app.yml` — electron-builder; macOS sign + notarize; GH
              release with .dmg / .AppImage.
            - `tauri-app.yml` — Tauri 2.x across the platform matrix; sign +
              optional notarize; bumps the three version files atomically.

        Editor extensions:
            - `vscode-ext.yml` — vsce package; Marketplace + optional Open VSX.
            - `zed-extension.yml` — wasm32-wasip2 build; bundles wasm + sources.
            - `nvim-plugin.yml` — source-only; validate, changelog, tag, release
              (the tag IS the version).

        Other stacks:
            - `tree-sitter.yml` — bundle .wasm + grammar + queries; optional npm
              publish.
            - `gh-action.yml` — composite actions + reusable workflows; validate,
              changelog, tag, advance the floating-major branch.

        Docs sites (deploy to GitHub Pages on push to main):
            - `mdbook.yml` — build an mdBook site.
            - `mkdocs.yml` — build a MkDocs site (`--strict`).

    6.4. Shared and infra workflows

        Reusable (a consumer may call these):
            - `copilot-review.yml` — request a Copilot review on a PR. Needs a
              user PAT secret, since the Actions bot silently no-ops otherwise.
            - `cascade-handler.yml` — cross-repo release cascades; computes a
              version bump from commits and dispatches a consumer's release.
            - `bats-e2e.yml` — run a consumer's BATS e2e suite.
            - `tauri-e2e.yml` — run a consumer's Tauri e2e suite.

        Internal to this repo (its own CI; not for consumers):
            - `ci.yml` — this repo's CI; dogfoods `gh-action-ci.yml` and runs the
              lefthook gate.
            - `release.yml` — this repo's own release; dogfoods `gh-action.yml@v2`
              and publishes the `release_core` wheel as a release asset. This is
              the supply side of the pull-model boot ([#4]): this repo publishes
              the wheel, and every consumer's `install-release-core` pulls it.
            - `copilot-review-self.yml` — applies `copilot-review.yml` to this
              repo's own PRs without leaking the token to forks.
            - The `*-tests.yml` suites — `changelog-tests`, `release-sync-tests`,
              `release-cut-tests`, `release-lex-tests`,
              `install-release-core-tests`, `pip-bootstrap-smoke`,
              `provision-gate-toolset-tests`, `audit-tests`. They verify the
              tooling here before it is synced out, and are not called by
              consumers.

7. orc — the maintainer orchestrator

    `orc` is a maintainer-only fleet orchestrator that runs FROM
    `arthur-debert/release`. It is not consumer-facing and is never synced; it
    lives in this repo's `bin/` and resolves its `uv` workspace (it depends on
    `release_core`). Its jobs:

    - `orc run <repo> <prompt>` / `resume` — open or continue an LLM session
      against a consumer repo.
    - `orc probe <repo> <prompt>` — spin a fresh subordinate agent to evaluate a
      (throwaway) consumer clone.
    - `orc watch <pr>` — poll PR lifecycle state and act on transitions.
    - `orc sessions list|clear` — manage stored sessions.

    The canonical fleet loop is PULL-only: cut a release (`release.yml` publishes
    the `release_core` wheel) → `release-core admin release advance-major`
    (fast-forward the floating major). Run `release-core admin repos verify`
    (hermetic pre-flight) before advancing. Consumers self-update at their next
    SessionStart — `install-release-core` pulls the wheel and a bare
    `release-core init` re-materializes the whole managed tree — so there is NO
    push step. (Seeding a pre-pull consumer is a one-time `bash
    bin/install-release-core` run in that repo, then open the resulting
    managed-sync PR — one repo at a time.) For the full doctrine and the
    upstream-vs-consumer routing rule, see the `release-fleet-ops` skill.

8. Roadmap (directional, not a task tracker)

    The GitHub issue tracker is the task list. This is the directional arc — the
    why, not the what's-next.

    Done:
        - Vocabulary + sync redesign: Stack→Kind, Component→Capability, client→
          Consumer; the build-dir + symlinks architecture (ADR-0001/0002) that
          makes removals and renames detectable instead of lingering.
        - Reliable dev cycle (epic #332): the reviewer-agnostic `gh-task-status`
          state engine (`release_core.prstate`) + `orc watch`, distributed and
          review-hardened. Residual in #349 (orc watch live shake-out), #350
          (cloud transport).
        - Pull-model distribution (#416): the wheel is the carrier; consumers
          self-update at SessionStart. No push mechanism.

    In progress:
        - Self-improving feedback loop (epic #348): consumer orientation +
          escalation contract shipped and propagated; the inbox / relay /
          notify-source triage loop and CI-failure analysis remain.
        - Minimal footprint (epic #501, ADR-0005): shrink the tracked consumer
          surface toward zero — `release-core how-to`/`gate` as the carriers,
          `.release/` ephemeral, the drift/sync subsystem retired once
          `.release/` is gitignored. This doc consolidation (WS9) is part of it.

    Later:
        - Architectural fine-tuning: cleaner separation of build / pack / sign /
          publish; research into goreleaser-style tooling.
        - Secret management (Doppler), Windows build/sign, broader package-
          manager support, Claude Cloud hardening.
