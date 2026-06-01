# Release token setup

Repos onboarded to the canonical `main-branch-protection` ruleset (via
`apply-ruleset`) require a Personal Access Token (PAT) for any release workflow
that pushes a version-bump commit to the default branch.

## Why a PAT is required

The ruleset blocks direct pushes to the default branch except for actors listed
in `bypass_actors`. The template ships with `RepositoryRole 5` (admin) as the
sole bypass actor.

`GITHUB_TOKEN` (the default secret available in every workflow) authenticates as
the `github-actions[bot]` Integration. Integration bypass actors are rejected on
user-owned repos and require explicit org-level install on org-owned repos —
neither path works out of the box. `RepositoryRole` bypass only matches actual
human collaborators with that role, not Integration actors, so `GITHUB_TOKEN`
cannot bypass the ruleset regardless of its workflow `permissions:` scope.

A PAT authenticates as **you** (the owner, who has admin role), which matches
the `RepositoryRole 5` bypass. Workflows that consume the PAT push successfully.

## One-time setup

1. Create a Classic PAT — pre-filled URL:

   ```
   https://github.com/settings/tokens/new?scopes=repo&description=release-bot
   ```

   Set expiry to whatever you're comfortable rotating. Click _Generate_, copy
   the token.

2. Propagate it as `RELEASE_TOKEN` to every onboarded repo:

   ```sh
   pbpaste | install-release-token
   ```

   The script auto-discovers onboarded repos by querying for the
   `main-branch-protection` ruleset, then runs `gh secret set RELEASE_TOKEN`
   against each.

## What workflows need

Release workflows that push to the default branch must check out with the PAT
and prefer it over `GITHUB_TOKEN`:

```yaml
- uses: actions/checkout@v4
  with:
    token: ${{ secrets.RELEASE_TOKEN || secrets.GITHUB_TOKEN }}
```

The `|| secrets.GITHUB_TOKEN` fallback keeps the workflow runnable in repos
that aren't onboarded yet (or in PR contexts where `RELEASE_TOKEN` isn't
exposed).

## Rotation

When the PAT expires, regenerate it (same URL) and re-run
`pbpaste | install-release-token`. Old secret is overwritten in place; no
workflow changes needed.

## Fine-grained alternative

Fine-grained PATs are scoped per resource owner, so you'd need one for
`arthur-debert/*` and a separate one for any other org/user. The setup is more
involved (per-repo selection in the form, two secrets to rotate), and the
classic `repo` scope is already narrow enough for this use case — it's only
held by an automated workflow runner, not a human.
