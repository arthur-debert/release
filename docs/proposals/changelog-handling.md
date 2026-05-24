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
    ├── legacy.md                # pre-adoption CHANGELOG.md verbatim (optional, see Migration)
    ├── unreleased-pr-142.md     # one per in-flight entry
    ├── unreleased-pr-145.md
    ├── 0.4.2.md                 # one per released version
    ├── 0.4.1.md
    └── 0.4.0.md
```

- `CHANGELOG/` is the source of truth.
- `CHANGELOG.md` at the repo root is **generated** with the
  structure spelled out under *Rendered format* below. It is
  committed (so consumers, crates.io, GitHub releases, and
  `cargo package` see the rendered file without needing to run our
  tooling).
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

- `CHANGELOG/<version>.md` files contain the `## <version> - <date>`
  header followed by the concatenated bullets. They are produced
  by `bin/changelog-cut` and not edited by hand afterward.
- `CHANGELOG/legacy.md` (optional) holds the pre-adoption
  `CHANGELOG.md` content verbatim — see *Migration*.

### Rendered format

`CHANGELOG.md` preserves the cargo-release-compatible format so
nothing downstream breaks. The rendered file is exactly:

1. Fixed prelude: `<!-- generated - do not edit. See CHANGELOG/README.txt -->` then a blank line then `# Changelog` then a blank line.
2. `## Unreleased` section, with the concatenation of every
   `CHANGELOG/unreleased-*.md` file (sorted by filename
   ascending — stable, locale-independent).
3. One section per `CHANGELOG/<version>.md` file, in descending
   semver order (see *Ordering* below). Each file already
   contains its own `## <version> - <date>` header, so render
   just concatenates them with a blank line separator.
4. If `CHANGELOG/legacy.md` exists, its contents are appended
   verbatim at the end.

Example output:

```markdown
<!-- generated - do not edit. See CHANGELOG/README.txt -->

# Changelog

## Unreleased

- Fix tokenizer crash on empty input (#142)
- Add --json output to status (#145)

## 0.4.2 - 2026-05-20

- Fix race condition in cache invalidation (#138)

## 0.4.1 - 2026-05-12
...
```

### Ordering

Render output must be stable across macOS (BSD) and Linux (GNU)
runners. `sort -V` is GNU-only and `sort` lexicographic order
breaks for `0.10.0` vs `0.9.0`, so we don't lean on shell sort
for versions.

- **Unreleased fragments**: plain ascending byte sort on filename
  (`ls CHANGELOG/unreleased-*.md | LC_ALL=C sort`). Order
  between fragments doesn't carry meaning; we just need it
  stable.
- **Version files**: parse `<major>.<minor>.<patch>(-<pre>)?` from
  the filename and sort numerically (descending). Pre-releases
  (`1.2.3-rc.1`) sort *below* their release (`1.2.3`), matching
  semver §11. Implementation: a 15-line awk/sed pipeline or a
  shelled-out `python3 -c "import packaging.version; ..."`.
  Concrete choice deferred to implementation; the contract is
  "semver-correct descending."
- Render must fail loudly if a `CHANGELOG/<version>.md` filename
  doesn't parse as semver, rather than silently dropping it.

### Scripts (in `bin/`)

All canonical entry points land in `bin/` per take-iii. The
prefix `changelog-` keeps them discoverable without polluting the
top-level verb namespace:

| Script | Purpose |
|---|---|
| `bin/changelog-add <slug> [content...]` | Write `CHANGELOG/unreleased-<slug>.md`. Body is read from stdin if no `content` args are given, otherwise from joined args. If `<slug>` is numeric, prefixes it with `pr-`. **Fails if the target file already exists**; pass `--force` to overwrite. |
| `bin/changelog-cut <version>` | Concat all `unreleased-*.md` into `CHANGELOG/<version>.md` (with `## <version> - <date>` header), then delete the unreleased fragments. |
| `bin/changelog-render` | Regenerate `CHANGELOG.md` from `CHANGELOG/*.md`. Idempotent. |
| `bin/changelog` | Orchestrator. `bin/changelog new-version <version>` runs `cut` then `render`. `bin/changelog add ...` forwards to `changelog-add`. Single entry point for humans; the dash-suffixed variants are for hooks and scripts that want to be explicit. |

Invocation patterns for `changelog-add`:

