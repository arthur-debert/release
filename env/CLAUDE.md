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
