On Internal Tooling

This repo is about the shared infrastructure for development projects. These are all source controlled via git and hosted on GitHub. More than hosted there, it builds on top of GitHub's platform: PRs, PR reviews, releases, issues.

Many internal tasks and investigations deal with handling these repos:

- Cleaning up git branches (locally and remotely)
- Listing PRs with status (reviews, unresolved comments, etc) (bin/list-repo-pr)
- Listing files and dirs then presenting them
- The audit commands (audit-repo, audit-portfolio)
- The workflows
- Gathering errors in workflow runs for fixing
- Gathering information from CI for improving workflows (caching, etc)

Currently some of these are implemented, others not. They are mostly hackish shell scripts which all reinvent the basics (getting repos, mapping to local dirs, etc).

We could:
- Move this to Python scripts.
- Leverage good deps: ghapi, click, rich.

The only caveat: make the Python scripts self-bootstrapping. If deps are not installed, error and warn the user to pass --bootstrap, then install deps and restart.

This should entail a common set of primitives:
- Getting all consumer repos (with filtering)
- Local/remote path mapping (when possible)
- Listing PRs, PR status (our definition: reviews pending? unresolved comments? mergeable? CI checks pass?)
- Listing files in dirs
- Listing branches with ahead/behind information
