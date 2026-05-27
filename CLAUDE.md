# release/

Reusable GitHub Actions workflows + composite actions for releasing
software across the arthur-debert / lex-fmt ecosystems. One canonical
pipeline per artifact category; consumers call them with a thin `with:`
block.

`README.md` has the consumer-facing overview, category table, and
versioning policy. This file captures the principles and non-obvious
constraints that apply when *working on this repo itself*.

## Repo shape

- `.github/workflows/rust-cli.yml`, `copilot-review.yml` — reusable workflows
- `.github/actions/<name>/` — composite actions, atomic units shared across (future) workflows
- `bin/` — local CLI tools that mutate consumer-repo state or drive the day-to-day PR loop. **Single source of truth for everything on `$PATH`.** Includes:
  - Policy/setup: `apply-ruleset`, `sweep-github-policy`, `install-release-{secrets,token}`, `enable-dependabot-security`, `detect-stack`
  - PR loop: `gh-copilot-{on,off,wait,review}`, `gh-pr-checks-wait`, `gh-pr-resolve-thread`, `gh-release-issue`
- `bin-internal/` — CI-side scripts that composite actions and reusable workflows exec inside GitHub Actions runners (not on `$PATH`, never called locally)
- `templates/` — render templates (e.g. Homebrew formula)
- `tests/fixtures/` — synthetic projects per category, exercised by `_ci.yml`
- `docs/` — consumer guide, secrets, breaking-changes log
- `examples/` — paste-ready consumer release.yml files

### `bin/` is on $PATH via dodot

`~/h/dotfiles/release/profile.zsh` exports `~/h/release/bin` directly onto `$PATH`, sourced at login by dodot's shell handler. **Drop a new executable into `bin/`, mark it `+x`, and it's reachable by name in any new shell** — no per-script symlink mirror needed.

Earlier attempts that *don't* work and shouldn't be reintroduced:
- `dotfiles/release/bin -> ~/h/release/bin` (a top-level symlink): dodot routes top-level symlinks through the **symlink** handler, not the **path** handler, so the dir never lands on `$PATH`.
- A real `dotfiles/release/bin/` of per-script symlinks: works (path handler kicks in) but creates a maintenance tax — every new script needs a matching symlink. The `profile.zsh` approach is strictly cleaner.

## Versioning contract — do not break

Consumers pin `@v1` (floating major) or `@v1.2.3` (exact). The `v1`
branch always points at the latest non-breaking tag.

| Bump | Trigger |
|---|---|
| PATCH | bug fix in any composite action, no input changes |
| MINOR | new optional input, opt-in feature, new category workflow |
| MAJOR | required-input rename, default behavior change, removed input |

Anything that forces every consumer to edit their thin caller is a
MAJOR. Six rust-CLI consumers currently track `@v1`; cutting v2 means
coordinating six PRs.

## Design principles

### Releases run in CI, never locally
Runnables (CLIs, GUI apps, extensions) build/sign/publish from GitHub
Actions. The user does not always have a fully provisioned dev machine
and may travel for weeks; "must release locally" can turn a security
fix into a multi-week stall. Pure Rust **library crates** are the one
exception (`cargo publish` from anywhere is fine). Don't suggest
"just publish locally" as a CI workaround — fix the CI.

### CI workflows: caching + artifact splits
1. **Caching is mandatory.** Without it a 2-minute Rust job balloons
   to 10. Use `Swatinem/rust-cache` rather than rolling your own.
2. **Split jobs; pass binaries via artifacts.** Build once in the
   unit job, upload, downstream jobs download. Concrete win: dodot
   E2E went 9m → 50s. Multi-platform release builds are the only
   exception (must build per target).

### E2E reined in; BATS for shell CLI
- Unit tests guard quality (breadth + depth + invariants). E2E
  verifies user perspective and glue. Don't substitute one for the
  other.
- Canonical shell E2E framework: **BATS** (`bats-core`). Each `@test`
  runs in a fresh subshell — matches the shell-subshell isolation
  pattern used in padz/dodot.
- Docker is for testing the *delivery mechanism* (`apt install
  ./pkg.deb`, `brew install <tap>/<formula>`), not binary behavior
  — for binary behavior the GH runner is already clean-enough.

### Distribution channels
- Library crates → cargo only.
- CLI binaries → always cargo install; ideally also brew (macOS)
  and apt (linux).
- Use the **shared brew tap** (`arthur-debert/homebrew-tools`), not
  a per-project tap. padz is the reference impl.
- GUI apps (Electron) → GH release artifacts only for now. Don't
  propose homebrew-core, mas, choco, snap unless asked.

### Cross-org consumers need explicit secrets
`secrets: inherit` only propagates within the same owner. lex-fmt
consumers must list every secret explicitly under `secrets:` in
their `uses:` block and may need name mapping (lex-fmt uses
`CARGO_REGISTRY_TOKEN`; this action's input is `CRATES_IO_KEY`).
arthur-debert/* consumers can use `inherit`. Caught empirically in
lex-fmt/lex v0.9.1.

### Docs layout
- `docs/proposals/` (to do) and `docs/proposals/done/` (enforced —
  don't leave finished proposals at top level).
- `docs/references/` for "why" / design.
- `docs/dev/` for tool-base work.
- `docs/users/` for end users.
- mdBook only for large, public-facing doc sets.

## Operational rules

- **Bug fixes go here, not in consumers.** A bug surfaced by a
  consumer is fixed here, tagged as a PATCH, and the `v1` branch
  advanced. Consumers re-run; nothing for them to edit.
- **`bin-internal/ci-publish-crate.sh` is load-bearing.** It tolerates
  the case where a re-published older RC version exists per the
  crates.io JSON API but `cargo publish` errors. Don't replace
  with a `cargo search VERSION | grep` fallback — that's the
  pattern this script was written to fix.
- **Companion scripts:** `bin/install-release-secrets` propagates the 7
  release secrets to onboarded rust repos. Sister to
  `bin/install-release-token`. If the set of required secrets here
  changes, update that script.
- Full design rationale, bug log, and open follow-ups live in
  `~/h/repo-all/tooling.lex` §13.
