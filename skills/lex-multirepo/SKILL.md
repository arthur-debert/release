---
name: lex-multirepo
description: "Bootstrap sibling lex-fmt repos into /tmp/lex-fmt/ for multi-repo agent tasks. Use when: (1) the task explicitly names a sibling lex-fmt repo by name (`comms`, `vscode`, `lexed`, `nvim`, `tree-sitter-lex`, `zed-lex`, `mkdocs-lex`) and the rooted repo is different; (2) the task is a planning / proposal / cross-repo analysis spanning ≥2 lex-fmt repos; (3) the task requires reading release-cascade config or shared specs that live in `comms`. Do NOT trigger on tasks scoped entirely to the rooted repo, monorepo-internal work inside `lex/crates/`, or generic 'multi-file' keyword matches."
---

# lex-multirepo

Standardizes how Claude Code agents pull in sibling lex-fmt repos when a task needs to read or work across them. Avoids the redundant-`/tmp`-clone pattern where every session re-discovers where to put things.

## Why this exists

The GitHub MCP server in cloud sessions is hard-scoped to the rooted repo. Cross-repo `mcp__github__*` calls return "Access denied". `gh` works cross-repo (authenticated via `GH_TOKEN`), but each agent had been reinventing the bootstrap: where to clone, what depth, whether to bootstrap deps.

This skill points agents at `clone-lex-stack` (installed env-level by `arthur-debert/release env/setup.sh`) and the standard checkout location `/tmp/lex-fmt/<repo>`. See [the merged plan](https://github.com/lex-fmt/lex/pull/661) for the full design rationale.

## When to use

| Use this skill | Don't use this skill |
|---|---|
| "Plan a feature that touches lex + vscode + lexed" | "Refactor `crates/lex-lsp/src/server.rs`" (in-repo) |
| "Read the release cascade config in comms to answer X" | "Add a flag to `lexd inspect`" (in-repo) |
| "Cross-repo audit of how editors consume lexd-lsp" | "Find all callers of `parse_lex_file`" (in-repo) |
| "Compare how nvim and vscode wire up semantic tokens" | "Update changelog for this PR" (in-repo) |

If the task is scoped to the rooted repo, **do not invoke this skill** — read the local checkout.

## Prerequisites

- `clone-lex-stack` on `$PATH` (installed by `env/setup.sh` from `arthur-debert/release`).
- `GH_TOKEN` exported with read access to lex-fmt repos.

If `clone-lex-stack` is missing (older env-setup snapshot), see the fallback section below.

## How to use

### Step 1: Identify which sibling repos the task needs

Default to a minimal subset. Run `--all` only when the task is genuinely cross-cutting (release-cascade audit, org-wide policy sweep, comparing every editor).

| Repo | What's there |
|---|---|
| `lex` | The parser, LSP, CLI, format adapters (`crates/lex-*`). The rooted repo for most sessions. |
| `comms` | Grammar specs, test fixtures, shared docs (`specs/`, `docs/`). Submoduled by most other repos. |
| `mkdocs-lex` | mkdocs plugin for rendering .lex files. |
| `vscode` | VSCode extension. |
| `lexed` | Electron desktop editor. |
| `nvim` | Neovim plugin. |
| `tree-sitter-lex` | Tree-sitter grammar (syntax highlighting, injection, textobjects). |
| `zed-lex` | Zed editor extension. |

### Step 2: Clone

```sh
# Minimal — read sources of two siblings:
clone-lex-stack comms vscode

# Read-only grep across everything, fast:
clone-lex-stack --all --depth=1

# Plan a feature that needs full git history of two repos:
clone-lex-stack comms vscode

# Need to actually build/test sibling code:
clone-lex-stack --with-setup lexed
```

Flags:

- `--all` — every repo in the fixed 8-repo list. Mutually exclusive with positional names.
- `--depth=N` — shallow clone (use `1` for read-only grep tasks; default is full clone for planning tasks that need `git log` / `blame`).
- `--with-setup` — run each repo's `scripts/setup-dev-env.sh` after clone. **Opt-in only** — most tasks just need to read source.
- `--refresh` — `git fetch + reset --hard` on existing dirs instead of skip-if-exists. Use sparingly.
- `--target=DIR` — override target (default `/tmp/lex-fmt`). Use the default unless you have a strong reason.

### Step 3: Read from `/tmp/lex-fmt/<repo>/`

```sh
grep -rln 'foo' /tmp/lex-fmt/comms/specs/
cat /tmp/lex-fmt/vscode/package.json
```

Do not re-clone. Other steps in the same session benefit from the cache.

### Step 4: GitHub operations on siblings

`mcp__github__*` is scoped to the rooted repo and will deny calls to siblings. Use `gh` against `lex-fmt/<repo>` directly:

```sh
gh issue list -R lex-fmt/vscode
gh pr view 42 -R lex-fmt/comms
gh api repos/lex-fmt/lexed/contents/package.json
```

The user's `~/.claude/CLAUDE.md` covers this in the "Cross-repo GitHub" section — same pattern applies here.

### Step 5: Don't clean up

`/tmp/lex-fmt/` is a within-session cache. Cloud containers are ephemeral, so the cache dies with the container automatically — no manual cleanup needed. Subsequent steps and skills in the same session benefit from the cache being present.

## Output contract

`clone-lex-stack` prints a tab-separated summary on stdout — easy to parse:

```text
repo            path                            status
comms           /tmp/lex-fmt/comms              cloned
vscode          /tmp/lex-fmt/vscode             skipped-exists
lexed           /tmp/lex-fmt/lexed              setup-ok
```

`status` values: `cloned`, `refreshed`, `skipped-exists`, `setup-ok`, `setup-failed`, `clone-failed`, `locked`.

Exit codes:

- `0` — all requested repos reached a usable state.
- `1` — one or more failed (see stdout summary for which).
- `2` — invalid arguments (unknown repo name, conflicting flags).

## Fallback (if `clone-lex-stack` isn't on `$PATH`)

The env-setup snapshot may not yet include `clone-lex-stack`. To check:

```sh
command -v clone-lex-stack || echo "missing — using fallback"
```

Manual loop, same end state:

```sh
mkdir -p /tmp/lex-fmt
cd /tmp/lex-fmt
for repo in comms vscode; do
  [ -d "$repo/.git" ] || gh repo clone "lex-fmt/$repo" "$repo"
done
```

When you hit the fallback, also consider filing a `release-issue-relay` issue noting that the env-setup snapshot is missing `clone-lex-stack` so the snapshot can be refreshed.

## Distinct from `clone-lex-repos`

`bin/clone-lex-repos` (in the same release repo) is a *release-pipeline* bootstrap used by `release-core admin release lex` (retired flat: `release-lex`) — clones 7 repos into cwd including `arthur-debert/release` itself, no flags. This skill uses `clone-lex-stack` instead, which has a different repo list (8 lex-fmt repos, no `arthur-debert/release`), different default location (`/tmp/lex-fmt/`), and the flag-driven selection model needed for agent use. Don't conflate them.

## The fixed repo list

Hardcoded in `clone-lex-stack`. To add a new lex-fmt repo to the stack:

1. PR to `arthur-debert/release` updating `bin/clone-lex-stack` (and this skill's table above).
2. Bump the version comment in `env/setup.sh` to invalidate the snapshot.
3. Re-paste setup.sh into claude.ai/code to pull the new snapshot.

Q6 of [the merged plan](https://github.com/lex-fmt/lex/pull/661) covers why this is hardcoded for v1 rather than dynamic-via-`gh api`.
