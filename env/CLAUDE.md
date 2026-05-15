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
- **Nvim plugins:** `lua5.4`, `luarocks`, `busted`, `vusted`

Plus what Anthropic ships by default: Node 20/21/22 + npm/yarn/pnpm/bun,
Python 3.x + pip/poetry/uv, Ruby, PHP, Java 21, Go, Rust + cargo, C/C++
toolchain, Docker, Postgres 16, Redis 7.

If a tool you need isn't in this list and isn't a project-local dependency
(node_modules/, cargo target/, etc.), check `env/setup.sh` in
arthur-debert/release main — if it should be env-level (OS-installed,
filesystem-root state), add it there in a PR.

Project-local dependencies belong in the consumer repo's
`scripts/setup-dev-env.sh` (invoked by a SessionStart hook).
