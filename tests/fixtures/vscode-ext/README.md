# release-canary-vscode-ext

Synthetic VS Code extension consumer exercised by the pre-cut canary loop of
[arthur-debert/release](https://github.com/arthur-debert/release) — see
[release#587](https://github.com/arthur-debert/release/issues/587). Canary runs
re-seed this repo from a candidate ref and drive a full consumer life against
it: boot (`install-release-core`), materialize (`release-core init`), the
hardened gate + unit tests via `bin/check`, and a real prerelease cut that
packages a universal VSIX (every marketplace publish path fenced).
Infrastructure only — do not depend on this repo, its extension, or its
releases.
