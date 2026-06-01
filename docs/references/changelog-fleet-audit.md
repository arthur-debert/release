# Changelog fleet audit

Snapshot of how each managed repo currently handles its changelog,
captured 2026-05-23. Drives the migration plan in
[docs/proposals/changelog-handling.md](../proposals/changelog-handling.md)
(tracker: #201, sub-issue: #203).

Scope and repo list are taken from
[`managed-repos.yaml`](../../managed-repos.yaml) — the in-repo
source of truth. Commented-out repos (`release`, `homebrew-tools`,
`simple-gal-action`) are explicitly out of scope for this audit
and excluded from totals.

## Summary

| Variant                                                                                                                   | Count | Repos                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **cargo-release** (single `CHANGELOG.md` with `## [Unreleased]`, `release.toml` drives promotion)                         | 4     | `padz`, `simple-gal`, `standout`, `lex-fmt/lex`                                                                                                                                              |
| **ad-hoc `## Unreleased` block** (single `CHANGELOG.md`, no `release.toml`, promotion via per-repo `scripts/*` or manual) | 13    | `phos-app`, `phos-core`, `burgertocow`, `clapfig`, `dodot`, `rustloc`, `simple-gal-ui`, `supage`, `wave-term`, `lex-fmt/comms`, `lex-fmt/lexed`, `lex-fmt/tree-sitter-lex`, `lex-fmt/vscode` |
| **ad-hoc, no `## Unreleased` block** (`CHANGELOG.md` exists but has no staging section)                                   | 2     | `lex-fmt/nvim`, `lex-fmt/zed-lex`                                                                                                                                                            |
| **none** (no `CHANGELOG.md` at all)                                                                                       | 0     | —                                                                                                                                                                                            |
| **two-file** (`CHANGELOG_UNRELEASED.md` + `CHANGELOG.md`)                                                                 | 0     | —                                                                                                                                                                                            |

**Total: 19 in-scope managed repos** (4 + 13 + 2 + 0 + 0 = 19).

`lex-fmt/lex` is categorised as **cargo-release** (its primary,
canonical mechanism). It also retains a couple of legacy
`scripts/release/*` files predating the cargo-release adoption —
noted separately rather than double-counted; cleanup tracked in
#211.

### Surprises worth pulling forward

- **Nobody is on the two-file variant.** The proposal called it out as one of three pain variants, but the fleet has fully drifted off it (likely retired before this initiative began). The proposal's framing is historically accurate but the migration plan can simplify: only **cargo-release** and **ad-hoc-`## Unreleased`** repos need a real conversion. The two `no-staging-block` repos (`lex-fmt/nvim`, `lex-fmt/zed-lex`) are essentially greenfield for changelog purposes.
- **GH release bodies are mostly empty / auto-generated.** Only the 4 cargo-release-driven repos have curated release notes (cargo-release fills them from the `Unreleased` section). The other **15/19** rely on GitHub's auto-generated notes or ship empty releases. This is exactly the gap the "tag + GH release notes from the fragment" addendum in the proposal closes.
- **`lex-fmt/*` repos share a `scripts/release/*` pattern** (`create-release`, `trigger-release`, `update-release`, `get-commits-since-release`) — six of the seven `lex-fmt` repos use some subset. Worth treating as one cluster during migration rather than seven independent repos.
- **`wave-term`** was missed from earlier informal lists (memory-based notes lagged the yaml). It's ad-hoc `## Unreleased` — joins the main migration cohort.

## Per-repo detail

| Repo                        | Variant                          | Unreleased file                  | Promotion script                                                                               | Tag annotation src | GH release body src |
| --------------------------- | -------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------ | ------------------- |
| arthur-debert/phos-app      | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | `scripts/release/{trigger,update}-release`                                                     | auto/empty         | auto/empty          |
| arthur-debert/phos-core     | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/burgertocow   | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/clapfig       | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/dodot         | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/padz          | cargo-release                    | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in)                                                                       | cargo-release      | cargo-release       |
| arthur-debert/rustloc       | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/simple-gal    | cargo-release                    | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in)                                                                       | cargo-release      | cargo-release       |
| arthur-debert/simple-gal-ui | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/standout      | cargo-release                    | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in)                                                                       | cargo-release      | cargo-release       |
| arthur-debert/supage        | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| arthur-debert/wave-term     | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | — (manual)                                                                                     | auto/empty         | auto/empty          |
| lex-fmt/comms               | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | `scripts/create-release`, `scripts/release/{trigger,update}-release`                           | auto/empty         | auto/empty          |
| lex-fmt/lex                 | cargo-release (+ legacy scripts) | `CHANGELOG.md` `## [Unreleased]` | `scripts/release/{get-commits-since-release,update-release}` + cargo-release                   | cargo-release      | cargo-release       |
| lex-fmt/lexed               | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | `scripts/release/update-release`                                                               | auto/empty         | auto/empty          |
| lex-fmt/nvim                | ad-hoc (no `## Unreleased`)      | —                                | `scripts/create-release`, `scripts/release/{trigger,update}-release`                           | auto/empty         | auto/empty          |
| lex-fmt/tree-sitter-lex     | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | `scripts/create-release`, `scripts/release/{get-commits-since-release,trigger,update}-release` | auto/empty         | auto/empty          |
| lex-fmt/vscode              | ad-hoc `## Unreleased`           | `CHANGELOG.md` `## Unreleased`   | `scripts/create-release`, `scripts/release/{trigger,update}-release`                           | auto/empty         | auto/empty          |
| lex-fmt/zed-lex             | ad-hoc (no `## Unreleased`)      | —                                | `scripts/create-release`, `scripts/build`                                                      | auto/empty         | auto/empty          |

