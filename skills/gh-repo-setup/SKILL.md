---
name: gh-repo-setup
description: "Bring a GitHub repo up to the canonical arthur-debert/* + lex-fmt/* release-loop setup: main-branch protection ruleset (PR required, linear history, required checks), per-stack policy files (CODEOWNERS, dependabot.yml, copilot-instructions.md, pull_request_template.md, workflows/copilot-review.yml). No PATH dependency on `~/h/release/bin/`; does clone the public `arthur-debert/release` repo at runtime to read the canonical templates. Idempotent for the policy sweep (re-runs report `ok` for unchanged files); ruleset application is a PUT-replace so it always reports `updated`, content-diff-aware `unchanged` is a known follow-up. Use when: onboarding a new repo, verifying an existing repo is still aligned, recovering from audit-repo drift, or when an agent reports missing branch-protection or copilot auto-trigger."
---

# gh-repo-setup

Portable equivalent of `release-core admin policy ruleset` + `release-core admin policy sweep` + `detect-kind` (flat aliases `apply-ruleset` / `sweep-github-policy`). Brings a repo up to the canonical release-loop setup. Idempotent for the policy sweep: re-running on an already-set-up repo reports `ok` for every file. The ruleset application is a PUT-replace and always reports `updated` (content-diff-aware `unchanged` is a known follow-up — see Pitfalls).

## When to use

- **Onboarding a new repo** to the `arthur-debert/*` or `lex-fmt/*` portfolio.
- **Verifying alignment** — quick way to check whether a repo has drifted from canonical.
- **Recovery** — when `release-core audit` (flat: `audit-repo`) reports a repo is missing pieces.

If you have `~/h/release/bin/` on `$PATH` (local Claude Code, dodot-set-up), prefer the local scripts — they're the same logic but quicker to invoke. This skill exists for cloud sessions and any environment without that PATH.

## Prerequisites

