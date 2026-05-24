# Changelog Handling

**Status:** proposed
**Author:** Arthur (with Claude)
**Date:** 2026-05-23

## Problem

Changelog handling across the fleet has fractured into three
incompatible variants, none of them fully satisfactory:

1. **cargo-release model (single file, parsed).** `CHANGELOG.md`
   holds an `## Unreleased` section at the top, followed by one
   `## <version>` section per release. `cargo release` parses the
   markdown, promotes `Unreleased` into a versioned section at tag
   time, and commits. Works well for Rust, but every other stack
   has to either reimplement the markdown surgery or hand-edit.
2. **Two-file home-cooked model.** `CHANGELOG.md` plus
   `CHANGELOG_UNRELEASED.md`. Authors append to the unreleased
   file; release flow prepends `## <version>` then the unreleased
   content into `CHANGELOG.md` and truncates the unreleased file.
   No markdown parsing, easier conflicts (the conflict is always
   in one append-only file), but still one shared file under
   active write.
3. **Ad-hoc per-project drift.** Several stacks ended up with
   slightly inconsistent variants of (2) — different headers,
   different release scripts, different ideas about what counts
   as "unreleased."

The pain is the inconsistency itself. release/ is supposed to
provide one canonical pipeline per artifact category; changelog
handling currently has three, none reusable.

A fourth model — **fragment directory** — is widely used outside
this fleet (towncrier, changie, knope, semantic-release-style
"changesets") and converges on it specifically because of the
properties below. We have not tried it here, and it solves the
multi-stack and conflict problems together.

## Goals

- One changelog convention across every stack (Rust, Node, Go,
  Python, shell-only).
- No markdown parsing in the release tooling — concat + write.
- PR-time changelog entries should not conflict between
  concurrent PRs.
- The published `CHANGELOG.md` keeps the cargo-release-compatible
  format consumers and tooling already expect.
- Scripts land in `bin/` as flat verbs, consistent with the
  take-iii canonical layout.

## Non-goals

- No replacement of `cargo release` itself — this slots in as a
  pre-release hook for Rust stacks and as the primary mechanism
  for non-Rust stacks.
- No automated changelog *content* generation from commit
  messages. Authors write fragments by hand (or with a
  one-line helper).
- No migration of historical `CHANGELOG.md` content. The first
  cut after adoption simply starts using fragments going
  forward; the existing rendered history stays as-is in the
  generated file.

## Design

### Layout

```
<repo>/
├── CHANGELOG.md                 # GENERATED — do not edit
└── CHANGELOG/
    ├── README.txt               # explains the directory, points at bin/changelog
    ├── unreleased-<slug>.md     # one per in-flight entry
    ├── unreleased-<slug>.md
    ├── 0.4.2.md                 # one per released version
    ├── 0.4.1.md
    └── 0.4.0.md
```

- `CHANGELOG/` is the source of truth.
- `CHANGELOG.md` at the repo root is **generated** by concatenating
  the per-version files in descending semver order, prefixed with
  a "do not edit" header. It is committed (so consumers, crates.io,
  GitHub releases, and `cargo package` see the rendered file
  without needing to run our tooling).
- `unreleased-<slug>.md` files are fragments. `<slug>` is the PR
  number when known (`unreleased-pr-142.md`) and a short
  kebab-case tag otherwise (`unreleased-fix-token-leak.md`). The
  slug exists only to avoid collisions between concurrent PRs —
  it does not appear in the rendered output.
- Fragment contents are the bullet(s) that will appear under the
  next `## <version>` heading. No version header, no date — just
  the body. Example:

  ```markdown
  - Fix tokenizer crash on empty input (#142)
  ```

### Rendered format

`CHANGELOG.md` preserves the cargo-release-compatible format so
nothing downstream breaks:

```markdown
<!-- generated — do not edit. See CHANGELOG/README.txt -->

# Changelog

## Unreleased

- Fix tokenizer crash on empty input (#142)
- Add --json output to status (#145)

## 0.4.2 — 2026-05-20

- Fix race condition in cache invalidation (#138)

## 0.4.1 — 2026-05-12
...
```

The `## Unreleased` section is rendered from the `unreleased-*.md`
fragments at generation time. Per-version sections come from
`CHANGELOG/<version>.md`.

### Scripts (in `bin/`)

All canonical entry points land in `bin/` per take-iii. The
prefix `changelog-` keeps them discoverable without polluting the
top-level verb namespace:

