# Captured-fixture provenance — the convention + the ratchet lint

A fixture that represents an **external surface** must be *captured reality*
with auditable provenance, not the author's belief about that surface. This
doc is the convention; `release_core.captured_fixtures` is the forcing-function
lint that enforces it (prose discipline loses to momentum).

## The defect class this kills

Three green-suite/wrong-world bugs in 24 hours (2026-06-11/12), all the same
shape: a mock or fixture encodes the author's belief about an external surface,
then the suite verifies the production code against that belief. The suite is
self-consistently **wrong** — every test passes while the real surface differs.

The worked example is **#619**:

> `classify.py` parsed the pinned lefthook's summary block with a `🥊 <name>`
> regex — a tty-mode guess. Lefthook 2.1.9's summary glyphs are mode-dependent:
> interactive runs render `🥊` fail / `✔️` pass, but **captured (piped) runs
> render `✗` (U+2717) / `✓` (U+2713)** — and the classifier only ever consumes
> captured logs (verify/poke/canary run the gate via subprocess capture). The
> unit fixture used the same *invented* glyph, so 1,300 tests were
> self-consistently green while the live fleet sweep classified every
> npm-toolchain FAIL as unexpected. Caught only on the first post-merge fleet
> verify.

The fix made the fixture the **verbatim block from a live verify-gate log**, so
it can never silently fall out of sync with the real surface again. That fixture
is the model. Its sisters: **#620** (a test seam stubbed below the layer it needed to
exercise) and **#612** (mocks only ever fed well-formed manifests).

The only oracle for an external surface is captured reality. So:

## The convention

**Any fixture representing an external surface carries a machine-readable
provenance marker.** The marker token is `captured-from`, written in one of
two surface-dependent forms — a top-level `"captured-from"` JSON key (no
colon) for JSON file fixtures, or a `captured-from:` comment for inline/YAML
fixtures. Either form is followed by the producing command or source **and a
date**. Examples:

- A JSON file fixture — a top-level key:

  ```json
  {
    "captured-from": "gh pr view 342 --json ... + gh api graphql (arthur-debert/release#342, 2026-06-11)",
    "meta": { "...": "..." }
  }
  ```

- An inline test constant — a marker comment near the constant:

  ```python
  # captured-from: the piped output of the pinned lefthook (2.1.9), verbatim
  # from a live verify-gate log (lex-fmt/vscode, 2026-06-11). #619.
  _GATE_LOG = (
      "...verbatim captured bytes...\n"
  )
  ```

A fixture may live inline (a test-file constant with the marker comment) or as a
file under the seam's fixture dir.

### What counts as an "external surface"

The producer is **outside our code** and we do not control its exact bytes:

- lefthook / gate logs (piped, mode-dependent glyphs)
- gh / GitHub API payloads (GraphQL `reviewThreads`, REST `/pulls/*/reviews`,
  status-check rollups, the empty `reviewRequests` even when a bot is engaged)
- workflow YAML as read via the GitHub contents API (and parsed by `yq`)
- git command output

A fixture an engine consumes **as if it came from that producer** must be
captured from the producer, with the marker recording where.

### Capture, don't invent

The marker's value must name a *real* capture command. A hand-shaped fixture is
exactly the belief-not-evidence problem — it does not get a `captured-from:`
marker (that would lie). If a scenario genuinely cannot be captured yet, it is
**baselined** (see below) as known debt, never marked.

## The lint — a shrink-only ratchet

`release_core.captured_fixtures` sweeps a **narrow, explicitly-registered** set
of seam sources (the design core: we never guess "is this external?"
repo-wide). It is wired into release's own gate via
`bin-internal/lint-captured-fixtures.sh` (a `lefthook.yml` pre-commit check,
also run by CI through the same `lefthook run pre-commit --all-files`).

The ratchet is modelled byte-for-byte on the consumer-contract lint
(`release_core.contract.apply_baseline`):

- today's unmarked offenders are grandfathered in
  `templates/commons/lib/release_core/tests/captured-fixture-lint-baseline.yaml`;
- a **NEW** unmarked external-surface fixture (not in the baseline) **fails**;
- a baseline entry that no longer matches any offender **also fails** ("stale —
  delete it"): once a fixture gets its marker, its baseline entry must be
  removed.

**For already-registered seams the baseline only ever shrinks.** Once a seam is
registered, adding a baseline entry is never the fix for a new finding — capture
the fixture from the real producer and add the marker instead. The one moment
the baseline *grows* is the deliberate, reviewed one-shot when a NEW seam is
registered (below): its pre-existing unmarked fixtures are grandfathered in the
same PR. After that, the ratchet for that seam is monotonically downward.

### The registered seams

Defined in `release_core/captured_fixtures.py` (`SEAMS` + `INLINE_SEAMS`):

| Seam | Source | Surface |
|---|---|---|
| `prstate` | `tests/prstate_fixtures/*.json` | gh / GitHub API PR payloads |
| `classify` | `tests/test_core_classify.py` (`_GATE_LOG`) | pinned-lefthook piped summary (#619) |
| `apply_ruleset` | `tests/test_core_apply_ruleset.py` | workflow YAML via the contents API |

To register a new seam, add it to `SEAMS` (a fixture dir) or `INLINE_SEAMS` (a
test file with a captured constant) and re-run the lint; any unmarked fixture it
finds either gets captured-with-marker or, if it is pre-existing debt, a baseline
entry (the only time the baseline grows is the one-shot at seam registration).

## Draining the baseline

Each entry names a synthetic fixture awaiting real capture. To drain one:

1. Re-capture the payload from the real producer — e.g. for a prstate fixture,
   `gh pr view <n> --json ...` plus `gh api graphql ...` against a live PR that
   exhibits the scenario.
2. Add the `captured-from:` key naming that command + date.
3. Delete the matching line from the baseline file (the lint will demand this —
   a now-marked fixture makes its baseline entry stale).

## Running the lint

```sh
bin-internal/lint-captured-fixtures.sh
# or directly:
PYTHONPATH=templates/commons/lib/release_core \
  python3 -m release_core.verbs.captured_fixtures lint
```

It runs the **working-tree** `release_core` (never the installed wheel), so a PR
that adds a fixture or changes the seam registry lints against its own code.