## Method

Source of truth: [`managed-repos.yaml`](../../managed-repos.yaml).
The same list is consumed by `audit-portfolio`.

For each repo, checked against the local clone:

- Presence of `CHANGELOG_UNRELEASED.md` → two-file variant.
- Presence of `release.toml` + `## [Unreleased]` block in
  `CHANGELOG.md` → cargo-release variant.
- Otherwise `## Unreleased` heading present → ad-hoc-with-staging.
- Otherwise `CHANGELOG.md` present without staging block → ad-hoc-no-staging.
- Otherwise → none.
- Promotion script: `grep -lrE 'changelog|CHANGELOG' scripts/`.
- Tag / release body source: `grep -E 'git tag.*-F|gh release create.*-F|--notes-file' .github/workflows/*` — falls back to "auto/empty" if nothing matches and the repo isn't cargo-release-driven.

Spot-checked manually against repo contents: `padz` (cargo-release confirmed via `release.toml` + populated GH release body), `phos-core` (ad-hoc confirmed via `## [Unreleased]` block + no `release.toml`), `wave-term` (ad-hoc, confirmed via shallow clone).

To reproduce, walk the yaml's `projects` list and apply the
checks above against each repo's local clone (or a shallow clone
if unavailable).

## Implications for the migration plan

- **Pilot picks (issue #209)**: one per stack that's currently
  cargo-release (proves the `pre-release-replacements = []` story
  end-to-end) and one per stack that's ad-hoc (proves the
  promotion-script replacement story).
- **Stragglers to delete in cleanup (#211)**: the per-repo
  `scripts/release/{create,trigger,update,get-commits-since}-release`
  scripts across `lex-fmt/*` and a few `arthur-debert/*` repos.
  These get replaced by `bin/changelog new-version` + the
  per-stack reusable release workflow. The legacy scripts on
  `lex-fmt/lex` come along in the same sweep.
- **`bin-internal/roll-changelog.sh` in release/ itself** is out of
  scope here (release/ isn't in the audit), but is still dead
  code worth deleting in #211 alongside the consumer-side sweep.
- **`no-staging-block` repos** (`lex-fmt/nvim`, `lex-fmt/zed-lex`)
  get an initial `CHANGELOG/` directory at adoption time with a
  small `legacy.md` (just whatever the existing `CHANGELOG.md`
  body is). Migration is strictly additive for them.
