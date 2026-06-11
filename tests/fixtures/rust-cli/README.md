# release-canary-rust

Synthetic rust consumer exercised by the pre-cut canary loop of
[arthur-debert/release](https://github.com/arthur-debert/release) — see
[release#587](https://github.com/arthur-debert/release/issues/587). Canary runs
re-seed this repo from a candidate ref and drive a full consumer life against
it: boot (`install-release-core`), materialize (`release-core init`), the
hardened gate via `bin/check`, bats e2e against the built binary, and a real
prerelease cut. Infrastructure only — do not depend on this repo, its crates,
or its releases.
