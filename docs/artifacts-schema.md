# `artifacts.json` schema

Canonical cross-repo artifact-pin file. Every onboarded repo that
consumes a binary or source-tree artifact from another repo's GitHub
release declares those pins in a single `artifacts.json` at the repo
root. The fetcher (composite action + CLI, tracked at #60 — Wave 1.2)
reads this schema and resolves the pins to actual downloads. Paths
referenced below as `.github/actions/fetch-artifact/` and
`bin/fetch-artifact` land in that PR; until it merges, treat those
references as forward-looking.

This file is the **interface contract**. Anything that fetches
cross-repo artifacts in the portfolio is expected to read this schema.

## Location

- Path: **`artifacts.json` at the repo root** (visible — it's
  project metadata, not dotfile state; should be reviewed in PRs
  the same way `package.json` or `Cargo.toml` is).
- Filename is fixed; the fetcher does not accept an override by
  default. (The CLI's `--manifest` flag exists for testing and
  multi-manifest experiments; production code should leave the path
  at the default.)

## Schema

JSON object. Each top-level key is an **artifact name** (a string,
conventionally lowercase-with-hyphens). The value is an object with
the fields below.

```json
{
  "<artifact-name>": {
    "version": "vX.Y.Z",
    "repo": "<org>/<repo>",
    "asset": "<asset-pattern>"
  }
}
```

| Field     | Required | Type   | Description |
|-----------|----------|--------|-------------|
| `version` | yes      | string | Semver string with a mandatory `v` prefix, matching the GH release tag (e.g. `v0.14.1`, `v1.0.0-rc.3`). The fetcher resolves the release by exact tag — not range, not "latest". |
| `repo`    | yes      | string | `<org>/<repo>` slug. Used by `gh release download --repo`. |
| `asset`   | no       | string | Asset-name pattern for the release. Supports the `{arch}` substitution (see below). **Default:** `<artifact-name>-{arch}.tar.gz`. |
| `type`    | no       | string | `binary` (default) or `tree`. Selects install shape — see "Install shape" below. Explicit > auto-detect, so use this when the archive contents could change shape upstream (e.g. a binary release gaining a `LICENSE` file shouldn't flip the install mode). |

Top-level keys outside the artifact-name set are **reserved**. The
fetcher should validate that all top-level values are objects matching
the schema above and error on unknown shapes; future schema versions
may use top-level metadata keys (e.g. `$schema`), and silently
ignoring them now would create a forward-compat trap. Comments are not
supported (it's JSON, not JSONC) — put rationale in a `README.md` or
a separate doc if needed.

## `{arch}` substitution

When the `asset` pattern contains the literal substring `{arch}`, the
fetcher substitutes the host's rust-target-triple at fetch time. The
host detection rule is `uname -s` + `uname -m`:

| `uname -s` | `uname -m`        | `{arch}` substitution            |
|------------|-------------------|----------------------------------|
| `Linux`    | `x86_64`          | `x86_64-unknown-linux-gnu`       |
| `Linux`    | `aarch64`         | `aarch64-unknown-linux-gnu`      |
| `Darwin`   | `x86_64`          | `x86_64-apple-darwin`            |
| `Darwin`   | `arm64`           | `aarch64-apple-darwin`           |
| `MINGW*` / `MSYS*` (Windows) | `x86_64` | `x86_64-pc-windows-msvc` |
| `MINGW*` / `MSYS*` (Windows) | `aarch64` | `aarch64-pc-windows-msvc` |

If the pattern does **not** contain `{arch}`, it is used verbatim. This
is the right shape for source-tree artifacts (one tarball serves all
hosts) and wasm artifacts (host-agnostic by nature).

**musl variants.** Some artifacts ship both `*-linux-gnu` and
`*-linux-musl` builds (lex's `lexd-lsp` is the canonical example).
Pick the variant explicitly via the `asset` field — there's no
implicit musl detection. Example:

```json
{
  "lexd-lsp-musl": {
    "version": "v0.14.1",
    "repo": "lex-fmt/lex",
    "asset": "lexd-lsp-{arch}-musl.tar.gz"
  }
}
```

## Examples

### Binary-per-arch (the common case)

```json
{
  "lexd-lsp": {
    "version": "v0.14.1",
    "repo": "lex-fmt/lex"
  }
}
```

Equivalent (with explicit default asset):

```json
{
  "lexd-lsp": {
    "version": "v0.14.1",
    "repo": "lex-fmt/lex",
    "asset": "lexd-lsp-{arch}.tar.gz"
  }
}
```

Resolves on Linux x86_64 to
`https://github.com/lex-fmt/lex/releases/download/v0.14.1/lexd-lsp-x86_64-unknown-linux-gnu.tar.gz`.

### Source tree (no arch — single tarball)

```json
{
  "tree-sitter-lex": {
    "version": "v0.10.3",
    "repo": "lex-fmt/tree-sitter-lex",
    "asset": "tree-sitter.tar.gz"
  }
}
```

Note `tree-sitter.tar.gz`, not `tree-sitter-lex.tar.gz` — the asset
field overrides the default-from-name pattern when the artifact name
differs from the actual asset name.

### Wasm bundle (no arch — host-agnostic)

```json
{
  "phos-color-wasm": {
    "version": "v0.4.0",
    "repo": "arthur-debert/phos-core",
    "asset": "phos-color-wasm-v0.4.0.tar.gz"
  }
}
```

(The version in the asset name is a current phos-core convention; if
phos-core later drops the version-in-filename, the asset field
shortens to `phos-color-wasm.tar.gz`.)

### Multiple artifacts in one manifest

```json
{
  "lexd-lsp": {
    "version": "v0.14.1",
    "repo": "lex-fmt/lex"
  },
  "tree-sitter-lex": {
    "version": "v0.10.3",
    "repo": "lex-fmt/tree-sitter-lex",
    "asset": "tree-sitter.tar.gz"
  }
}
```

This is what `lex-fmt/vscode`, `lex-fmt/nvim`, `lex-fmt/lexed` will
look like post Wave-3 migration (today they have the older
`shared/lex-deps.json` shape; the data is the same, the file moves).

## Install shape

The fetcher needs to know whether the archive contains a single
binary (move to `<target>/<name>`) or a directory tree (extract to
`<target>/<name>/`). Two ways to declare it, in priority order:

1. **Explicit `type` field** in the manifest entry: `"type":
   "binary"` or `"type": "tree"`. Recommended when there's any
   ambiguity, or when the upstream archive might evolve (e.g. a
   binary release gaining a `LICENSE` file that would confuse
   auto-detect).
2. **Auto-detect** if `type` is omitted. The fetcher extracts the
   archive and looks for an executable file whose name matches the
   artifact name. Match → `binary`. No match → `tree`. The
   "executable matching artifact-name" rule is robust against
   `LICENSE`/`README`/sibling files appearing in binary releases.

## Fetcher behavior summary

(Full details land in #60 — `bin/fetch-artifact` and
`.github/actions/fetch-artifact/`.)

- Reads `./artifacts.json` (or `--manifest <path>`).
- Looks up `<artifact-name>` key. If missing → exit non-zero, no-op.
- Resolves `{arch}` from host detection (or `--arch <override>`).
- Runs `gh release download <version> --repo <repo> --pattern <asset>`
  into a temp dir.
- Resolves install shape per "Install shape" above (explicit `type`
  wins; otherwise auto-detect via executable-name match).
- Installs:
  - `binary`: chmod 0755, move to `<target>/<name>`, write
    `<target>/.<name>.version` stamp file.
  - `tree`: extract under `<target>/<name>/`, write
    `<target>/<name>/.version` stamp file.
- Idempotency: if the version-stamp file matches the requested
  version, exit 0 without downloading. Override with `--no-cache`.

## Why not other shapes

Alternatives considered + dropped:

- **Per-language manifests (re-using `Cargo.toml`, `package.json`)** —
  these don't model "fetch a binary from a GH release" cleanly. Cargo
  has no first-class binary-dep concept; npm `bin` is for things npm
  installs, not arbitrary GH releases. We'd be back-fitting.
- **Manifest-per-artifact (one file per binary)** — N files for what's
  conceptually one declaration. Real consumers (lex-fmt/nvim) pin 2+
  artifacts in one logical block; splitting them adds files for no
  benefit.
- **Inline pins in `setup-dev-env.sh`** — what we had before. The
  reason we're standardising is that this hides pins from review and
  bumps require touching the script.
- **YAML / TOML instead of JSON** — JSON wins because every language's
  stdlib parses it, and `jq` is universally available. The pin file
  is read by shell scripts more often than by humans; choose the
  shape that minimises tooling cost.

## Migration from `shared/lex-deps.json`

The existing `lex-fmt/{vscode,nvim,lexed}` repos carry
`shared/lex-deps.json` (flat schema for vscode/nvim, nested for lexed).
These move to canonical `artifacts.json` at the repo root during the
**Wave 3** migration sweep (one PR per repo).

### Before (e.g. `lex-fmt/nvim/shared/lex-deps.json`, flat schema)

```json
{
  "lexd-lsp": "v0.14.1",
  "lexd-lsp-repo": "lex-fmt/lex",
  "tree-sitter": "v0.10.3",
  "tree-sitter-repo": "lex-fmt/tree-sitter-lex"
}
```

### After (e.g. `lex-fmt/nvim/artifacts.json`, canonical schema)

```json
{
  "lexd-lsp": {
    "version": "v0.14.1",
    "repo": "lex-fmt/lex"
  },
  "tree-sitter-lex": {
    "version": "v0.10.3",
    "repo": "lex-fmt/tree-sitter-lex",
    "asset": "tree-sitter.tar.gz",
    "type": "tree"
  }
}
```

The cascade-handler's `update-release` primitive learns to write the
new shape; the fetcher reads the new shape. Old `shared/lex-deps.json`
is deleted once the repo's `update-release` is updated.

## References

- Fetcher implementation (Wave 1.2 in flight): #60 — lands as
  `bin/fetch-artifact` + `.github/actions/fetch-artifact/`
- Cascade architecture: [`docs/lex-release-cascade.md`](lex-release-cascade.md)
- Tracking: #59 (this schema), #60 (fetcher), #61 (cascade-handler)
