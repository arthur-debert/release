---
name: macos-signing-notarization
description: "macOS code signing + notarization for Electron apps in GitHub Actions CI. Covers certificate setup, electron-builder config, entitlements, and Apple notarization via App Store Connect API key with submit/poll/staple."
---

# macOS Code Signing + Notarization for Electron Apps

How to sign and notarize macOS Electron binaries in a GitHub Actions release workflow. This was worked out iteratively across several real CI failures and is the known-good approach as of April 2026.

## Why this matters

Unsigned macOS apps trigger Gatekeeper warnings and can be quarantined. Notarization is Apple's server-side malware scan — without it, users get the "Apple cannot check it for malicious software" dialog. A signed + notarized + stapled `.dmg` opens cleanly.

## Architecture overview

```
electron-builder (signs the .app)
        |
        v
  .dmg is produced
        |
        v
xcrun notarytool submit --no-wait   (submit to Apple)
        |
        v
xcrun notarytool info (poll loop)    (wait for Apple's verdict)
        |
        v
xcrun stapler staple                 (attach ticket to .dmg)
```

Notarization is intentionally **not** done by electron-builder. We handle it in separate workflow steps so we get per-step visibility into Apple's queue and avoid opaque hangs.

## Secrets required

| Secret | Purpose | Source file |
|--------|---------|-------------|
| `APPLE_CERTIFICATE_P12_BASE64` | Base64-encoded Developer ID Application certificate | Your `.p12` file exported from Keychain Access or via `openssl pkcs12 -export -legacy`. Encode with `base64 -i cert.p12`. |
| `APPLE_CERTIFICATE_PASSWORD` | Password for the .p12 | The password you set during `.p12` export |
| `ASC_API_KEY_BASE64` | Base64-encoded App Store Connect API key | The `AuthKey_<KEY_ID>.p8` file downloaded from App Store Connect. Encode with `base64 -i AuthKey_XXXXXXXXXX.p8`. |
| `ASC_API_KEY_ID` | Key ID (the `XXXXXXXXXX` part of the `.p8` filename) | Shown in App Store Connect > Users and Access > Integrations > Keys |
| `ASC_API_ISSUER_ID` | Issuer ID (a UUID, same for all keys in your org) | Shown at the top of the App Store Connect Keys page |

### Where keys land at runtime

| File | Written by | Used by |
|------|-----------|---------|
| (in-memory) | electron-builder decodes `CSC_LINK` base64 internally | Code signing the `.app` bundle |
| `$RUNNER_TEMP/AuthKey.p8` | Workflow step "Write App Store Connect API key" decodes `ASC_API_KEY_BASE64` | `xcrun notarytool` submit/poll/log steps (`--key` flag) |

The `.p8` is written to `$RUNNER_TEMP` (a per-job ephemeral directory that GitHub Actions cleans up automatically). The `.p12` is never written to disk at all — electron-builder handles it from the base64 env var.

## Key decisions and pitfalls

### 1. The .p12 must use legacy PKCS12 encryption

OpenSSL 3.x defaults to AES-256-CBC for PKCS12 files. macOS Security framework cannot read this — you get a cryptic "MAC verification failed" error. When exporting the certificate:

```bash
openssl pkcs12 -export -legacy \
  -in cert.pem -inkey key.pem \
  -out cert.p12 -passout pass:YOUR_PASSWORD
```

The `-legacy` flag is critical. Without it, signing will fail on macOS runners.

### 2. Let electron-builder manage its own keychain

The first approach was to manually create a temp keychain, import the cert, and point `CSC_LINK` at the `.p12` file path. This works but is unnecessary complexity. electron-builder can decode a base64 `.p12` directly from the `CSC_LINK` env var and manages its own temporary keychain internally. Just pass the base64 string:

```yaml
env:
  CSC_LINK: ${{ secrets.APPLE_CERTIFICATE_P12_BASE64 }}
  CSC_KEY_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
```

No manual keychain import, no cleanup step.

### 3. Use App Store Connect API key auth for notarization (not Apple ID)

The initial approach used Apple ID + app-specific password (`--apple-id` / `--password`). This works but has problems in CI:
- Credentials rotate and can trigger 2FA challenges
- Apple's queue sometimes deprioritizes automated submissions from password auth

API key auth (`--key` / `--key-id` / `--issuer`) is Apple's recommended path for CI:
- No credential rotation issues
- Better queue recognition for automated submissions
- The `.p8` key file never expires (unless revoked)

```yaml
- name: Write App Store Connect API key
  shell: bash
  env:
    ASC_API_KEY_BASE64: ${{ secrets.ASC_API_KEY_BASE64 }}
  run: |
    echo "$ASC_API_KEY_BASE64" | base64 --decode > "$RUNNER_TEMP/AuthKey.p8"

- name: Submit DMG for notarization
  shell: bash
  run: |
    DMG=$(ls release/*.dmg | head -1)
    RESULT=$(xcrun notarytool submit "$DMG" \
      --key "$RUNNER_TEMP/AuthKey.p8" \
      --key-id "${{ secrets.ASC_API_KEY_ID }}" \
      --issuer "${{ secrets.ASC_API_ISSUER_ID }}" \
      --output-format json \
      --no-wait)
    ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    echo "submission-id=$ID" >> "$GITHUB_OUTPUT"
```

### 4. Split notarization into submit / poll / staple

