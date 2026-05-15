---
name: pr-review-respond
description: "Reply to and resolve PR review comments using `gh` + `jq` — no other local helper scripts required. Use when processing Copilot or human review feedback on a PR in Claude Code Cloud, CI agents, or any environment without ~/h/release/bin scripts on $PATH. Triggered by: 'address review comments', 'resolve review threads', 'reply to Copilot', or any iteration on PR review feedback."
---

# pr-review-respond

Read, reply to, and resolve PR review comments using `gh` and `jq`. Self-contained — no dependency on the `gh-*` helper scripts in `~/h/release/bin/` (those aren't available in Claude Code Cloud sessions or any environment that hasn't sourced the dotfiles). Required deps: authenticated `gh` CLI and `jq`.

## When to use

- Processing Copilot or human review feedback on a PR.
- Resolving review threads after fix-and-push or rationale-reply.
- Any environment without `~/h/release/bin/` on `$PATH`.

If you're working locally with the helper scripts available, the broader `gh-pr-review-loop` skill is a superset — it adds Copilot request/wait, check waiting, and onboarding flows. This skill is the comment-handling subset, in a form that works anywhere `gh` is authenticated.

## Cloud session note: prefer `gh` over MCP for cross-repo and thread resolution

In Claude Code on the web, the `github` MCP server is hard-scoped to the session's rooted repo. Cross-repo issue/PR access via MCP returns `Access denied: repository ... not configured for this session`. The `gh` CLI (authed via `GH_TOKEN`) reaches every repo the PAT covers and is the right tool whenever:

