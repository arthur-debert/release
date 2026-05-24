# Changelog fleet audit

Snapshot of how each managed repo currently handles its changelog,
captured 2026-05-23. Drives the migration plan in
[docs/proposals/changelog-handling.md](../proposals/changelog-handling.md)
(tracker: #201, sub-issue: #203).

## Summary

| Variant | Count | Repos |
|---|---|---|
| **cargo-release** (single `CHANGELOG.md` with `## [Unreleased]`, `release.toml` drives promotion) | 4 | `padz`, `simple-gal`, `standout`, `lex-fmt/lex` |
| **ad-hoc `## Unreleased` block** (single `CHANGELOG.md`, no `release.toml`, promotion via per-repo `scripts/*` or manual) | 13 | `arami-app`, `arami-core`, `burgertocow`, `clapfig`, `dodot`, `rustloc`, `simple-gal-ui`, `supage`, `comms`, `lexed`, `tree-sitter-lex`, `vscode` (+ `lex-fmt/lex` overlaps; see note) |
| **ad-hoc, no `## Unreleased` block** (`CHANGELOG.md` exists but has no staging section) | 2 | `lex-fmt/nvim`, `lex-fmt/zed-lex` |
| **none** (no `CHANGELOG.md` at all) | 3 | `arthur-debert/release`, `homebrew-tools`, `simple-gal-action` |
| **two-file** (`CHANGELOG_UNRELEASED.md` + `CHANGELOG.md`) | **0** | — |

**Total: 21 managed repos** (per `~/.claude/.../project_managed_repos.md`).

### Surprises worth pulling forward

- **Nobody is on the two-file variant.** The proposal called it out as one of three pain variants, but the fleet has fully drifted off it (likely retired before this initiative began). The proposal's framing is historically accurate but the migration plan can simplify: only **cargo-release** and **ad-hoc-`## Unreleased`** repos need a real conversion. `none` and `no-unreleased` repos are essentially greenfield.
- **`lex-fmt/lex` is both cargo-release *and* has per-repo scripts** (`scripts/release/{get-commits-since-release,update-release}`). The scripts predate the cargo-release adoption — worth checking whether they're still wired in or dead code.
- **GH release bodies are almost entirely auto-generated or empty.** Only cargo-release-driven repos (4) have curated release notes (cargo-release fills them from the `Unreleased` section). The other 17 either rely on GitHub's auto-generated release notes or ship empty releases. This is exactly the gap the "tag + GH release notes from the fragment" addendum in the proposal addresses.
- **`lex-fmt/*` repos have a shared `scripts/release/*` pattern** (`create-release`, `trigger-release`, `update-release`, `get-commits-since-release`) — six of the seven `lex-fmt` repos use some subset. Worth treating as one cluster during migration rather than seven independent repos.

## Per-repo detail

| Repo | Variant | Unreleased file | Promotion script | Tag annotation src | GH release body src |
|---|---|---|---|---|---|
| arthur-debert/release | none | — | `scripts/roll-changelog.sh` (legacy, no `CHANGELOG.md` to roll) | workflow `gh-action.yml` (release-of-actions) | workflow `gh-action.yml` |
| arthur-debert/arami-app | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | `scripts/release/{trigger,update}-release` | auto/empty | auto/empty |
| arthur-debert/arami-core | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/burgertocow | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/clapfig | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/dodot | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/homebrew-tools | none | — | — | n/a (formula bumps via PRs, no release artifacts) | n/a |
| arthur-debert/padz | cargo-release | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in) | cargo-release | cargo-release |
| arthur-debert/rustloc | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/simple-gal | cargo-release | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in) | cargo-release | cargo-release |
| arthur-debert/simple-gal-action | none | — | — | auto/empty | auto/empty |
| arthur-debert/simple-gal-ui | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| arthur-debert/standout | cargo-release | `CHANGELOG.md` `## [Unreleased]` | cargo-release (built-in) | cargo-release | cargo-release |
| arthur-debert/supage | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | — (manual) | auto/empty | auto/empty |
| lex-fmt/comms | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | `scripts/create-release`, `scripts/release/{trigger,update}-release` | auto/empty | auto/empty |
| lex-fmt/lex | cargo-release (+ legacy scripts) | `CHANGELOG.md` `## [Unreleased]` | `scripts/release/{get-commits-since-release,update-release}` + cargo-release | cargo-release | cargo-release |
| lex-fmt/lexed | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | `scripts/release/update-release` | auto/empty | auto/empty |
| lex-fmt/nvim | ad-hoc (no `## Unreleased`) | — | `scripts/create-release`, `scripts/release/{trigger,update}-release` | auto/empty | auto/empty |
| lex-fmt/tree-sitter-lex | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | `scripts/create-release`, `scripts/release/{get-commits-since-release,trigger,update}-release` | auto/empty | auto/empty |
| lex-fmt/vscode | ad-hoc `## Unreleased` | `CHANGELOG.md` `## Unreleased` | `scripts/create-release`, `scripts/release/{trigger,update}-release` | auto/empty | auto/empty |
| lex-fmt/zed-lex | ad-hoc (no `## Unreleased`) | — | `scripts/create-release`, `scripts/build` | auto/empty | auto/empty |

## Method

Generated by walking each managed repo's local clone (paths from
`~/.claude/.../project_managed_repos.md`) and checking:

- Presence of `CHANGELOG_UNRELEASED.md` → two-file variant.
- Presence of `release.toml` + `## [Unreleased]` block in
  `CHANGELOG.md` → cargo-release variant.
- Otherwise `## Unreleased` heading present → ad-hoc-with-staging.
- Otherwise `CHANGELOG.md` present without staging block → ad-hoc-no-staging.
- Otherwise → none.
- Promotion script: `grep -lrE 'changelog|CHANGELOG' scripts/`.
- Tag / release body source: `grep -E 'git tag.*-F|gh release create.*-F|--notes-file' .github/workflows/*` — falls back to "auto/empty" if nothing matches and the repo isn't cargo-release-driven.

Spot-checked manually: `padz` (cargo-release), `arami-core` (ad-hoc), `homebrew-tools` (none).

## Implications for the migration plan

- **Pilot picks (issue #209)**: one per stack that's currently
  cargo-release (proves the `pre-release-replacements = []` story
  end-to-end) and one per stack that's ad-hoc (proves the
  promotion-script replacement story).
- **Stragglers to delete in cleanup (#211)**: the per-repo
  `scripts/release/{create,trigger,update,get-commits-since}-release`
  scripts across `lex-fmt/*` and a few `arthur-debert/*` repos.
  These get replaced by `bin/changelog new-version` + the
  per-stack reusable release workflow.
- **`scripts/roll-changelog.sh` in release/ itself** is dead code
  (release/ has no `CHANGELOG.md`). It can be deleted as part of
  #211 with no migration risk.
- **`none`-variant repos** (`release`, `homebrew-tools`,
  `simple-gal-action`) get an initial `CHANGELOG/` directory at
  adoption time but no `legacy.md` to capture. Migration is
  strictly additive for them.
