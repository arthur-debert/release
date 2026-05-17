# User-level instructions (Claude Code on the web)

These notes apply across every cloud session in this environment.

## Cross-repo GitHub: use `gh` CLI, not MCP

The `github` MCP server in cloud sessions is hard-scoped to the rooted
repo. Any `mcp__github__*` call against a different repo returns
"Access denied: repository ... not configured for this session". This
is a session-config restriction, not a permissions one.

The `gh` CLI is installed and authenticated via `GH_TOKEN` (a PAT scoped
to the related-repo group). Use it for:

- Cross-repo issue creation, comments, PR reads/writes (the relay
  pattern across arthur-debert/* and lex-fmt/* repos)
- Enumerating PR review thread node IDs (`PRRT_*`) for
  `resolveReviewThread` — the GitHub MCP server's `pull_request_read`
  drops thread IDs from its response (bug:
  github/github-mcp-server#2331)

Using `gh` for these operations is the documented route, not a security
bypass.

## Cloud-session PR flow specifics

These are the rules of the road in Claude Code on the web that don't apply to local Claude Code (CLI / Desktop). Internalize them so the PR flow stays clean.

### Opening a PR

- **First PR for a feature**: open it as draft from your session-assigned branch. Both Gemini and Copilot auto-review drafts under the canonical policy (as of 2026-05-15 — `copilot-review.yml` fires on `pull_request: [opened]` regardless of `draft` state; previously Copilot waited for ready, but that produced an awkward "two state transitions per PR" pattern). Drafts get both bots' input; the Auto-fix loop addresses comments while the PR is still draft.
- **Immediately after `gh pr create`, enable Auto-fix on the PR.** Auto-fix is per-PR opt-in — without it, neither this session nor a future session gets webhook events for new review comments or CI failures. Toggle via the CI status bar in claude.ai/code, or tell Claude "auto-fix this PR." Requires the Claude GitHub App installed on the org that owns the PR (e.g. for the current portfolio: `arthur-debert`, `lex-fmt`).
- **Flip the PR to ready** when you're done iterating — both bots have already reviewed at open under the current policy, so `gh pr ready` is purely a state-transition signal to the user ("I'm done, please final-read and merge"). It does not re-trigger Copilot.

### Working on an existing PR (someone else's, or your own from another session)

The cloud orchestrator scopes git-push auth to your session's assigned branch (named `claude/<task>-XXXXX`), so you usually **can't push fixups directly to the existing PR's feature branch**. Two patterns:

1. **Stacked sub-PR (default in cloud).** Make your changes, push to your session branch, open a sub-PR targeting the *original PR's feature branch* (not main). Squash-merge the sub-PR into the feature branch; the original PR picks up the new commits automatically. This is the canonical cloud pattern when the agent can't push to the existing branch.
2. **`/teleport` the session local.** If you need to push directly to the original branch (e.g. the stacked-PR overhead isn't worth it for a one-line fix), pull the cloud session down to local Claude Code via `/teleport`, push there, and the local push doesn't go through the cloud orchestrator's branch restriction.

The stacked-PR pattern is workable, not a bug. Name it as a stacked PR in the PR description so the human reviewer doesn't think it's a duplicate.

### Addressing review comments

Use the `pr-review-respond` skill (installed at `~/.claude/skills/pr-review-respond/SKILL.md`). Wait for both Gemini and Copilot's initial pass before triaging (the batch approach catches overlapping comments and avoids whipsaw fixes). Both reviewers fire at PR open under the current canonical policy, so the wait is "until both have posted reviews" — typically a few minutes for Gemini, ~7 min for Copilot.

### The PR state-transition the agent owns

The agent — not the user — owns these state transitions:

1. **`gh pr create --draft`** — at the start of a feature.
2. **`gh pr ready`** — after addressing both reviewers' comments and CI is green. This is the agent's explicit signal to the user that iteration is done. Under the current canonical policy (since 2026-05-15) flipping to ready does **not** re-trigger Copilot — both reviewers fired at PR open while still draft. The flip is purely a state transition signaling "agent is done, user please review and merge." **Without this step the loop doesn't close** and the user has to manually flip every PR. The skill walks the agent through this; the principle is that `pr ready` is the agent's job, not the user's.
3. **Stop and notify the user** — once both reviewers have weighed in, all threads are resolved, CI is green, and `mergeStateStatus=CLEAN`. The user does the final read and merges.

Don't rely on the Claude UI's "CI monitoring" feature to drive these transitions — it has its own gh-auth setup that may report `CI checks unavailable` even when the agent's `gh` works fine. The agent should poll with `gh pr checks "$PR" --watch` directly.

## Shell footguns in cloud sessions

Recurring traps worth knowing so background watchers and long-running commands don't get stuck:

- **`pgrep -f <pattern>` matches the pgrep process's own argv.** If you watch for `git commit -m` with `pgrep -f 'git commit -m'`, the watcher's command line literally contains that pattern, so it matches itself and loops forever. Fix: `pgrep -fa 'pat' | awk -v me=$$ '$1 != me'` (filter out the current shell's pid), or use a uniquely-named sentinel file the actual command will touch. Same trap applies to `pkill -f` and any other full-command-line matcher.

## Available skills

`~/.claude/skills/pr-review-respond/SKILL.md` is the canonical flow for
replying to and resolving PR review comments. Invoke it for any review-
feedback handling.

`~/.claude/skills/gh-repo-setup/SKILL.md` brings a repo up to the canonical
release-loop setup (branch protection ruleset, per-stack policy files,
copilot-review wiring). Idempotent. Invoke when onboarding a new repo or
verifying alignment.

`~/.claude/skills/release-issue-relay/SKILL.md` escalates infrastructure
friction back to `arthur-debert/release`. Invoke when you hit a problem
the consumer repo can't fix in place (workflow misbehavior, broken policy
template, helper-script bug).

## Tools available in this env

Pre-installed by the env setup script — don't reinstall:

- **General:** `gh` (CLI), `lefthook` (pre-commit hook runner; binary only,
  `lefthook install` is per-repo)
- **Shell tests:** `bats`
- **VS Code extensions:** `vsce` (`@vscode/vsce`), `ovsx`
- **Nvim plugins:** `lua5.4`, `luarocks`, `busted`, `vusted`, `nvim` (binary)
- **GUI tests:** `xvfb` binary (start `Xvfb :99 &` per-session in your
  scripts/setup-dev-env.sh, then `export DISPLAY=:99`)
- **Tauri (GTK system libs):** `libgtk-3-dev`, `libwebkit2gtk-4.1-dev`,
  `libsoup-3.0-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`,
  `libjavascriptcoregtk-4.1-dev`
- **Misc:** `uuidgen` (via uuid-runtime)

Plus what Anthropic ships by default: Node 20/21/22 + npm/yarn/pnpm/bun,
Python 3.x + pip/poetry/uv, Ruby, PHP, Java 21, Go, Rust + cargo, C/C++
toolchain, Docker, Postgres 16, Redis 7.

If a tool you need isn't in this list and isn't a project-local dependency
(node_modules/, cargo target/, etc.), check `env/setup.sh` in
arthur-debert/release main — if it should be env-level (OS-installed,
filesystem-root state), add it there in a PR.

Project-local dependencies belong in the consumer repo's
`scripts/setup-dev-env.sh` (invoked by a SessionStart hook).
