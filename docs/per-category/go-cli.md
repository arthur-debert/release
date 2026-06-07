# go-cli

The reusable workflow at `.github/workflows/go-cli.yml@v2` covers the
release lifecycle for a Go CLI: roll the changelog, push tag, create GH
release, build cross-target binaries (with optional macOS sign +
notarize), and render + push a Homebrew formula. Differences from
`rust-cli`:

- **No manifest version bump.** Go has no canonical version field in
  `go.mod`; the tag itself is the source of truth. Version metadata is
  injected at build time via `-ldflags "-X <pkg>.<var>=X.Y.Z"` when
  `version-package` is set.
- **No registry publish step.** Go modules are pulled from Git tags by
  downstream consumers directly — no equivalent of `cargo publish`.
- **Cross-compilation is free.** `GOOS=linux GOARCH=arm64 go build` Just
  Works from a `ubuntu-latest` runner; no cross-toolchain step is
  needed. (macOS targets still run on `macos-14` so codesign +
  notarize use the native keychain + `xcrun`.)

## Caller shape

```yaml
name: Release
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to release (e.g. 0.1.0 or 1.0.0-rc.1)'
        required: true
        type: string

permissions:
  contents: write

jobs:
  release:
    uses: arthur-debert/release/.github/workflows/go-cli.yml@v2
    with:
      version:         ${{ inputs.version }}
      bin-name:        my-cli                    # built from ./cmd/my-cli by default
      version-package: github.com/me/my-cli/internal/version
      brew-desc:       'My CLI for doing the thing'
      brew-license:    MIT
    secrets: inherit                             # same-owner; cross-org needs explicit pass
```

## Inputs

### Required

| Input | Description |
|---|---|
| `version`  | `MAJOR.MINOR.PATCH[-PRERELEASE]`. Pre-release suffixes mark the GH release as `prerelease: true`. |
| `bin-name` | Primary binary, also the Homebrew formula subject. Built from `./cmd/<bin-name>` by default. |

### Optional

| Input | Default | Description |
|---|---|---|
| `build-path` | `./cmd/<bin-name>` | Override the Go package path to build for the primary binary. |
| `extra-binaries` | `''` | Comma-separated additional binaries to build, sign, and ship as separate tarballs. Each entry is either `name` (built from `./cmd/<name>`) or `name=./pkg/path`. NOT brew-formulated. |
| `darwin-archs` | `arm64` | Comma-separated macOS archs. Set `''` to skip macOS. |
| `linux-archs` | `x86_64,aarch64` | Comma-separated Linux archs. |
| `windows-archs` | `''` | Comma-separated Windows archs (opt-in). |
| `brew` | `true` | Render and push Homebrew formula to tap. |
| `brew-tap` | `arthur-debert/homebrew-tools` | Tap repo to push the formula to. |
| `brew-desc` | `''` (required when `brew: true`) | Formula `desc` line (single line, ≤79 chars, no trailing period). |
| `brew-license` | `''` (required when `brew: true`) | License identifier (e.g. MIT, Apache-2.0). |
| `brew-homepage` | `''` (defaults to GitHub repo URL) | Homepage URL for the formula. |
| `go-version` | `''` (read from `go.mod`) | Go version to install. Override only when `go.mod`'s `go` directive isn't a real released version. |
| `version-package` | `''` | Go import path of the package whose `version` variable receives the release version via `-ldflags -X`. Empty = no injection. |
| `version-variable` | `Version` | Name of the string variable inside `version-package` to set. |
| `submodules` | `false` | Pass `--recurse-submodules` on checkout. |
| `changelog-path` | `CHANGELOG.md` | Path to Keep-a-Changelog-format file with `## [Unreleased]` section. |

## Brew metadata

Unlike `rust-cli` (which reads `desc` / `license` / `homepage` from
`Cargo.toml`), Go has no canonical metadata location, so the brew
formula's three header lines must come from explicit inputs. The
workflow fails fast at the homebrew job if `brew-desc` / `brew-license`
are empty and `brew: true`.

The `homebrew-formula.rb.tmpl` template and the
`render-brew-formula` / `push-brew-tap` composite actions are shared
verbatim with `rust-cli`; the formula format is identical regardless of
source language.

## Version injection

The canonical pattern: a tiny `internal/version` package with an
exported `Version` variable, wired to a `--version` CLI flag.

```go
// internal/version/version.go
package version

// Version is the release version. Overridden at build time via
// -ldflags "-X github.com/<owner>/<repo>/internal/version.Version=X.Y.Z".
// `dev` is what `go build` produces locally without ldflags — the value
// surfaces in `<bin> --version` for un-tagged builds.
var Version = "dev"
```

Then in the workflow caller:

```yaml
with:
  version-package: github.com/<owner>/<repo>/internal/version
```

The build step runs:

```
go build -trimpath -ldflags="-s -w -X <pkg>.Version=<tag-version>" -o <bin> ./cmd/<bin>
```

The Homebrew formula's `test` block calls `<bin> --version` and matches
the rendered version, so this wiring is required for the formula to
pass `brew test`.

## Secrets

| Secret | Required when | Purpose |
|---|---|---|
| `RELEASE_TOKEN` | always (recommended) | PAT with `repo` + `read:org`. Bypasses branch ruleset for the changelog-roll push. Without it, falls back to `GITHUB_TOKEN`, which can't bypass the ruleset on ruleset-protected repos. |
| `APPLE_CERTIFICATE_P12_BASE64` | macOS sign | Developer ID Application certificate, base64-encoded. |
| `APPLE_CERTIFICATE_PASSWORD` | macOS sign | Password for the .p12. |
| `ASC_API_KEY_BASE64` | macOS notarize | App Store Connect API key (.p8), base64-encoded. |
| `ASC_API_KEY_ID` | macOS notarize | API key ID. |
| `ASC_API_ISSUER_ID` | macOS notarize | App Store Connect issuer ID. |
| `HOMEBREW_TAP_TOKEN` | brew formula push | PAT with write access to the tap repo. |

`bin/install-release-secrets` propagates all of these from canonical
local sources (`~/h/dotfiles/apple/auth/...`, env vars) to every
onboarded repo.

## Job graph

```
setup-matrix ─┐
              ├─► build-binaries ──► homebrew-formula
prepare ──────┤
              └─► create-release
```

`prepare` rolls the changelog, commits, tags, and pushes (or, on
resume, validates that the existing tag matches the requested version
and skips the bump). `build-binaries` is a per-target matrix job that
cross-compiles, optionally signs+notarizes on macOS, packages into
`<bin>-<target>.tar.gz` (or `.zip` for Windows), uploads to the GH
release, and stashes the artifact for the brew job. `homebrew-formula`
gathers the SHA256s of every tarball, renders the formula against the
shared template, and pushes to the tap repo.
