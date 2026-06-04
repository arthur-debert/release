Skills

    Claude Code skills are markdown playbooks an agent can invoke by name. This
    repo is the single home for the skills used across the fleet: it stores
    them, lints the ones we own, and distributes the consumer-facing ones into
    consumer repos.

1. Where Skills Live

    Every skill lives in exactly one place: the `skills/` directory at the
    root of this repo. One directory per skill, each with a `SKILL.md`
    (frontmatter `name` + `description`, then the playbook body). There is no
    second copy in `templates/` — one source, no drift.

    Two provenances share that directory, told apart by an `.upstream` marker
    file inside the skill's dir:

    Self-authored:
        No `.upstream` marker. We own these; they MUST pass markdownlint.
        Examples: `gh-pr-review-loop`, `pr-review-respond`, `release-fleet-ops`,
        `release-fleet-triage`, `release-issue-relay`, `lex-primer`,
        `lex-multirepo`, `gh-repo-setup`, `migrate-consumer-to-build-dir`,
        `macos-signing-notarization`, `electron-e2e-testing`, `padz-for-agents`.

    Vendored:
        Carry an `.upstream` marker recording the source repo and pinned
        commit (most are from `mattpocock/skills`). Exempt from our lint — they
        are not ours to reformat. Examples: `diagnose`, `tdd`, `review`,
        `triage`, `qa`, `handoff`, `grill-me`, `grill-with-docs`, `to-issues`,
        `request-refactor-plan`, `improve-codebase-architecture`,
        `ubiquitous-language`, `zoom-out`, `teach`, `setup-matt-pocock-skills`.

2. Ownership policy

    release/ is the single source of truth for infrastructure and general
    development-cycle skills. Every consumer repo carries release's official
    set, synced (never hand-copied) as symlinks into its `.release/` build
    tree. A consumer owns ONLY its own application-domain skills — anything
    specific to that project's subject matter.

    Why: a hand-copied infra skill drifts. We found a consumer running a stale,
    much-shortened `pr-review-respond` against release's official copy because
    nothing kept the local copy in step. The distribution mechanism (§4) closes
    that gap — the consumer's copy is a symlink to the synced official blob, so
    it cannot fall behind.

3. The Three Distribution Tiers

    Every skill under `skills/` falls into exactly one tier. The catalogs are
    the literal lists `PUSH_ALL_SKILLS` and `REPLACE_IF_PRESENT_SKILLS` in
    `templates/commons/lib/release_core/release_core/sync.py` — that file is
    the authority; the names below are a convenience snapshot.

    Push-all (synced to EVERY consumer, unconditionally):
        The PR loop, review-response, upstream escalation, and the general
        development-cycle skills an agent needs anywhere. Currently:
        `gh-pr-review-loop`, `pr-review-respond`, `release-issue-relay`,
        `diagnose`, `tdd`, `review`, `triage`, `to-issues`, `handoff`, `qa`,
        `grill-me`, `grill-with-docs`, `improve-codebase-architecture`,
        `request-refactor-plan`, `ubiquitous-language`, `zoom-out`, `teach`,
        `padz-for-agents`.

    Replace-if-present (upgrade-only):
        Synced into a consumer ONLY when that consumer already carries
        `.claude/skills/<name>` (a real dir OR a symlink). The sync upgrades
        the existing copy to release's official; it never ADDS the skill to a
        consumer that lacks it. Currently: `lex-primer`, `lex-multirepo`,
        `electron-e2e-testing`, `macos-signing-notarization`.

    Release-only (NEVER distributed):
        Skills that only make sense while working ON this repo. They stay in
        `skills/` and never reach a consumer: `release-fleet-ops`,
        `release-fleet-triage`, `setup-matt-pocock-skills`, `gh-repo-setup`,
        `migrate-consumer-to-build-dir`.

    :: note :: The maintainer's own machine reaches the full set through the
    repo's `.claude/skills/`. That is a maintainer convenience, not the
    consumer distribution path.

4. How a Skill Reaches a Consumer

    Distribution is whole-directory and rides the same build-dir + symlink
    mechanism as every other injected file (see injected-files.lex). For each
    distributed skill, EVERY file under `skills/<name>/` is materialized — so
    a multi-file skill (e.g. `tdd`, `triage`) arrives complete, not just its
    `SKILL.md`.

    The path, per file under `skills/<name>/`:
        - Source of truth: `skills/<name>/<subpath>` in this repo.
        - `release-sync`, run in the consumer, writes the file as a real blob
          into the consumer's committed `.release/` build tree at
          `.release/.claude/skills/<name>/<subpath>`.
        - It then creates a relative symlink at the discovery path
          `.claude/skills/<name>/<subpath>` pointing back into `.release/`.
        - Claude Code discovers skills under `.claude/skills/`, follows the
          symlink, and reads it.

    Replacing a stale local copy:
        If the consumer already has a REAL file or directory at a managed skill
        dest (a hand-copied skill from before this policy), the sync removes it
        and replaces it with the managed symlink — no `--migrate` flag needed.
        Skill dests are release-owned, so a stale hand-copy is always upgraded
        rather than flagged as a conflict.

    The result in a consumer repo:
        - `.release/.claude/skills/<name>/<subpath>` — committed real blobs
          (the materialized skill).
        - `.claude/skills/<name>/<subpath>` — committed symlinks into
          `.release/`.

    One source, one materialized copy, one symlink — no hand-copied skill
    files in the consumer, so nothing can drift out of step with upstream.

5. Linting

    `bin-internal/lint-skills.sh` enforces the provenance split: self-authored
    skills (no `.upstream`) are markdownlinted; vendored skills (with
    `.upstream`) are skipped. The lefthook pre-commit gate and the CI
    `skill-lint` job both run it.

    When authoring a skill: write it, then run `bin-internal/lint-skills.sh`.
    When vendoring a skill: drop an `.upstream` marker (source repo + pinned
    commit) in its dir so the gate skips it.
