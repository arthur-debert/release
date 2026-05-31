# cloud-env-check

Local Docker harness that approximates Anthropic's Claude Code Cloud
Ubuntu base image so `env/setup.sh` + a consumer repo's
`scripts/setup-dev-env.sh` can be validated without burning a cloud
session every iteration.

## What it does

- Builds an Ubuntu 24.04 image with the toolchain Anthropic documents as
  preinstalled (Node 22, Python 3, Ruby, PHP, Java 21, Go, Rust). It
  deliberately does **not** preinstall what `env/setup.sh` itself
  installs (gh, lefthook, bats, vsce, ovsx, lua/luarocks/busted/vusted,
  plus any candidate apt additions). That way both scripts get tested.
- For each consumer repo, runs in order:
  1. `env/setup.sh` (mounted read-only from this checkout)
  2. `git clone` + checkout of the target branch
  3. `scripts/setup-dev-env.sh` (if present)
  4. `lefthook run pre-commit --all-files` (if `lefthook.yml` exists)
  5. The repo's primary test command (auto-detected by stack, or
     overridden by the caller)
- Reports the first failing step. Each step's output is preserved in
  `.logs/<org>__<repo>.log`.

## Limitations (read these before trusting a green run)

This is an approximation, not a faithful clone. Things it does **not**
catch:

- The cloud env's egress proxy (`Trusted` host allowlist); local Docker
  has unrestricted internet.
- The exact preinstalled tool versions Anthropic ships.
- `CLAUDE_CODE_REMOTE_SESSION_ID` semantics (we set
  `CLAUDE_CODE_REMOTE=true` for the gate in `setup-dev-env.sh` but no
  more).
- Snapshot/cache semantics — every run is a fresh container, no
  five-minute snapshot budget pressure.
- Anthropic's undocumented patches/overlays on the base image.

Known fidelity gaps that cause **harness-only false failures** (real
cloud is fine):

- **Ruby gems.** The image installs Ruby via apt, so gems live under
  `/var/lib/gems/` (root-owned). Real cloud uses **rbenv**, which puts
  gems under `$HOME/.rbenv` (user-writable). Consumers that
  `bundle install` without a vendor path fail here with
  `Bundler::PermissionError` even though they work in real cloud.
  Affected: `lex-fmt/comms`.
- **GUI / Electron e2e tests** (e.g. Playwright visual-fidelity).
  These can fail for non-env reasons (font metrics, GPU absence)
  unrelated to the env-setup contract. If lefthook runs e2e, the
  harness reflects the same outcome a `Bash` agent would see in the
  cloud — useful, but not an env-package problem to solve here.

What it **does** catch reliably: missing apt packages, missing binaries
on `$PATH`, broken bash syntax, missing files, exit-code regressions in
either script.

## Usage

Requires Docker, `GH_TOKEN` exported. Run from this directory or anywhere
— scripts resolve their own paths.

```bash
export GH_TOKEN=ghp_...

# Single repo, current branch on main
./run.sh arthur-debert/dodot

# Single repo, a feature branch (e.g. an open PR's headRef)
./run.sh arthur-debert/phos-core claude/check-environment-setup-DEWLx

# Override the test command
./run.sh lex-fmt/lexed main "pnpm -s preview:test"

# All consumer repos in one go (15-20 min)
./check-all.sh

# Just the lex-fmt ones
./check-all.sh lex-fmt
```

Logs land in `.logs/<org>__<repo>.log`. The summary at the end lists
which step (if any) each repo failed at, so it's easy to triage which
need an apt-list bump.

## Re-using this harness as a regression gate

The base image is cached after the first build (~3-5 min cold,
instantaneous warm). Iterating on `env/setup.sh` is fast: edit the file,
re-run `./run.sh <repo>`, see the diff. The image rebuild only happens
when `Dockerfile` itself changes.

The `TARGETS` list in `check-all.sh` is the source of truth for which
consumer repos are validated; add new repos there as the portfolio
grows.