- You need to read or write to a repo other than the rooted one (cross-repo issue creation, status PR comments, the relay pattern).
- You need to enumerate review thread `PRRT_*` IDs to pass to `resolveReviewThread` (the GitHub MCP server drops thread IDs from its `pull_request_read` / `get_review_comments` response — tracked at [github/github-mcp-server#2331](https://github.com/github/github-mcp-server/issues/2331), fix in open PR [#2245](https://github.com/github/github-mcp-server/pull/2245)). Use the step-1 GraphQL query in this skill via `gh api graphql`.

The MCP scope is enforced server-side, not at the PAT level, so using `gh` for these operations is not a security bypass — it's the documented route for cross-repo work in Claude Code on the web. Ask the user before using `gh` only if your session's system prompt explicitly forbids it (some session-spawning paths inject "MCP only" instructions that override skill guidance).

## The three primitives

All commands assume you're inside the PR's repo. Set up once (replace `123` with the PR number — don't paste `<pr-number>` literally, the shell parses `<` as redirection):

```sh
PR=123
OWNER=$(gh repo view --json owner -q .owner.login)
REPO=$(gh repo view --json name -q .name)
```

### 1. List unresolved review threads

```sh
gh api graphql -F owner="$OWNER" -F name="$REPO" -F pr="$PR" -f query='
  query($owner: String!, $name: String!, $pr: Int!) {
    repository(owner: $owner, name: $name) {
      pullRequest(number: $pr) {
        reviewThreads(first: 100) {
          nodes {
            id
            isResolved
            path
            line
            originalLine
            comments(first: 20) {
              nodes {
                databaseId
                author { login }
                body
              }
            }
          }
        }
      }
    }
  }' | jq '[.data.repository.pullRequest.reviewThreads.nodes[]
           | select(.isResolved == false)
           | { threadId: .id,
               path: .path,
               line: (.line // .originalLine),
               firstCommentId: .comments.nodes[0].databaseId,
               comments: [.comments.nodes[] | { author: .author.login, body: .body }] }]'
```

The output gives you everything needed to triage and act:
- `threadId` — the `PRRT_*` GraphQL ID to pass to `resolveReviewThread` (step 3).
- `firstCommentId` — the numeric REST `databaseId` to POST replies against (step 2). Always the root comment, even when the thread has follow-up replies.
- `comments[]` — full thread history in order, so you see the latest reviewer comment plus any earlier replies. The newest comment (`comments[-1]`) usually carries the most current request.
- `path`, `line` — for triage.

Note: this fetches the first 100 review threads (GraphQL caps `first` at 100). PRs with more than 100 threads are extremely rare here; if you ever hit one, paginate with `pageInfo.endCursor`.

### 2. Reply to a comment

Use `jq` to build the JSON payload, pipe to `gh api --input -`. This is the only form that reliably handles arbitrary multi-line markdown (double quotes, backticks, dollar signs, code blocks) across shells:

```sh
COMMENT_ID=<firstCommentId from step 1>

BODY=$(cat <<'EOF'
Reply markdown here. Multiple lines fine.

For rationale-style pushbacks, end with a searchable line so future passes can find it:
Recording for future review passes: don't ask us to <X>.
EOF
)

jq -n --arg body "$BODY" '{body: $body}' \
  | gh api "repos/$OWNER/$REPO/pulls/$PR/comments/$COMMENT_ID/replies" \
      -X POST --input -
```

Why this form and not `-f body=@-` with a heredoc: empirically, `gh api ... -f body=@- <<EOF ... EOF` can silently send the literal string `@-` as the body instead of reading from stdin (observed on `gh 2.x` across `bash` and `zsh` heredoc forms — the failure mode is silent because gh returns 201 created with body `"@-"`). The `jq | gh api --input -` form avoids the ambiguity by handing gh a fully-formed JSON request body.

For scripted batch replies (e.g. processing N threads in a loop), the equivalent in Python is straightforward and avoids shell quoting entirely:

```python
import json, subprocess
subprocess.run(
    ["gh", "api", f"repos/{owner}/{name}/pulls/{pr}/comments/{cid}/replies",
     "-X", "POST", "--input", "-"],
    input=json.dumps({"body": reply_text}),
    text=True, check=True,
)
```

The endpoint **does** include `pull_number` in the path — that's the documented form for replies, even though comment IDs are repo-unique. An empty body errors out.

### 3. Resolve the thread

```sh
THREAD_ID=<threadId from step 1, looks like "PRRT_kw...">

gh api graphql -F threadId="$THREAD_ID" -f query='
  mutation($threadId: ID!) {
    resolveReviewThread(input: { threadId: $threadId }) {
      thread { isResolved }
    }
  }'
```

GitHub does **not** auto-resolve threads when you push a fix or reply. Without this step, every addressed comment stays "Unresolved" through every subsequent round and the PR becomes unreadable. Resolve aggressively.

## Triage rules

For each unresolved thread, pick one:

**A) Real, project-specific issue → fix the code, push, resolve the thread.** The diff is the proof. Examples seen in this ecosystem:
- `cargo clippy -D warnings` must be `cargo clippy -- -D warnings` (the `--` forwards `-D` to rustc).
- `permissions: { pull-requests: write }` alone removes default `contents: read`; add `contents: read` explicitly.
- Fork PRs need a `github.event.pull_request.head.repo.fork == false` guard before posting reviewers.

**B) Project ethos drift → rationale-reply, then resolve. Do not change the file.** End the reply with `Recording for future review passes: don't ask us to <X>.` so it's grep-able next round. Examples that always get pushback in this ecosystem:
- "Pin org-internal reusable workflows to a SHA." Same owner controls both repos; pinning defeats the "fix once, propagate" point.
- "Per-repo customize the multi-repo template." The template is intentionally generic — pointing at only what's local defeats its purpose.
- "Match fallback flags exactly to CI." The fallback is a generic approximation; CI is the source of truth and varies per project.

**C) Cosmetic nit in already-merged style → resolve without replying.** The thread is closed because the codebase has settled on a different convention; an empty reply isn't useful, but leaving the thread "Unresolved" pollutes the next pass. Only push back (and leave open until you've replied) if the same nit recurs across PRs — at that point, encode the project convention in `copilot-instructions.md` so it stops being raised.

