Working on Release

The tricky bit is about the interaction of release and consumer repos. A few ground rules:

On Simpler Changes (no significant impact on consumers)

    1. The release project is responsible for driving adoption.
    2. "Release code is ready" is just the first step. Until it is adopted in consumer repos, it is not really ready.

    3. A full remote cycle of release and consumer repos is very slow. Validate as much as possible locally before starting the remote cycle.

        3.1. Consumer repos can specify a branch of release to use via the branch dial, so you can test changes without affecting others.
        3.2. Since all core functionality is in scripts, we can test most things locally, leaving only a thin layer in GH Actions for env vars, secrets, etc.
        3.3. Identify relevant consumers for your change and their Kind variations.
        3.4. Test locally. Once done, shepherd the PR (see skill /gh-pr-review-loop).
        3.5. Pick one locally tested consumer, update it to use your release branch, create a PR. That PR exercises the quality workflows (linting, testing).
        3.6. If release verification is important, dispatch a release candidate workflow in the consumer repo and verify it works.
        3.7. With significant assurance, merge the release PR. Then on the consumer PR, switch to main and verify everything works.
        3.8. If the change touches only tool/bin files, every other consumer on main is effectively updated on next release-sync run. Exercise judgment whether to run checks or release workflows on additional consumers.

On Consumer Repo Changes

    Sometimes we need to change things in consumer repos directly. Examples: changing file layout to match release's topology, or fixing linting errors when we merged new linters.

    For these, open a GH issue on the consumer repo tracker with full information. Then either in a separate session or via a sub-agent, tackle the work and go through the regular PR flow.

    Then the release agent checks the changes. If correct, merge.
