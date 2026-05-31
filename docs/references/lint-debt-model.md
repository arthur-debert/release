# Lint debt: the three-case model

**Status:** doctrine
**Date:** 2026-05-30

Markdown-lint friction came from re-deciding the same question per file, forever.
There are only three kinds of lint failure, each with exactly one correct
response. The first one is removed structurally; the other two are always "fix,"
never "ignore."

## The gate only lints what we own

The quality gate runs on **git-tracked files** (`lefthook` feeds it the staged /
`git ls-files` set). So the foundational rule is absolute:

> **If you don't want it linted, gitignore it.**

Third-party dependencies, fetched vendor dirs, build output — gitignore them, and
the gate (lint **and** tests) never sees them. This is not incidental:
**gitignore means "not ours / not our concern" for every quality check.** A
per-stack vendor dir is gitignored precisely so we never lint or test third-party
code. The linter therefore only ever faces files we wrote or generated.

## The three cases

### 1. Third-party — we don't control it → it's **gitignored** (already out)

Not our code: deps, vendored libs, fetched assets. They live in gitignored dirs;
the gate never touches them. If a third-party file is somehow *tracked*, the fix
is to **gitignore it** — not to lint it, and not to hand-fix it (our fix breaks
on their next update). "Ignore" always means "gitignore"; there is no separate
lint-ignore escape hatch for third-party content.

### 2. Authored — we wrote it → **fix the file, once**

Our own prose: READMEs, docs, notes. `markdownlint --fix` silently handles
*style* (whitespace, blank lines, list markers); *content* rules (`MD040` fence
language, `MD051` link anchors, `MD041` first heading) need a human edit. Do it
once — it stays fixed. Never ignore authored content to dodge a content rule.

### 3. Tool-generated — our tool emits it → **fix the generator**

A converter's `.md`, a codegen step, a templated file. Do **not** hand-fix it (it
diverges from the generator and reappears next run) and do **not** ignore it
(that hides a real tool bug). **Fix the tool to emit correct markdown**, then
regenerate. If the output shouldn't be committed at all, gitignore it (case 1).

## Ownership: where the fix lands

Orthogonal to the three cases — *who owns the thing being fixed*:

- **Release-owned** — the gate config, a release-distributed file, a release-run
  generator → fix in `arthur-debert/release`; propagates via `@vN`. (A
  distributed file failing the gate is a release bug, e.g. release#374.)
- **Consumer-owned** — the consumer's docs, their vendoring, their generator
  (e.g. the LexD converter) → fix in the consumer repo. (release#375 was closed
  consumer-side: comms' converter-output fixtures → fix the converter.)

## The one tracked-but-not-authored exception

A few files are *committed* (so can't be gitignored) yet aren't prose to
author-lint: the generated `CHANGELOG`, mdbook `SUMMARY.md`, test `fixtures/`,
agent skill-docs. Those — and only those — live in the small, **release-owned**
managed `.markdownlintignore`. It is **not** a per-repo escape hatch; it's the
fleet-wide list of committed-but-generated conventions, and it changes rarely and
deliberately. There is deliberately **no consumer-local ignore**: third-party is
gitignored, authored is fixed, generated is fixed at the tool.

## Do it wholesale, not per file

The gate runs on changed files, so legacy debt surfaces one file at a time and
gets re-adjudicated on every brush-past — that *is* the whack-a-mole. Break the
loop by categorizing a repo's whole tracked markdown set once:

```sh
lefthook run pre-commit --all-files     # surfaces all current markdown debt
```

Then apply the case to each hit: third-party tracked-by-mistake → gitignore;
authored → fix; generated → fix the tool. One pass, then it's done — not an
ongoing hunt.
