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

2. The Two Audiences

    Consumer-facing:
        Skills meant for an agent doing tasks in a consumer repo — the PR
        loop, responding to reviews, escalating infra friction upstream,
        writing Lex.

        - `gh-pr-review-loop` — drive a PR through push, Copilot review,
          triage, fixups, merge-readiness. This is the only skill mechanically
          synced into consumer repos today (see §3).
        - `pr-review-respond` — reply to individual PR review comments.
        - `release-issue-relay` — escalate infra friction the consumer cannot
          fix in place to `arthur-debert/release` as a GitHub issue.
        - `lex-primer` — write syntactically correct Lex.

    Release-only:
        Skills that only make sense while working ON this repo — fleet
        operations, run from inside `arthur-debert/release`. They are NOT
        synced to consumers.

        - `release-fleet-ops` — drive and diagnose release→consumer fleet
          changes; the "is this a release bug or a consumer bug?" oracle.
        - `release-fleet-triage` — drain the consumer-filed issue inbox and
          run the batch upstream-fix loop.

    :: note :: The maintainer's own machine reaches the full set through the
    repo's `.claude/skills/` (real copies of vendored skills, symlinks back to
    `skills/` for the release-only ones). That is a maintainer convenience, not
    the consumer distribution path.

3. How a Skill Reaches a Consumer

    Distribution is deliberately narrow: only `gh-pr-review-loop` is
    materialized into consumer repos, and it rides the same build-dir +
    symlink mechanism as every other injected file (see injected-files.lex).

    The path:
        - Source of truth: `skills/gh-pr-review-loop/SKILL.md` in this repo.
        - `release-sync`, run in the consumer, writes the skill as a real file
          into the consumer's committed `.release/` build tree at
          `.release/.claude/skills/gh-pr-review-loop/SKILL.md`.
        - It then creates a relative symlink at the discovery path
          `.claude/skills/gh-pr-review-loop/SKILL.md` pointing back into
          `.release/`.
        - Claude Code discovers skills under `.claude/skills/`, follows the
          symlink, and reads it.

    The result in a consumer repo:
        - `.release/.claude/skills/gh-pr-review-loop/SKILL.md` — a committed
          real file (the materialized blob).
        - `.claude/skills/gh-pr-review-loop/SKILL.md` — a committed symlink
          into `.release/`.

    One source, one materialized copy, one symlink — no hand-copied skill
    files in the consumer, so nothing can drift out of step with the upstream
    skill.

4. Linting

    `bin-internal/lint-skills.sh` enforces the provenance split: self-authored
    skills (no `.upstream`) are markdownlinted; vendored skills (with
    `.upstream`) are skipped. The lefthook pre-commit gate and the CI
    `skill-lint` job both run it.

    When authoring a skill: write it, then run `bin-internal/lint-skills.sh`.
    When vendoring a skill: drop an `.upstream` marker (source repo + pinned
    commit) in its dir so the gate skips it.
