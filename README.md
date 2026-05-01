# release

Reusable GitHub Actions workflows for releasing software across the
arthur-debert / lex-fmt project ecosystem. One canonical pipeline per
artifact category; consumers call them with a thin `with:` block.

## Categories

| Workflow | Status | Consumers |
|---|---|---|
| `rust-cli.yml` | in development | dodot, padz, simple-gal, rustloc, burgertocow, lex-fmt/lex |
| `rust-lib.yml` | planned | clapfig, standout |
| `electron-app.yml` | planned | lex-fmt/lexed, lightable/simple-gal-ui |
| `vscode-ext.yml` | planned | lex-fmt/vscode |
| `editor-plugin-source.yml` | planned | lex-fmt/nvim |
| `tree-sitter-grammar.yml` | planned | lex-fmt/tree-sitter-lex |
| `gh-action.yml` | planned | lightable/simple-gal-action |
| `static-site-mdbook.yml` | planned | standout |
| `static-site-jekyll.yml` | planned | lex-fmt/comms |

## Versioning

| Bump | Trigger |
|---|---|
| PATCH (`v1.2.3` → `v1.2.4`) | bug fix in any composite action, no input changes |
| MINOR (`v1.2.x` → `v1.3.0`) | new optional input, new opt-in feature, new category workflow |
| MAJOR (`v1.x.x` → `v2.0.0`) | required-input rename, default behavior change, removed input |

Tags: plain `vX.Y.Z`. Floating major: `v1` branch always points at latest
non-breaking. Consumers pin `@v1` for floating, `@v1.2.3` for exact.

## Layout

```
.github/
  workflows/         # reusable workflows (one per category)
  actions/           # composite actions (atomic units, shared across workflows)
scripts/             # tools that composite actions exec
templates/           # default render templates (e.g. Homebrew formula)
tests/fixtures/      # tiny synthetic projects per category, exercised by _ci.yml
docs/                # consumer guide, secrets, breaking-changes log
examples/            # paste-ready consumer release.yml files
```

## See also

- [Secrets and onboarding](docs/secrets.md)
- [Per-category input shapes](docs/per-category/)
- [Breaking changes log](docs/breaking-changes.md)
- Companion script: `~/h/dotfiles/gh/bin/install-release-secrets`
