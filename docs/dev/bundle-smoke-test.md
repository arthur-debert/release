# Bundle integration smoke test (#841)

Runs the **real** package-job `tauri bundle` path against a **pre-built** tauri
binary and asserts each format produces a valid, correctly-named bundle. It
catches bundling / dep-set regressions (e.g. the slim-deps AppImage break, where
`linuxdeploy-plugin-gtk` needs the GTK-stack pkg-config `.pc` files the runtime
`-0` libs don't provide) **before** a ~15-minute RC instead of after.

## Why it exists

The `tests/tauri-pipeline` BATS suite stubs `npx`/`hdiutil`, so it never runs a
real bundle — every real-tauri behavior (linuxdeploy deps, per-format output,
reseal) is invisible until an RC. This is the integration test that fills that
gap. It would have **failed the slim-deps PR pre-merge**.

## What it reuses (does not reimplement)

`resolve-tauri-bundles.sh` → per-format `bundle-tauri.sh` → `collect-tauri-bundles.sh`
→ `assert-tauri-bundle-binary.sh` (the #817 main-binary identity guard), then adds
per-format existence + non-trivial-size + name assertions (`bundle-smoke.sh`).

## Fixture: on-demand, no committed fixture

`resolve-compile-artifact.sh` pulls the **latest non-expired** consumer
`compile-<platform>` artifact (the binary + frontendDist a real release run
uploaded) via `gh`, and restores it with the package job's own
`restore-tauri-compile.sh`.

The resolver walks **all** artifact pages (`gh api --paginate`) so a busy
consumer can't push the newest artifact off page 1, and **pins the consumer
checkout to the artifact run's `head_sha`** (best-effort; `fetch --depth 1` for
shallow clones) so the source tree matches the pre-built binary.

**Limitation:** GitHub artifacts retain ~7 days. If the consumer hasn't built
recently there is no artifact, and the resolver **fails loud** (exit 3) — never a
silent skip (that would be false confidence). Re-run after the consumer cuts or
builds.

## Run it locally (one command)

The dev box is a mac, so the linux path runs in `ubuntu:24.04` Docker (needs
Docker + a `gh` login):

```sh
bin-internal/bundle-smoke-local.sh
# or target a different consumer / formats:
CONSUMER_REPO=phos-editor/app FORMATS="deb appimage" bin-internal/bundle-smoke-local.sh
```

It clones the consumer, installs the package-job deps + node/pnpm/cargo (container
bootstrap the GH runner already ships) + `file` (appimagetool needs `file(1)`),
sets `APPIMAGE_EXTRACT_AND_RUN=1` (no FUSE in a container — does not change the
dep answer), restores the artifact, and runs `bundle-smoke.sh`.

The mac `dmg`/reseal path runs natively (out of Docker) — not yet wired into the
local wrapper.

## CI gate

`.github/workflows/bundle-smoke-tests.yml` runs the same logic on the ubuntu
runner (linux, ships `file(1)`, has FUSE — no Docker), pulling the consumer
artifact with `RELEASE_TOKEN`. It gates PRs touching the bundle scripts,
`install-tauri-linux-deps*.sh`, `tauri-app.yml`, or the harness itself.
