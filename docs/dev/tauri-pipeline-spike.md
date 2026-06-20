# Tauri pipeline spike — build/bundle decoupling + post-hoc `.dmg` signing

WS0 of epic #811 (issue #812). A throwaway feasibility spike, not a
production workflow. It answers two unknowns that decide the WS3/WS4
design:

1. Can the **compile** step and the **bundle** step be decoupled — ideally
   into separate *jobs* on separate runners — so the cross-job artifact is
   the ~8 MB binary, not the multi-GB `target/`?
2. Can a `.dmg` be bundled **unsigned**, then signed + notarized + stapled
   **post-hoc** (so the signing leg is its own reusable step gated on
   credentials)?

Verified locally on darwin (arm64), tauri-cli 2.10.0, cargo 1.94.1, against
a minimal `npm create tauri-app@latest` vanilla app. Notarization (the one
leg needing Apple Connect creds) is deferred to CI — see "Notarization".

## Verdict

| Question | Verdict |
|---|---|
| (a) Packaging as its own **job** (separate runner) | **GO** |
| (b) Post-hoc `.dmg` signing (sign after bundle) | **GO-WITH-CAVEAT** |

- **(a) GO.** `tauri bundle` is a first-class subcommand ("Generate bundles
  and installers for your app (already built by `tauri build`)"). It packages
  a pre-built binary with **no recompile**, and works on a runner that has
  **only the binary** in `target/release/` (the other ~860 MB of `target/`
  deleted). The cross-job artifact is the binary + the source-side bundle
  inputs (`src-tauri/tauri.conf.json`, `src-tauri/icons/`, the frontend
  `frontendDist`) — tens of MB. So WS3/WS4 can make `package` a separate
  **job**, not just a step.
- **(b) GO-WITH-CAVEAT.** Post-hoc `codesign` of the inner `.app` with the
  real Developer ID, re-packaging into a `.dmg`, and signing the `.dmg` all
  pass `codesign --verify --deep --strict`. **Caveat:** you must **not**
  re-run `tauri bundle --bundles dmg` to re-seal — that regenerates the
  `.app` from the binary and **strips your signature**. Build the dmg from
  the signed `.app` with `hdiutil` instead (commands below). The notarize +
  staple leg needs Apple Connect creds (CI secrets) and is the only part not
  confirmed locally.

## (a) Build/bundle decoupling

### Compile only (no bundle)

```sh
npx tauri build --no-bundle
# => Built application at: src-tauri/target/release/spikeapp   (8.2 MB Mach-O arm64)
```

### Bundle the pre-built binary (no recompile)

```sh
npx tauri bundle --bundles app,dmg
# Bundling spikeapp.app  (target/release/bundle/macos/spikeapp.app)
# Bundling spikeapp_0.1.0_aarch64.dmg  (target/release/bundle/dmg/...)
```

Runs in seconds — it consumes `target/release/spikeapp`, does not rebuild.

### Cross-runner proof (the job-vs-step decider)

Simulate a fresh runner that has only the compiled binary:

```sh
# keep ONLY target/release/spikeapp; delete the rest of target/ (868M -> 8.2M)
find src-tauri/target -mindepth 1 -maxdepth 1 ! -name release -exec rm -rf {} +
find src-tauri/target/release -mindepth 1 -maxdepth 1 ! -name spikeapp -exec rm -rf {} +

npx tauri bundle --bundles app,dmg     # still succeeds — produces .app + .dmg
```

So the **package job** needs, as its downloaded artifact:

- `src-tauri/target/release/<binary>` (the compiled Mach-O, ~8 MB)
- `src-tauri/tauri.conf.json` (+ any `tauri.macos.conf.json` overlay)
- `src-tauri/icons/`
- the frontend `frontendDist` directory (the app's built web assets)

It does **not** need the `~860 MB` `target/` cache, `node_modules`, or a Rust
toolchain. Tens of MB cross-job, well inside artifact limits.

> Sizes observed: binary 8.2 MB · full `target/` 868 MB · frontend dist 20 KB.

### Linux

`tauri bundle --bundles deb,appimage` is the same decoupled entry point; deb /
AppImage only build on Linux, so they were not exercised on this darwin box.
Same shape applies: compile job uploads the Linux binary, a Linux package job
runs `tauri bundle`.

## (b) Post-hoc `.dmg` signing

A valid **Developer ID Application** identity was present on this Mac
(`security find-identity -v -p codesigning` listed one), so the real-identity
signing leg was exercised — not ad-hoc. Examples below use a placeholder
identity / team id; substitute your own:

```
Developer ID Application: <Your Name> (<TEAMID>)   (1 valid identity)
```

A freshly bundled `.app` is **adhoc / linker-signed** (`TeamIdentifier=not
set`); the `.dmg` is unsigned. `tauri bundle --no-sign` makes the unsigned
state explicit (the WS4 "bundle unsigned" entry point).

### Step 1 — sign the inner `.app` post-hoc (hardened runtime)

```sh
ID="Developer ID Application: <Your Name> (<TEAMID>)"
APP=src-tauri/target/release/bundle/macos/spikeapp.app
codesign --force --options runtime --timestamp --deep --sign "$ID" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"   # valid + satisfies Designated Requirement
```

Result: `flags=0x10000(runtime)`, `Authority=Developer ID Application: …`,
`TeamIdentifier=<TEAMID>`, secure `Timestamp` present.

### Step 2 — build the `.dmg` from the SIGNED app (hdiutil, NOT tauri)

The caveat. **Do not** re-run `tauri bundle --bundles dmg` here — it
regenerates `spikeapp.app` from the binary and reverts it to adhoc, discarding
the Developer ID signature (verified: the re-bundled dmg's inner app showed
`flags=…(adhoc,linker-signed)`, `TeamIdentifier=not set`). Build the dmg
manually so the signature survives:

```sh
stage=$(mktemp -d); out="$PWD/spikeapp-signed.dmg"
cp -R "$APP" "$stage"/
ln -s /Applications "$stage"/Applications
hdiutil create -volname "spikeapp" -srcfolder "$stage" -ov -format UDZO "$out"
codesign --force --timestamp --sign "$ID" "$out"
codesign --verify --verbose=2 "$out"                  # valid + satisfies Designated Requirement
```

Verified after: mounting the signed dmg shows the inner `.app` **still** has
`Authority=Developer ID Application: …` and `TeamIdentifier=<TEAMID>`
(`codesign --verify --deep --strict` passes).

> Note: `tauri bundle --bundles dmg` also **deletes** the standalone `.app`
> after it builds the dmg ("Cleaning … spikeapp.app"). Regenerate the app
> with `tauri bundle --bundles app` first if you need it back.

### Step 3 — notarize + staple (CI-only leg — NOT confirmed locally)

`spctl` rejects the signed-but-unnotarized dmg, as expected:

```sh
spctl -a -t open --context context:primary-signature -vvv "$out"
# rejected — source=Unnotarized Developer ID — origin=Developer ID Application: <Your Name> …
```

That rejection is the *correct* pre-notarization state — signature is valid,
Gatekeeper just wants a notarization ticket. The remaining commands need
App Store Connect API creds (CI secrets) and were **not** runnable locally
(no stored notary profile; no API key / Apple-ID+team pairing in env). This
repo already ships the notary leg as a composite action,
`.github/actions/notarize-mac/action.yml` — WS4 should reuse it rather than
re-derive these commands. Its env var names are the convention to follow
(`ASC_KEY_BASE64` → decoded `--key` path, `ASC_KEY_ID` → `--key-id`,
`ASC_ISSUER_ID` → `--issuer`):

```sh
# what notarize-mac/action.yml runs, distilled:
echo "$ASC_KEY_BASE64" | base64 --decode > "$RUNNER_TEMP/AuthKey.p8"
xcrun notarytool submit "$out" \
  --key    "$RUNNER_TEMP/AuthKey.p8" \
  --key-id "$ASC_KEY_ID" \
  --issuer "$ASC_ISSUER_ID" \
  --wait
xcrun stapler staple "$out"
codesign --verify --verbose=2 "$out"
spctl -a -t open --context context:primary-signature -vvv "$out"   # expect: accepted
```

`tauri build`/`bundle` also have a `--skip-stapling` flag: notarize without
blocking on the (multi-hour, first-time) wait, staple later. Useful for a
cold-start app; not needed once the app has been notarized once.

## Recommendation for WS3/WS4

- **Make `package` its own job** (#817 item). Compile job uploads `{binary,
  tauri.conf.json, icons/, frontend dist}` (~tens of MB); package job
  downloads it and runs `tauri bundle`. No `target/` cache, no toolchain on
  the package runner.
- **Two viable signing shapes** for the reusable `sign-notarize-mac` step
  (WS4); pick per the `sign-mode` input:
  1. **Native (preferred when creds are present at bundle time):** let tauri
     sign + notarize inside the bundle job via env — it reads
     `APPLE_CERTIFICATE` / `APPLE_SIGNING_IDENTITY` plus tauri's own
     `APPLE_API_KEY` / `APPLE_API_ISSUER` / `APPLE_API_KEY_PATH` notary vars
     (the names tauri's bundler expects), and infers the identity from
     `APPLE_CERTIFICATE` (no `signingIdentity` config needed). Fewest moving
     parts.
  2. **Post-hoc (this spike):** bundle `--no-sign`, then the reusable step
     signs the `.app`, repackages the dmg via `hdiutil`, signs the dmg, and
     hands the dmg to the existing `notarize-mac` action (`ASC_*` inputs) to
     notarize + staple. Use when bundling and signing must be different
     jobs/runners, or signing is conditionally gated. Honor the caveat: build
     the resealed dmg with `hdiutil`, never `tauri bundle --bundles dmg`.
- **Gate Apple secrets on `sign-mode`** (WS1 #813): in `sign-mode: none` the
  bundle is `--no-sign` and the whole notarize chain is skipped, so a fork /
  no-secrets build still produces an (unsigned) artifact.

## Reproduce from scratch

```sh
cd $(mktemp -d)
npm create tauri-app@latest spikeapp -- --template vanilla --manager npm --yes
cd spikeapp && npm install
npx tauri build --no-bundle                 # (a) compile only
npx tauri bundle --bundles app,dmg          # (a) package pre-built binary
# (b) sign: see Step 1/2 above with your "Developer ID Application: …" identity
```