electron-builder's built-in `notarize: true` blocks silently — you can't see where you are in Apple's queue and the job just hangs until it times out. Splitting into three explicit steps gives:

- **Submit** (`--no-wait`): captures the submission ID immediately
- **Poll**: loops `notarytool info` every 30s with per-iteration logging so you can see `[$i/60] status=In Progress` in the CI log
- **Staple**: attaches the notarization ticket to the `.dmg` so users don't need an internet connection at install time

```yaml
- name: Wait for notarization
  shell: bash
  run: |
    ID="${{ steps.notarize-submit.outputs.submission-id }}"
    for i in $(seq 1 60); do
      RESULT=$(xcrun notarytool info "$ID" \
        --key "$RUNNER_TEMP/AuthKey.p8" \
        --key-id "${{ secrets.ASC_API_KEY_ID }}" \
        --issuer "${{ secrets.ASC_API_ISSUER_ID }}" \
        --output-format json 2>&1) || true
      STATUS=$(echo "$RESULT" | python3 -c \
        "import sys,json; print(json.load(sys.stdin).get('status','Unknown'))" \
        2>/dev/null || echo "Unknown")
      echo "  [$i/60] status=$STATUS ($(date -u +%H:%M:%S))"
      if [ "$STATUS" = "Accepted" ]; then exit 0; fi
      if [ "$STATUS" = "Invalid" ] || [ "$STATUS" = "Rejected" ]; then
        xcrun notarytool log "$ID" \
          --key "$RUNNER_TEMP/AuthKey.p8" \
          --key-id "${{ secrets.ASC_API_KEY_ID }}" \
          --issuer "${{ secrets.ASC_API_ISSUER_ID }}" 2>&1 || true
        exit 1
      fi
      sleep 30
    done
    echo "::warning::Timed out after 30 min. DMG is signed but not notarized."
```

### 5. Make notarization non-blocking for release artifacts

Apple's queue can be slow. A notarization timeout should not prevent the signed `.dmg` from being uploaded or block the Linux/Windows artifacts from shipping. Key details:

- Poll timeout emits `::warning::` instead of failing the job
- Staple step uses `|| echo "::warning::..."` so a staple failure is non-fatal
- Artifact upload uses `if: always() && !cancelled()` so it runs even after timeout
- The release job uses `if: always() && !cancelled()` so other platforms still get released

The DMG is still code-signed even without notarization. Users may see a Gatekeeper prompt, but the binary works. You can staple manually later:

```bash
xcrun stapler staple path/to/App.dmg
```

### 6. Electron entitlements for hardened runtime

Electron apps need these entitlements to run under hardened runtime:

```xml
<key>com.apple.security.cs.allow-jit</key>               <!-- V8 JIT -->
<key>com.apple.security.cs.allow-unsigned-executable-memory</key>  <!-- V8 -->
<key>com.apple.security.cs.allow-dyld-environment-variables</key>  <!-- helper processes -->
```

Place in `resources/entitlements.mac.plist` and reference from electron-builder config:

```yaml
mac:
  hardenedRuntime: true
  gatekeeperAssess: false
  entitlements: resources/entitlements.mac.plist
  entitlementsInherit: resources/entitlements.mac.plist
  notarize: false  # handled in workflow steps
```

`entitlementsInherit` applies the same entitlements to helper processes (GPU, renderer). Without it, child processes crash.

### 7. electron-builder.yml: disable DMG signing

```yaml
dmg:
  sign: false
```

The `.app` inside is already signed. Signing the DMG container itself causes issues with notarization (double-signing). Let the `.app` carry the signature and the DMG carry the stapled notarization ticket.

## The evolution (what failed along the way)

1. **Manual keychain management** -- Created a temp keychain, imported the cert, added it to the search list, cleaned up after. Worked but was fragile and unnecessary since electron-builder handles this internally from a base64 `CSC_LINK`.

2. **OpenSSL 3.x PKCS12 incompatibility** -- The default AES-256-CBC encryption silently fails on macOS. Took a while to diagnose because the error ("MAC verification failed") doesn't mention encryption at all. Fix: re-export with `-legacy`.

3. **electron-builder's built-in notarization** -- Blocks the entire job with no visibility. If Apple's queue is slow (common), the job times out at 45 minutes with no useful output. Fix: disable `notarize` in config, handle externally.

4. **Apple ID auth for notarization** -- Works locally but unreliable in CI. Credential rotation and queue deprioritization. Fix: switch to ASC API key auth.

5. **Notarization failures blocking all platforms** -- A timeout on macOS was preventing Linux and Windows artifacts from being released. Fix: make notarization non-blocking with warnings.

## Checklist for a new project

1. Export your Developer ID Application cert as `.p12` with `openssl pkcs12 -export -legacy`
2. Base64-encode it: `base64 -i cert.p12 | pbcopy`
3. Create an App Store Connect API key at https://appstoreconnect.apple.com/access/integrations/api (Developer role is sufficient)
4. Add all 5 secrets to the GitHub repo
5. Create `resources/entitlements.mac.plist` with the three Electron entitlements
6. Set `hardenedRuntime: true`, `notarize: false`, and both entitlement paths in electron-builder config
7. Set `dmg: { sign: false }`
8. Add the submit/poll/staple steps to the release workflow (after packaging, before artifact upload)
9. Set artifact upload to `if: always() && !cancelled()`
10. Set the release job to `if: always() && !cancelled()`