```sh
# inline single bullet
bin/changelog-add pr-142 "- Fix tokenizer crash on empty input (#142)"

# multi-line body via stdin (preferred in CI / for multi-bullet entries)
bin/changelog-add pr-145 <<'EOF'
- Add --json output to status (#145)
- Bonus: --json also emits exit-code metadata
EOF
```

Each script is ~10–30 lines of shell. No markdown parsing — only
file concat, header prepending, and `rm`.

### cargo-release integration

For Rust stacks, `release.toml` gets a pre-release hook *and*
must disable cargo-release's built-in changelog promotion:

```toml
pre-release-hook = ["bin/changelog", "new-version", "{{version}}"]

# disable cargo-release's own CHANGELOG.md surgery — bin/changelog
# already produced the final rendered file in the hook above.
pre-release-replacements = []
```

This runs *before* cargo-release stages files, so the freshly
generated `CHANGELOG.md` and the new `CHANGELOG/<version>.md`
land in the release commit alongside the `Cargo.toml` bump. The
old "cargo-release parses the Unreleased section" mechanism is
explicitly turned off — if left enabled, cargo-release would
look for an `## Unreleased` section in our already-rendered file
and either re-promote it (duplicating the section) or fail
because we already emptied it.

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

**Con: Slug discipline.** If two PRs pick the same slug,
`changelog-add` fails fast (the file already exists, no
`--force`), so the second author has to pick a new slug rather
than silently clobbering the first entry. PR-number slugs make
this a non-issue in the common case; the failure path only
kicks in for the freeform fallback.

## Migration

Per-repo, one-shot, no fleet-wide coordination required. The key
move is to treat the existing `CHANGELOG.md` as a **legacy blob**
rather than retroactively splitting it into per-version files:

1. Land `bin/changelog*` in release/ and add to the release-sync
   manifest.
2. In each consumer repo, `release-sync` brings down the new
   scripts.
3. Initialize:
   ```sh
   mkdir CHANGELOG
   # capture the existing rendered history, stripping the top-level
   # "# Changelog" header so the render output has exactly one.
   sed '/^# Changelog$/d' CHANGELOG.md > CHANGELOG/legacy.md
   ```
   No per-version splitting, no copy of the whole file as a
   pseudo-"version." The render step appends `CHANGELOG/legacy.md`
   verbatim at the end of the new `CHANGELOG.md`, so historical
   content stays visible without being misclassified.
4. Run `bin/changelog-render` to overwrite `CHANGELOG.md` with the
   new format (which, at this point, is just the prelude + empty
   Unreleased + legacy blob — identical history, new structure).
5. Rust stacks: update `release.toml` pre-release hook and clear
   `pre-release-replacements` (see *cargo-release integration*).
6. Non-Rust stacks: update the release workflow's first step.

No flag day. Repos migrate when their next release goes out. The
first post-migration release produces the first real
`CHANGELOG/<version>.md`; everything older stays in `legacy.md`
forever (no retroactive split needed).

## Adjacent opportunity: tag + GitHub release notes from the fragment

Orthogonal but cheap to wire up at the same time. When
`bin/changelog-cut <version>` produces `CHANGELOG/<version>.md`,
that file is already exactly the right content for:

- The annotated git tag message (`git tag -a v<version> -F CHANGELOG/<version>.md`).
- The GitHub release notes body (`gh release create v<version> -F CHANGELOG/<version>.md`).

Today most stacks either leave tag messages empty or generate
auto-notes from commit titles, which is noisier than the curated
fragment. Plumbing this through the release flow means:

- Rust stacks: cargo-release can read the tag message from a hook
  (`tag-message` setting), pointing at the freshly-cut file.
- Non-Rust stacks: the release workflow's `gh release create` step
  passes `-F CHANGELOG/<version>.md`.

Net result: one authored artifact (the fragment) becomes the
section in `CHANGELOG.md`, the annotated tag, and the GitHub
release body — no copy-paste, no drift between them.

## Open questions

- **Pre-commit drift check: in release/ or in consumers?** Probably
  a reusable composite action (`actions/changelog-check`) that
  consumer CI calls, so the rule lives once.
- **Should `bin/changelog-add` be invoked by a git hook on PR
  branches?** Tempting (you can't open a PR without a fragment)
  but probably out of scope for v1. Start with social
  convention + reviewer enforcement.
- **Date format in `## <version> - <date>` headers.** ISO
  `YYYY-MM-DD`, matching cargo-release defaults.
