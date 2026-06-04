---
name: release-fleet-ops
description: "Drive and diagnose release→consumer fleet changes from inside arthur-debert/release: advancing the floating major, re-syncing the fleet, propagating a fix, and — above all — diagnosing why a consumer's CI/gate is red and routing it to the right repo. Use when doing release-side work that affects consumers, or whenever you face 'is this a release bug or a consumer bug?'. Triggered by: release-verify-fleet, orc propagate, release-advance-major, a consumer CI failure after a sync, or a fleet-wide lint/gate failure."
---

# release-fleet-ops

The operating doctrine for release-side work that touches the fleet. `release/`
produces the files consumers run **and** the config that lints them, so most
fleet failures are *release* bugs wearing a consumer's error message. This skill
encodes the loop that routes them correctly — written because the obvious
reflexes (fix the red consumer PR, spin an agent per repo) are wrong and waste
hours.

## The one rule: upstream-first

**A consumer failure is upstream (a `release/` bug) until proven consumer-specific.**
Release produces the distributed files (`templates/**`) and the gate config
(commons). If a synced file fails a consumer's gate, the file or the gate is
release's — fix it once in `release/`, not N times in consumers. Only failures
in *consumer-authored* content are consumer-specific.

The default reflex is the opposite (the red thing is the consumer's PR, so fix
it there). Resist it. Every fleet failure in the #348 saga — the gh-task-status
shim, `setup-dev-env.sh` SC2015, the gate divergence — was upstream, and every
consumer-side fix was wasted motion.

## The loop

1. **Reproduce once, in one throwaway clone.** Not an agent per repo. The fleet
   is already cloned by `release-core admin repos verify` under
   `/tmp/release-fleet-verify-$USER/`. Reset one to clean `main`, `release-sync`
   from your candidate ref, run the gate. One repo tells you what 15 would.
2. **Consult the oracle — is it upstream?** Two cheap, deterministic signals:
   - **Is the failing file release-managed?** A path that is a symlink into
     `.release/` (or lives in `templates/**`) is release's. Consumer-authored
     files (their `app-bin/`, their `src/`, their `Dockerfile`) are theirs.
   - **Does release's dogfood catch it?** Release CI lints its own distributed
     output (the `templates-bin-shellcheck` job + the `gate-unified.bats`
     dogfood). If the failure reproduces against release's own tree, it is
     upstream by definition. Red dogfood = release bug. Green dogfood + red
     consumer = consumer-specific.
3. **Route.**
   - **Upstream:** fix in `release/`, open a PR, **merge to main**, then re-sync
     (`orc propagate`) the fleet from fixed main. One fix, propagated.
   - **Consumer:** fix in the consumer repo — but first rule out a *shadow*
     (below). Genuinely-consumer-authored content debt is the only thing that
     belongs in a consumer PR.
4. **Propagate, then verify faithfully.** After an upstream fix, re-sync and let
   the *same* gate consumers run report green. The pre-flight must run what
   production runs (see "faithful pre-flight").

## The shadow trap (check this before any consumer fix)

A consumer can **override the managed gate** via `lefthook-local.yml` (lefthook
merges it on top of the synced `lefthook.yml`). These files are usually stale
workarounds someone hand-rolled for *past* gate brittleness, and they silently
shadow the fixed upstream gate — so an upstream fix appears not to land.

When a consumer stays red after an upstream fix that should have worked:

```sh
lefthook dump | grep -A6 '<the failing step>:'   # the EFFECTIVE config
ls lefthook-local.yml .lefthook/ 2>/dev/null     # the shadow
```

If `lefthook dump` shows a command that is not the synced one, a local override
is shadowing it. The fix is to delete the obsolete `lefthook-local.yml` (it
exists only because the gate used to be brittle), not to re-patch upstream.

## The tools (compose these, don't reinvent)

- `release-core admin repos verify --ref <ref>` — hermetic pre-flight: clones the
  fleet, syncs each from `<ref>`, runs the gate. Use it BEFORE
  `release-core admin release advance-major`. Its clones double as your
  reproduction sandbox. (Flat alias `release-verify-fleet` still works.)
- `orc propagate --ref main <clone>...` — re-sync N consumers and open a PR each.
  Strict: each clone must be clean and on its base branch. Reset the clones first
  (`git checkout -B main origin/main && git reset --hard && git clean -fd`).
- `orc probe --yes <clone> "<eval prompt>"` — spin ONE fresh agent to evaluate a
  repo's state and report. Use for a perspective check, not as a per-repo fixer.
- `release-core admin release advance-major` — fast-forward the floating major to
  main (ff-only). Run `release-core admin repos verify` first. (Flat alias
  `release-advance-major` still works.)

## Faithful pre-flight

A pre-flight that runs the gate differently from production lies. `lefthook run
pre-commit --all-files` and a real `git commit` do **not** behave identically
(file selection, glob `exclude` honouring). Verify with the same invocation the
consumer's CI uses, and prefer the dogfood (release CI running the canonical
gate over its own output) as the source of truth — its green equals the
consumer's green by construction.

## Anti-patterns (all observed; all costly)

- **Per-repo fix agents.** Heavyweight (clone + install + full gate + CI wait),
  most get rejected at the permission prompt, and they fix symptoms. Reproduce
  once, fix the root.
- **Consumer-first patching.** Opening consumer PRs for what is a `release/` bug
  just produces red CI in N repos. Classify before touching anything.
- **Glob whack-a-mole in the gate.** Per-file `exclude` patterns (`**/x`) drift
  across lefthook modes and can't catch extensionless non-shell files. Selection
  belongs in a content-based runner (`bin/check-shell`), not in glob/exclude.
- **Trusting a non-faithful pre-flight.** `verify-fleet --all-files` passing did
  not mean consumers would pass — it ran a different mode. Match production.
- **Status churn.** Re-querying state you already have instead of reporting it.

## Worked example: the gh-task-status shim

Symptom: 15 consumer re-sync PRs red on shellcheck `SC1071` over
`bin/gh-task-status`. Wrong path taken: fix consumers, spin agents. Right path:
`bin/gh-task-status` is a *release-managed* symlink (a Python shim release
ships) → **upstream**. The gate was shellchecking a non-shell file. Root fix:
`bin/check-shell` selects shell by content so the shim falls out; release
dogfoods it. One PR, merged, re-synced. The two consumers that *still* stayed red
had stale `lefthook-local.yml` shadows — obsolete workarounds, deleted, not
re-patched. See `docs/proposals/unified-gate.lex`.