Healthy end state: only genuinely contested threads (and the original review summary, which isn't itself a thread) remain unresolved.

## Getting notified when reviews land (Claude Code on the web)

Cloud sessions don't poll for new review comments on their own. After opening a PR, choose one of:

1. **Auto-fix (recommended)** — Claude subscribes to PR webhook events and runs a session response when reviews arrive or CI fails. Per-PR opt-in. Activate via any of:
   - The CI status bar in the cloud session that just opened the PR → click **Auto-fix**
   - `/autofix-pr` from a local terminal while on the PR branch
   - "watch this PR and auto-fix any review comments" in a fresh cloud session, pasting the PR URL
   - The mobile app, same natural-language instruction
   
   Requires the [Claude GitHub App](https://github.com/apps/claude) installed on the repo's org. Once on, Claude reacts to each new review comment automatically; this skill's flow runs when the agent decides to address them. Docs: [Auto-fix pull requests](https://code.claude.com/docs/en/claude-code-on-the-web#auto-fix-pull-requests).

2. **Manual poll** — if Auto-fix isn't installed or appropriate for the repo, re-run the step-1 "list unresolved review threads" query periodically. When the count increases, address the new threads.

Local Claude Code (CLI/Desktop) has no webhook subscription equivalent; either poll manually, or in repos with `~/h/release/bin/` on PATH use the broader `gh-pr-review-loop` skill which wraps `gh-copilot-wait` for the Copilot-specific path.

## After fixup pushes

CI re-runs automatically on push. **Do not** re-request Copilot for minor follow-up rounds — the canonical `copilot-review.yml` workflow only auto-triggers on `pull_request: [opened, ready_for_review]`, and one Copilot review per PR is the convention. Re-request manually only if the round is substantial:

```sh
gh pr edit "$PR" --add-reviewer @copilot
```

(`requested_reviewers` REST POST silently no-ops for Copilot — must go through `gh pr edit`, which uses GraphQL with the bot's real node_id.)

### In a cloud session: stacked sub-PR pattern

In Claude Code on the web, your session is on an orchestrator-assigned branch (`claude/<task>-XXXXX`) and **cannot push fixups directly to an existing PR's feature branch** — the git-push auth is scoped to your session branch. The canonical workaround:

1. Make the fix on your session branch.
2. Open a draft sub-PR targeting the original PR's feature branch (not main). Gemini reviews drafts automatically; flip to ready when you want Copilot too. Use `--body` to name it as a stacked PR up-front so the human reviewer doesn't read it as a duplicate:
   ```sh
   FEATURE_BRANCH="<the original PR's branch — quoted defensively in case it contains a slash or colon>"
   gh pr create --draft \
     --base "$FEATURE_BRANCH" \
     --title "fixup: address review on #<original-PR>" \
     --body "Stacked sub-PR addressing review feedback on #<original-PR>. Squash-merge into \`$FEATURE_BRANCH\`; the original PR picks up the new commits automatically."
   ```
3. Drive the sub-PR through review the same way (enable Auto-fix on it, address Gemini, flip to ready, address Copilot, resolve).
4. Squash-merge the sub-PR into the feature branch. The original PR picks up the new commits automatically.

To bypass the orchestrator entirely on a one-off, `/teleport` the session to local Claude Code and push directly to the original branch. See `~/.claude/CLAUDE.md` (the user-level instructions installed by the env setup script) for the broader rules of the road.

## Stop at "ready to merge"

When all addressed threads are resolved and checks are green, the comment-handling phase is done. Report status and stop. The user does the final read and merges.

```sh
gh pr view "$PR" --json mergeStateStatus,mergeable
# Ready when: mergeStateStatus=CLEAN, mergeable=MERGEABLE
```

Wait for checks before declaring done:

```sh
gh pr checks "$PR" --watch
```

Merge only on explicit authorization from the user ("merge it", "go ahead and merge", "merge when green"). Then:

```sh
gh pr merge "$PR" --squash --delete-branch
```

## Pitfalls

- **REST `databaseId` vs GraphQL `id` are different namespaces.** The reply REST endpoint takes the numeric `databaseId`; the resolve mutation takes the `PRRT_*` string. Don't cross them.
- **`line` is null for outdated/multi-line comments.** Fall back to `originalLine` (the step-1 query already does this).
- **Already-resolved thread → mutation returns `isResolved: true`.** Safe to no-op; check `isResolved` upstream if you want to skip the call entirely.
- **Empty reply body errors out.** Always send text.
- **`-f body=@-` with a heredoc silently posts the literal string `@-`.** The reply endpoint returns 201 created and you only notice when you read the comment back. Use `jq -n --arg body "$BODY" '{body: $body}' | gh api ... --input -` instead (see §2 above). Same pattern needed for any `gh api` call passing arbitrary multi-line content.
- **Copilot `requested_reviewers` REST add silently no-ops.** Use `gh pr edit --add-reviewer @copilot`.
- **Don't `--admin` merge unprompted.** If the PR is blocked by an unrelated pre-existing CI failure, surface it and ask before bypassing.
