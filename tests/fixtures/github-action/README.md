# release-canary-gh-action

Synthetic GitHub Action consumer exercised by the pre-cut canary loop of
[arthur-debert/release](https://github.com/arthur-debert/release) — see
[release#587](https://github.com/arthur-debert/release/issues/587) and
[release#786](https://github.com/arthur-debert/release/issues/786). Canary runs
re-seed this repo from a candidate ref and drive a full consumer life against
it: boot (`install-release-core`), install (`release-core init`), the gh-action
lint gate (actionlint + shell/markdown/yaml), and a real prerelease cut through
`gh-action.yml` (tag + GH release; the floating-major advance is skipped for
prereleases).

Infrastructure only — do not depend on this repo, its action, or its releases.

## Usage

```yaml
- uses: arthur-debert/release-canary-gh-action@v0
  with:
    who: release
```