| Script | Purpose |
|---|---|
| `bin/changelog-add <slug> <content>` | Write `CHANGELOG/unreleased-<slug>.md` with `<content>` as the body. If `<slug>` is numeric, prefixes it with `pr-`. Re-running with the same slug overwrites. |
| `bin/changelog-cut <version>` | Concat all `unreleased-*.md` into `CHANGELOG/<version>.md` (with `## <version> — <date>` header), then delete the unreleased fragments. |
| `bin/changelog-render` | Regenerate `CHANGELOG.md` from `CHANGELOG/*.md`. Idempotent. |
| `bin/changelog` | Orchestrator. `bin/changelog new-version <version>` runs `cut` then `render`. `bin/changelog add ...` forwards to `changelog-add`. Single entry point for humans; the dash-suffixed variants are for hooks and scripts that want to be explicit. |

Each script is ~10–30 lines of shell. No markdown parsing — only
file concat, header prepending, and `rm`.

### cargo-release integration

For Rust stacks, `release.toml` gets a pre-release hook:

```toml
pre-release-hook = ["bin/changelog", "new-version", "{{version}}"]
```

This runs *before* cargo-release stages files, so the freshly
generated `CHANGELOG.md` and the new `CHANGELOG/<version>.md`
land in the release commit alongside the `Cargo.toml` bump. The
old "cargo-release parses the Unreleased section" mechanism is
no longer needed — we generate the same shape, but from
fragments.

### Non-Rust stacks

Whatever the stack's release workflow is (npm publish, GoReleaser,
PyPI), it calls `bin/changelog new-version <version>` as the first
step. The rest of the release flow doesn't have to know anything
about markdown. This is the main reason for the redesign.

## Tradeoffs

**Pro: PR conflicts collapse to near-zero.** Two concurrent PRs
each write their own `unreleased-<slug>.md` file. Git sees an
add+add, not a merge into the same hunk. The only conflict path
is the rendered `CHANGELOG.md`, which is regenerated at release
time and which authors don't edit by hand.

**Pro: Stack-agnostic.** A 30-line shell script works the same in
every repo. No need for stack-specific changelog tooling.

**Pro: Releases don't depend on markdown parsing.** The release
flow is "concat files, prepend a header, write." That is
debuggable from the terminal.

**Pro: History is per-file.** `git log CHANGELOG/0.4.2.md` gives
the authoring history of a specific release's changelog. Today
that lives inside one giant `CHANGELOG.md` and is harder to
isolate.

**Con: Git carries two representations.** Both `CHANGELOG/*.md`
(source) and `CHANGELOG.md` (rendered) are committed. This is a
real cost — every changelog-touching PR has a diff in both
places, and the rendered file can drift from the fragments if
someone bypasses the tooling. Mitigations:

- The `# generated — do not edit` header is the first line.
- A pre-commit (or CI) check can re-run `bin/changelog-render`
  and fail if `CHANGELOG.md` is out of sync.
- We accept the duplication as the price of stack-agnostic
  simplicity; the alternative is "consumers must run our tool
  to read the changelog," which breaks crates.io, GitHub
  release pages, and casual `cat CHANGELOG.md`.

**Con: Two writes per entry.** Authors add a fragment and (on
release) the rendered file regenerates. In practice the second
write is automated, but it does mean fragments and rendered
output exist in parallel between releases.

**Con: Slug discipline.** If two PRs pick the same slug, the
second overwrites the first silently. PR-number slugs avoid
this; the freeform fallback needs reviewer attention.

## Migration

Per-repo, one-shot, no fleet-wide coordination required:

1. Land `bin/changelog*` in release/ and add to the release-sync
   manifest.
2. In each consumer repo, `release-sync` brings down the new
   scripts.
3. Initialize: `mkdir CHANGELOG && cp CHANGELOG.md CHANGELOG/0.x.y.md`
   for the most recent version, leaving the existing `CHANGELOG.md`
   alone. Future versions render new files alongside the historical
   blob; we do not retroactively split the history.
4. Rust stacks: update `release.toml` pre-release hook.
5. Non-Rust stacks: update the release workflow's first step.

No flag day. Repos migrate when their next release goes out.

## Open questions

- **Pre-commit drift check: in release/ or in consumers?** Probably
  a reusable composite action (`actions/changelog-check`) that
  consumer CI calls, so the rule lives once.
- **Should `bin/changelog-add` be invoked by a git hook on PR
  branches?** Tempting (you can't open a PR without a fragment)
  but probably out of scope for v1. Start with social
  convention + reviewer enforcement.
- **Date format in `## <version> — <date>` headers.** ISO
  `YYYY-MM-DD`, matching cargo-release defaults.
