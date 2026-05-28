Secret Management

Each project has a bunch of secrets: tokens to PyPI or crates.io, GitHub tokens, and so on. Some are shared between projects, some are specific. A few should have different values in production, CI, or local.

The standardization of workflows and environments has helped greatly: we have common names for them and mostly use the same values. But we do not store the secrets centrally, and of course one cannot read GitHub repo secrets. New projects or changes invariably lead to manually copying secrets and piping them into commands.

This is messy and error-prone.

We want to implement a secret management solution. Since we already use Doppler and it is a decent tool for small single-person setups, we will use it.

This would entail:

1. Read the consumer repos, assess secret and env var needs.
2. Plan the release project in Doppler (separate from personal accounts), with the configurations and environments needed.
3. Use the Doppler CLI locally to create and populate secrets.
4. Test how to inject them on app/container/agent session at startup (should not be hard; Doppler has a GH Action for this).
5. Build an internal release tool that populates secrets: given a consumer, determine its Kind and Capabilities (which reveal needed secrets), then from a mapping of secret name to Doppler name, loop and set them all on GitHub via gh secret set.