- **Bash 4+** (the snippets use arrays, process substitution, and ANSI-C `$'...\n...'` quoting — not POSIX `sh` / `dash` compatible). Bash 4+ is what cloud Ubuntu and macOS-with-`brew install bash` provide.
- `gh` CLI authenticated with `repo + read:org` scope on the target repo. In cloud sessions, the `GH_TOKEN` env var (set by your Claude env config) handles this — make sure the PAT covers the target repo with at minimum `Administration: write` (for ruleset application), `Contents: write` (for the policy-file PR), and `Pull requests: write`.
- `jq` available (pre-installed in cloud sessions).
- `yq` for parsing existing workflow files. Pre-installed in cloud; install locally with `brew install yq` if missing.
- Network access to clone the public `arthur-debert/release` repo (no auth needed — it's public).

Each numbered step below is meant to run as its own bash invocation (or one combined script). The blocks use `set -euo pipefail` and `exit 1` on fatal errors, so don't `source` them — invoke as `bash` (or copy into a single sectioned script).

## Setup

Cache the release repo into a unique temp directory once per skill invocation. Subsequent steps read templates and ruleset JSON from this clone:

```sh
set -euo pipefail

TARGET_REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
TARGET_ROOT=$(git rev-parse --show-toplevel)
RELEASE_CLONE=$(mktemp -d -t arthur-debert-release-setup.XXXXXX)
git clone --depth 1 https://github.com/arthur-debert/release.git "$RELEASE_CLONE"
echo "target: $TARGET_REPO at $TARGET_ROOT"
echo "clone:  $RELEASE_CLONE"
```

`mktemp -d` produces a unique path (avoids collisions if multiple invocations run concurrently) and lives outside any predictable location an attacker could target on a shared system.

## Step 1: detect stack

Inspects the target repo's filesystem to pick the right template set. Output: one of `rust`, `electron`, `vsce-ext`, `nvim-plugin`, `tree-sitter`, `static-site`, `brew-tap`, `github-action`. Exits non-zero if undetermined.

```sh
detect_stack() {
  local dir=${1:-.}
  cd "$dir"
  if [ -d Formula ] || [ -d Casks ]; then echo brew-tap; return; fi
  if [ -f grammar.js ]; then echo tree-sitter; return; fi
  if [ -f Cargo.toml ]; then echo rust; return; fi
  if [ -f package.json ]; then
    if grep -q '"electron-builder"\|"electron"' package.json 2>/dev/null; then
      echo electron; return
    fi
    if grep -q '"@vscode/vsce"\|"vsce"' package.json 2>/dev/null; then
      echo vsce-ext; return
    fi
  fi
  if [ -f action.yml ] || [ -f action.yaml ]; then echo github-action; return; fi
  if [ -d plugin ] && find . -maxdepth 3 -name '*.lua' -print -quit | grep -q .; then
    echo nvim-plugin; return
  fi
  if [ -f book.toml ] || [ -f _config.yml ]; then echo static-site; return; fi
  echo "could not detect stack of $(pwd)" >&2
  return 1
}

STACK=$(cd "$TARGET_ROOT" && detect_stack)
echo "stack: $STACK"
```

If `detect_stack` errors out, the repo doesn't match any known pattern — either pass `STACK=<stack>` manually based on what you know about the project, or stop and ask the user.

## Step 2: apply the main-branch protection ruleset

Applies `rulesets/main-protection.json.tmpl` from the cloned release repo, with `required_status_checks` populated from the target's actual workflow runs (not just job IDs — this captures matrix expansion and `name:` overrides).

### 2a. Auto-detect required checks

```sh
set -euo pipefail

# Collect workflows that trigger on pull_request (excluding copilot-review.yml).
#
# Note the `(.on // .true)` fallback: YAML 1.1 (and mikefarah/yq prior to recent
# versions) parses the bareword key `on:` as the boolean true, so the resulting
# JSON has `{"true": ...}` instead of `{"on": ...}`. Handling both shapes lets
# this work across yq versions and across consumer repos whose `.yml`/`.yaml`
# choice may affect parser behavior.
PR_WORKFLOWS=()
if [ -d "$TARGET_ROOT/.github/workflows" ]; then
  while IFS= read -r f; do
    base=$(basename "$f")
    case "$base" in
      copilot-review.yml|copilot-review.yaml) continue ;;
    esac
    triggers=$(yq -o json . "$f" 2>/dev/null | jq -r '
      (.on // .true) as $on |
      if   ($on | type) == "string" then [$on]
      elif ($on | type) == "array"  then $on
      elif ($on | type) == "object" then ($on | keys)
      else [] end | .[]
    ' 2>/dev/null)
    if echo "$triggers" | grep -qx pull_request; then
      PR_WORKFLOWS+=(".github/workflows/$base")
    fi
  done < <(find "$TARGET_ROOT/.github/workflows" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \))
fi

# Get the actual check-run names from the latest default-branch run of each.
# Paginate the workflows listing so repos with many workflows aren't truncated.
DEFAULT_BRANCH=$(gh api "repos/$TARGET_REPO" --jq .default_branch)
CHECKS=$(
  for path in "${PR_WORKFLOWS[@]}"; do
    wid=$(gh api --paginate "repos/$TARGET_REPO/actions/workflows" \
      --jq ".workflows[] | select(.path == \"$path\") | .id" 2>/dev/null)
    [ -n "$wid" ] && [ "$wid" != "null" ] || continue
    run_id=$(gh api "repos/$TARGET_REPO/actions/workflows/$wid/runs?branch=$DEFAULT_BRANCH&per_page=1" \
      --jq '.workflow_runs[0].id' 2>/dev/null)
    [ -n "$run_id" ] && [ "$run_id" != "null" ] || continue
    gh api --paginate "repos/$TARGET_REPO/actions/runs/$run_id/jobs" \
      --jq '.jobs[].name' 2>/dev/null
  done | sort -u
)

# Fallback: if no workflow runs exist (brand-new repo), use static job IDs from yq.
if [ -z "$CHECKS" ]; then
  CHECKS=$(
    for path in "${PR_WORKFLOWS[@]}"; do
      yq -r '.jobs | keys | .[]' "$TARGET_ROOT/$path" 2>/dev/null
    done | sort -u
  )
fi

echo "required checks: $(echo "$CHECKS" | paste -sd, -)"
```

If `$CHECKS` is empty (no workflows yet), pass them manually — set `CHECKS=$'check-one\ncheck-two'` (newline-separated) before step 2b.

### 2b. Build the ruleset payload and apply

```sh
set -euo pipefail

TMPL="$RELEASE_CLONE/rulesets/main-protection.json.tmpl"
[ -f "$TMPL" ] || { echo "ruleset template missing in clone — check the clone succeeded" >&2; exit 1; }

CHECKS_JSON=$(printf '%s\n' "$CHECKS" | jq -R 'select(. != "") | {context: .}' | jq -s .)

PAYLOAD=$(jq --argjson c "$CHECKS_JSON" '
  .rules |= map(
    if .type == "required_status_checks"
    then .parameters.required_status_checks = $c
    else . end
  )
' "$TMPL")

RULESET_NAME=$(jq -r '.name' <<<"$PAYLOAD")
# Paginate in case the repo has accumulated many rulesets.
EXISTING_ID=$(gh api --paginate "repos/$TARGET_REPO/rulesets" \
  --jq ".[] | select(.name == \"$RULESET_NAME\") | .id" | head -1 || true)

echo "ruleset: $RULESET_NAME (existing id: ${EXISTING_ID:-none})"

if [ -n "$EXISTING_ID" ]; then
  # Idempotent path — PUT replaces the existing ruleset with the payload.
  # If the payload matches what's there, this is effectively a no-op.
  jq . <<<"$PAYLOAD" | gh api -X PUT "repos/$TARGET_REPO/rulesets/$EXISTING_ID" --input - --silent
  echo "ruleset: updated"
else
  jq . <<<"$PAYLOAD" | gh api -X POST "repos/$TARGET_REPO/rulesets" --input - --silent
  echo "ruleset: created"
fi
```

Dry-run mode: skip the final `gh api -X PUT/POST` block and just `echo "$PAYLOAD" | jq .` to inspect.

## Step 3: sweep policy files

Drop the canonical policy + setup files into the target repo. Compare before copying so we can report `ok`, `created`, `updated`, or `conflict`.

### Source layout (path-mirror)

Sources live under two subtrees:

- `release/templates/commons/**` — synced to every consumer
- `release/templates/<stack>/**` — synced to consumers of that stack

The destination in each consumer is the source path with the `templates/commons/` or `templates/<stack>/` prefix stripped. For example, `templates/rust/.github/dependabot.yml` lands at `.github/dependabot.yml`; `templates/commons/scripts/setup-dev-env.sh` lands at `scripts/setup-dev-env.sh`.

No destination map. To add a new managed file, drop it under the right subtree at the path you want it to land. Files outside `commons/` and `<stack>/` (e.g. `templates/fragments/`, `templates/render/`) are never synced.

Stack-specific paths win on collision with commons (a stack may specialize a shared file).

```sh
set -euo pipefail

COMMONS="$RELEASE_CLONE/templates/commons"
STACK_DIR="$RELEASE_CLONE/templates/$STACK"
[ -d "$STACK_DIR" ] || { echo "no templates for stack '$STACK' at $STACK_DIR" >&2; exit 1; }

FORCE=${FORCE:-0}   # set FORCE=1 if you want conflicts overwritten
CREATED=0; UPDATED=0; SKIPPED=0; CONFLICTS=0

cd "$TARGET_ROOT"

process_subtree() {
  local prefix=$1
  [ -d "$prefix" ] || return 0
  while IFS= read -r src; do
    local dest=${src#"$prefix"/}
    mkdir -p "$(dirname "$dest")"
    if [ ! -e "$dest" ]; then
      cp "$src" "$dest"
      [ -x "$src" ] && chmod +x "$dest"
      echo "  created   $dest"
      CREATED=$((CREATED + 1))
    elif cmp -s "$src" "$dest"; then
      echo "  ok        $dest"
      SKIPPED=$((SKIPPED + 1))
    elif [ "$FORCE" = 1 ]; then
      cp "$src" "$dest"
      [ -x "$src" ] && chmod +x "$dest"
      echo "  updated   $dest"
      UPDATED=$((UPDATED + 1))
    else
      echo "  conflict  $dest  (differs; set FORCE=1 to overwrite)"
      CONFLICTS=$((CONFLICTS + 1))
    fi
  done < <(find "$prefix" -type f -not -name '.DS_Store')
}

# Commons first, stack second — stack overrides on collision.
process_subtree "$COMMONS"
process_subtree "$STACK_DIR"

printf 'policy files: %d created, %d updated, %d ok, %d conflicts\n' \
  "$CREATED" "$UPDATED" "$SKIPPED" "$CONFLICTS"
```

**Drift note for the local `bin/sweep-github-policy`:** at the time of writing, the bin/ version still hardcodes `dest=".github/$rel"` for *all* template files. That worked when templates only contained `.github/`-bound files, but became wrong when `scripts/` and `lefthook.yml` were added (PR #8). The skill above is the corrected logic; the bin/ version needs the same fix. Tracked as a follow-up issue.

If any files were created or updated, the target repo now has uncommitted changes. Commit them on a branch and open a PR through the standard flow (`gh pr create` → Auto-fix → review → merge); the policy files are part of the repo's history once that PR merges.

## Cleanup

```sh
rm -rf "$RELEASE_CLONE"
```

## Triage rules for the output

The end-state of a successful invocation:

| Outcome | Interpretation |
|---|---|
| `ruleset: updated` + all `ok` | Repo was already aligned. No-op confirmation. |
| `ruleset: created` + all `created` | Brand-new onboarding. Commit the .github/ changes, open the PR, merge. |
| `ruleset: updated` + mix of `created`/`ok` | Partial drift. Review the created files; commit the additions; investigate any items that were missing. |
| Any `conflict` | The repo has a customized version of a canonical file. Either resolve manually, or re-run with `FORCE=1` to overwrite — but only after deciding the customization isn't worth keeping. |

A clean idempotent re-run reports `ok` for every file and either `updated` (PUT replaced with same content) or `created` (new) for the ruleset. The `updated` line for an unchanged-content ruleset is not great UX — see Pitfalls.

## Pitfalls

- **`ruleset: updated` doesn't tell you whether the content actually changed.** The GH API's PUT-replace doesn't emit a diff; we report `updated` for any successful PUT. To detect actual drift, fetch the existing ruleset (`gh api "repos/$REPO/rulesets/$ID"`) and diff against `$PAYLOAD` before deciding to PUT. Skipped here for simplicity; add it if you want a "no change needed" report.
- **YAML 1.1 `on:` boolean footgun.** GitHub Actions uses `on:` as the trigger key, but under YAML 1.1 (and older mikefarah/yq) the bareword `on` is parsed as the boolean literal `true`. So `yq -o json` may emit `{"true": ...}` instead of `{"on": ...}`. The check-detection jq uses `(.on // .true)` to handle both shapes. If you see `PR_WORKFLOWS` come back empty on a repo that definitely has PR-triggering workflows, this is the likely cause — check the `yq` output directly.
- **`copilot-review.yml` is excluded from the required-checks detection** by filename. It's a side-effect workflow (requests Copilot), not a gate — listing it as required would make every PR block on it.
- **No `--checks` override here yet.** If `detect_stack` succeeds but `$CHECKS` is empty (no PR-trigger workflows or no workflow runs), set `CHECKS=$'name1\nname2'` manually before step 2b.
- **`.DS_Store` is skipped** in the policy sweep (macOS artifact in the templates).
- **Conflicts require manual resolution by default.** The `FORCE=1` override exists but bypassing a conflict means the consumer's customization is gone. Only force after seeing what's different.
- **Templates live under `release/templates/<stack>/`.** Only `rust/` exists today; per-stack templates for electron, vsce-ext, nvim-plugin, etc. are Phase 2 work. If `detect_stack` returns a stack that has no templates yet, step 3 errors out — that's the expected behavior until Phase 2 ships those templates.
- **The skill clones the public release repo via HTTPS** without auth. If release ever becomes private, the clone needs `gh auth setup-git` or `git clone https://x-access-token:$GH_TOKEN@github.com/...` instead.

## Related local-only scripts (not ported here)

These are not reimplemented in this portable skill because they touch multiple repos at once and aren't useful from inside a single repo's session — run them via `release-core admin …` (flat aliases shown in parens) on a machine with the release tooling on `$PATH`:

- `release-core admin secrets install` (flat: `install-release-secrets`) — propagate the canonical secrets set to every onboarded repo.
- `release-core admin secrets token` (flat: `install-release-token`) — propagate `RELEASE_TOKEN` to every onboarded repo.
- `release-core admin policy dependabot` (flat: `enable-dependabot-security`) — enable Dependabot vulnerability alerts portfolio-wide.
- `release-core admin repos audit` (flat: `audit-portfolio`), `release-core audit` (flat: `audit-repo`), `release-core admin smoke-test` (flat: `audit-smoke-test`) — read-only auditing.
- `release-core admin policy sweep` (flat: `sweep-github-policy`) — the local wrapper.

If a cloud agent needs any of these, that's a signal to escalate via the `release-issue-relay` skill (Phase 1.3) rather than reimplementing them piecemeal.
