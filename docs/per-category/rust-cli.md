# rust-cli

The reusable workflow at `.github/workflows/rust-cli.yml@v2` covers the
full release lifecycle for a Rust CLI workspace: bump versions, push tag,
create GH release, build cross-target binaries (with optional macOS
sign + notarize), publish crates to crates.io, render and push a
Homebrew formula, and (opt-in) build a wasm-bindgen workspace member
via wasm-pack and publish to npm.

## Caller shape

```yaml
name: Release
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to release (e.g. 0.18.0 or 1.0.0-rc.1)'
        required: true
        type: string

permissions:
  contents: write

jobs:
  release:
    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
    with:
      version: ${{ inputs.version }}
      crates: my-core,my-cli              # topological dep order
      bin-name: my-cli                    # primary binary == brew formula subject
    secrets: inherit                      # same-owner; cross-org needs explicit pass
```

Cross-org consumers (e.g. `lex-fmt/*` calling `arthur-debert/release`)
must list every secret explicitly under `secrets:` — `inherit` only
propagates within the same owner. Some consumers also need to map
secret names (e.g. `CRATES_IO_KEY: ${{ secrets.CARGO_REGISTRY_TOKEN }}`)
when their existing secret is named differently from the workflow's
input.

## Inputs

### Required

| Input | Description |
|---|---|
| `version` | `MAJOR.MINOR.PATCH[-PRERELEASE]`. Pre-release suffixes mark the GH release as `prerelease: true`. |
| `crates` | Comma-separated crate names in publish order (topological — dependencies first). The last entry is the primary CLI crate by convention. |
| `bin-name` | Primary binary, also the Homebrew formula subject. Built from `cargo build -p <bin-name>`. |

### Optional

| Input | Default | Description |
|---|---|---|
| `extra-binaries` | `''` | Comma-separated additional binaries to build, sign, and ship as separate tarballs (NOT brew-formulated). E.g. `lexd-lsp` for lex. |
| `darwin-archs` | `arm64` | Comma-separated macOS archs. Set `''` to skip macOS. |
| `linux-archs` | `x86_64,aarch64` | Comma-separated gnu-linux archs. |
| `linux-musl-archs` | `''` | Comma-separated musl-linux archs (opt-in). |
| `windows-archs` | `''` | Comma-separated windows archs (opt-in). |
| `brew` | `true` | Render and push Homebrew formula to tap. |
| `apt` | `true` | Build `.deb` packages for linux targets via cargo-deb. |
| `brew-tap` | `arthur-debert/homebrew-tools` | Tap repo to push the formula to. |
| `submodules` | `false` | Pass `--recurse-submodules` on checkout. |
| `changelog-path` | `CHANGELOG.md` | Path to Keep-a-Changelog-format file with `## [Unreleased]` section. |

### WASM/npm slot (opt-in)

Set `wasm-package` to enable. Two new jobs slot in: `build-wasm` (after
`prepare`, before downstream consumers) and `publish-npm` (after
`build-wasm`, downloads the prebuilt artifact — no second Rust install).
When `wasm-package` is unset, behavior is byte-identical to a non-wasm
release.

| Input | Default | Description |
|---|---|---|
| `wasm-package` | `''` | Workspace member with a wasm-bindgen crate. Set to enable wasm-pack build + npm publish. |
| `wasm-pack-target` | `bundler` | `wasm-pack --target` value: `bundler` \| `web` \| `nodejs` \| `no-modules`. |
| `wasm-npm-scope` | `''` | npm org scope. When set, publishes as `@<scope>/<wasm-package>`; when unset, publishes the bare crate name. |

#### What ships when `wasm-package` is set

1. **`build-wasm` job** runs once after `prepare` on `ubuntu-latest`.
   Reuses the warm Swatinem cache, runs `wasm-pack build <crate-path>
   --target <wasm-pack-target> [--scope <wasm-npm-scope>] --out-dir
   pkg`, paranoia-checks `pkg/package.json` version against the tag,
   and:
   - Attaches `<wasm-package>-wasm.tar.gz` to the GH release (alongside
     native binary tarballs — non-npm consumers can `curl` it just like
     editor extensions grab `lexd-lsp`).
   - Uploads `pkg/` as the `wasm-pkg` workflow artifact for the
     downstream `publish-npm` job.

2. **`publish-npm` job** downloads the `wasm-pkg` artifact and runs
   `npm publish --access public` against `registry.npmjs.org`. No Rust
   install — it's a pure node + npm step.

If `NPM_TOKEN` isn't set on a wasm-enabled call, `publish-npm` warns
and skips (with a noticeable annotation), but the wasm tarball is still
attached to the GH release. This is the homebrew-tap pattern — opt-in
secrets, opt-out gracefully.

## Secrets

| Secret | Required when | Purpose |
|---|---|---|
| `RELEASE_TOKEN` | always (recommended) | PAT with `repo` + `read:org`. Bypasses branch ruleset for the version-bump push. Without it, falls back to `GITHUB_TOKEN`, which can't bypass the ruleset on ruleset-protected repos. |
| `CRATES_IO_KEY` | publishing crates | crates.io API token. |
| `APPLE_CERTIFICATE_P12_BASE64` | macOS sign | Developer ID Application certificate, base64-encoded. |
| `APPLE_CERTIFICATE_PASSWORD` | macOS sign | Password for the .p12. |
| `ASC_API_KEY_BASE64` | macOS notarize | App Store Connect API key (.p8), base64-encoded. |
| `ASC_API_KEY_ID` | macOS notarize | API key ID. |
| `ASC_API_ISSUER_ID` | macOS notarize | App Store Connect issuer ID. |
| `HOMEBREW_TAP_TOKEN` | brew formula push | PAT with write access to the tap repo. |
| `NPM_TOKEN` | wasm-package set + npm publish | npm publish token (Automation type recommended). |

`bin/install-release-secrets` propagates all of these from canonical
local sources (`~/h/dotfiles/apple/auth/...`, env vars) to every
onboarded repo.

## Job graph

```
setup-matrix ─┐
              ├─► build-binaries ──► homebrew-formula
prepare ──────┤
              ├─► create-release ──┬─► publish-crates
              │                    └─► build-wasm ──► publish-npm
              └─► (no other deps)
```

## Example: WASM-enabled caller (lex-fmt/lex)

```yaml
jobs:
  release:
    uses: arthur-debert/release/.github/workflows/rust-cli.yml@v2
    with:
      version: ${{ inputs.version }}
      crates: lex-core,lex-babel,lex-config,lex-analysis,lex-lsp-core,lexd-lsp,lexd
      bin-name: lexd
      extra-binaries: lexd-lsp
      linux-musl-archs: x86_64
      windows-archs: x86_64
      submodules: true
      wasm-package: lex-wasm
      wasm-npm-scope: lex-fmt
    secrets:
      RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}
      APPLE_CERTIFICATE_P12_BASE64: ${{ secrets.APPLE_CERTIFICATE_P12_BASE64 }}
      APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
      ASC_API_KEY_BASE64: ${{ secrets.ASC_API_KEY_BASE64 }}
      ASC_API_KEY_ID: ${{ secrets.ASC_API_KEY_ID }}
      ASC_API_ISSUER_ID: ${{ secrets.ASC_API_ISSUER_ID }}
      CRATES_IO_KEY: ${{ secrets.CARGO_REGISTRY_TOKEN }}
      HOMEBREW_TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
```

The result: one tag push, one workflow run, one operator dashboard
view that says "did v0.10.4 publish?" One artifact built once,
distributed to four channels (crates.io, GH release, Homebrew, npm).
