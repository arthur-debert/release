# `fetch-artifact` composite action

Install a pinned cross-repo artifact from a GH release. Reads the
canonical `artifacts.json` schema (see
[`docs/artifacts-schema.md`](../../../docs/artifacts-schema.md)) and
delegates to the same [`bin/fetch-artifact`](../../../bin/fetch-artifact)
CLI used locally by `scripts/setup-dev-env.sh` — CI and local paths
exercise identical code.

## Usage

```yaml
- name: Fetch lexd-lsp
  uses: arthur-debert/release/.github/actions/fetch-artifact@v1
  with:
    artifact: lexd-lsp
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The artifact is installed into `$HOME/.local/bin` by default — which
is on `$PATH` in every standard runner. Override `target` for a
checked-in location (e.g. inside `${{ github.workspace }}`).

## Inputs

| Name        | Required | Default          | Description                                                  |
|-------------|----------|------------------|--------------------------------------------------------------|
| `artifact`  | yes      | —                | Top-level key in the manifest (e.g. `lexd-lsp`).             |
| `manifest`  | no       | `artifacts.json` | Path to the manifest relative to the consumer repo root.     |
| `target`    | no       | `$HOME/.local/bin` | Install directory. Created if missing.                     |
| `arch`      | no       | (auto-detect)    | Override host arch (rust-target-triple, e.g. `x86_64-apple-darwin`). |
| `no-cache`  | no       | `false`          | Force re-download even if the version stamp matches.         |

## Outputs

| Name             | Description                                                       |
|------------------|-------------------------------------------------------------------|
| `installed-path` | Absolute path where the artifact was installed (file or dir).     |
| `version`        | Pinned version that was installed (read from the stamp file).     |

## Required env

The caller must export `GH_TOKEN` for the step that runs this action.
Composite actions can't read secrets directly; the standard pattern is:

```yaml
- uses: arthur-debert/release/.github/actions/fetch-artifact@v1
  with:
    artifact: lexd-lsp
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}   # or secrets.RELEASE_TOKEN for cross-org
```

Use `RELEASE_TOKEN` instead of `GITHUB_TOKEN` when the artifact lives
in an org other than the consumer's own, or when private-repo download
permissions are needed.

## Notes

- The action checks out at `${{ github.action_path }}` and reaches
  `bin/fetch-artifact` via `../../../bin/fetch-artifact`. Don't move
  the script without updating the action.
- Install shapes (binary vs tree) and idempotency rules match the
  CLI exactly — see `docs/artifacts-schema.md` §"Install shape".

## See also

- [`docs/artifacts-schema.md`](../../../docs/artifacts-schema.md)
- [`bin/fetch-artifact`](../../../bin/fetch-artifact)
