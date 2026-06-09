# ADR-0001: release-sync uses a build directory with symlinks

## Status

Accepted, then partially superseded. The package-distribution half is superseded
by [ADR-0003](0003-pip-install-bootstrap-distribution.md): `release_core` now
arrives via `pip install` of a wheel from the GitHub release, not as committed code
materialized into `.release/`. The build-dir + symlink mechanism described here
still governs how config is composed into the consumer tree — but the
committed-`.release/` decision is superseded by
[ADR-0004](0004-symlinked-managed-files-into-the-installed-package.md) and
[ADR-0005](0005-minimal-footprint-invoke-dont-discover.md): as of **WS4
(release#521, shipped)** the build dir is **gitignored** and composed on demand by
`release-core init` (the standalone `release-sync` verb was retired), so the
repo holds only committed symlinks into an ephemeral tree — nothing under
`.release/` is committed. See [Consequences](#consequences).

## Context

release-sync copies managed files from the release repo's templates directly into consumer repos at their final destination paths (`bin/check`, `lefthook.yml`, etc.). This design cannot handle removals: when a template is deleted or renamed in release, the old file lingers in every consumer indefinitely. The state file (`.release-sync-state.yaml`) was meant to enable manifest-diffing for removals, but that approach requires tracking what was synced before vs now, breaks on repos that were shelved for months/years with stale state, and pushes bookkeeping onto agents and humans.

## Decision

Sync materializes ALL managed files into a single `.release/` directory in the consumer repo, rebuilt from scratch on every sync. Files at their expected locations (`bin/check`, `lefthook.yml`, `.claude/skills/*`, etc.) are symlinks pointing into `.release/`. Both `.release/` and the symlinks are checked into git.

The sync cycle:

1. Remove `.release/` entirely
2. Rebuild it from current templates (commons + capabilities + kind)
3. Create symlinks for any new files that appeared
4. Walk the repo tree for symlinks pointing into `.release/` that are now broken — delete them
5. Commit the result

## Consequences

- **Removals are free.** A deleted template means its file vanishes from `.release/` on next rebuild. The symlink breaks. Broken-symlink cleanup removes it. No state tracking, no removal manifest.
- **~~Self-contained repos.~~ (superseded — WS4, release#521).** ADR-0001
  committed `.release/` with real file content so a repo worked standalone. WS4
  reverses this: `.release/` is **gitignored and ephemeral**, composed on demand
  from the pinned wheel by `release-core init` (SessionStart + CI). The committed
  surface is just the symlinks (+ the real-file workflow copies + the CLAUDE.md
  block); nothing under `.release/` is tracked. The trade — losing
  stale-but-working self-containedness for a drift-free, uniform tree — is the
  explicit subject of [ADR-0004](0004-symlinked-managed-files-into-the-installed-package.md)
  and [ADR-0005](0005-minimal-footprint-invoke-dont-discover.md).
- **Clear ownership.** Symlinks visually signal "this file is managed by release — don't edit it here." The build directory is the single place managed content lives.
- **No state file needed.** The filesystem is the state. `.release-sync-state.yaml` can be dropped.
- **Symlink compatibility.** All consumer tools (shell, lefthook, Claude Code, GitHub checkout) follow symlinks transparently. GitHub Actions workflows are not part of the sync surface (they're thin callers written once per consumer).
