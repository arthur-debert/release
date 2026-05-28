Foundational Architecture Review

There are some subtler points on release's design that have been lost, and before we move forward with more features and iterations, it is good to lay them out clearly.

At its core, release is a way for repos to declaratively tell what they are (a Kind: npm package, Electron app, mkdocs docs, etc.), and with a light configuration file, get a rich set of features for free: testing, building, releasing, etc.

The problem is that the present cut of Kinds, Capabilities, and features are less decoupled and clear than they need to be. For example: building Windows apps and signing macOS binaries should be features on top of others, composable. Likewise mkdocs and so forth.

Aside from general design confusion, it is probably worth breaking out more formally what is under the release pipeline:

1. Release Cutting: release prep, changelog, versions, validations.
2. Building: build per platform all the binaries/artifacts (can be wasm, npm package, crate, etc).
3. Packing: taking the build artifacts and packing them for release, i.e. signing macOS binaries, creating Electron packages, etc.
4. Releasing: make this available on GitHub Releases (the base distribution, where others can pull from).
5. Publishing: pushing to package managers (Homebrew, crates.io, npm, etc).

Side Note: Deeper Research Into The Ecosystem

    Two common use cases that seem widespread enough that good solutions likely exist:

    1. A CLI compiled for various platforms and architectures: package and distribute it. That would handle OS signing, packaging (brew, nix, apt) and maybe even publishing to app stores. Something like goreleaser.
    2. The same for GUI apps.
