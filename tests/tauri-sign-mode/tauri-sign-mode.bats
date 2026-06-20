#!/usr/bin/env bats

# Unit suite for the WS3+WS4 post-hoc signing shell logic (#815):
#   - build-tauri.sh   BUILD_PHASE dispatch (compile vs all)
#   - bundle-tauri.sh  unsigned bundle + mac .app packaging (no collision
#                      with tauri's own .app.tar.gz updater bundle; forces
#                      `app` into the mac targets)
#   - reseal-mac-dmg.sh  volname derivation + hdiutil invocation (stubbed)
#   - unpack-unsigned-app.sh  one-app extraction + zero/multi errors
#   - enumerate-macho.sh  nested signable Mach-O enumeration (inner-first)
#   - stage-release-assets.sh  sign-mode-agnostic release asset selection (WS5)
#
# Hermetic: stubs `npx` / `hdiutil` on PATH; `tar` / `find` / `file` are real.

BIN="${BATS_TEST_DIRNAME}/../../bin-internal"

setup() {
  TMP="$(mktemp -d)"
  cd "$TMP"
  mkdir -p stub
  # npx stub: record argv so tests can assert which tauri subcommand ran.
  cat > stub/npx <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$NPX_LOG"
# Emulate `tauri bundle` producing a .app + .dmg so packaging logic runs.
if [ "$1" = "tauri" ] && [ "$2" = "bundle" ]; then
  d="src-tauri/target/release/bundle/macos"
  mkdir -p "$d" "src-tauri/target/release/bundle/dmg"
  mkdir -p "$d/MyApp.app/Contents"
  echo "binary" > "$d/MyApp.app/Contents/exe"
  # tauri's own updater bundle lives here too — must NOT be clobbered.
  echo "updater" > "$d/MyApp.app.tar.gz"
  echo "dmg" > "src-tauri/target/release/bundle/dmg/MyApp_1.0.0.dmg"
fi
EOF
  chmod +x stub/npx
  export NPX_LOG="$TMP/npx.log"
  : > "$NPX_LOG"
  export PATH="$TMP/stub:$PATH"
}

teardown() { rm -rf "$TMP"; }

# Write a synthetic Mach-O file at $1. Uses the 64-bit little-endian magic
# (cf fa ed fe) + a minimal header so `file` reports "Mach-O" on ANY host —
# the test runs on ubuntu CI where a real /bin/echo is ELF, not Mach-O.
make_macho() {
  mkdir -p "$(dirname "$1")"
  printf '\xcf\xfa\xed\xfe\x07\x00\x00\x01\x03\x00\x00\x00\x02\x00\x00\x00' > "$1"
}

# --- build-tauri.sh -------------------------------------------------------

@test "build-tauri compile phase runs tauri build --no-bundle" {
  run env BUILD_PHASE=compile bash "$BIN/build-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri build --no-bundle' "$NPX_LOG"
  ! grep -q 'tauri bundle' "$NPX_LOG"
}

@test "build-tauri default (all) runs full tauri build" {
  run env BUNDLES=dmg bash "$BIN/build-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri build --bundles dmg' "$NPX_LOG"
}

@test "build-tauri rejects an unknown phase" {
  run env BUILD_PHASE=bogus bash "$BIN/build-tauri.sh"
  [ "$status" -ne 0 ]
}

# --- bundle-tauri.sh ------------------------------------------------------

@test "bundle-tauri bundles unsigned and packages the .app for mac" {
  run env PLATFORM=mac BUNDLES=dmg bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  # --no-sign keeps the bundle unsigned; `app` forced in so the .app exists.
  grep -q 'tauri bundle --no-sign --bundles app,dmg' "$NPX_LOG"
  # the .app is tarred as *.unsigned-app.tar.gz ...
  [ -f src-tauri/target/release/bundle/macos/MyApp.unsigned-app.tar.gz ]
  # ... and tauri's own updater bundle is left untouched (no collision).
  run cat src-tauri/target/release/bundle/macos/MyApp.app.tar.gz
  [ "$output" = "updater" ]
}

@test "bundle-tauri does not double-add app when already requested" {
  run env PLATFORM=mac BUNDLES=app,dmg bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri bundle --no-sign --bundles app,dmg' "$NPX_LOG"
  ! grep -q 'app,app' "$NPX_LOG"
}

@test "bundle-tauri forces app even when BUNDLES is empty (tauri.conf default)" {
  # Empty BUNDLES → tauri.conf picks targets (maybe dmg-only, which deletes the
  # .app). The mac path must still request `app` explicitly so the reseal
  # payload exists.
  run env PLATFORM=mac bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri bundle --no-sign --bundles app,dmg' "$NPX_LOG"
  [ -f src-tauri/target/release/bundle/macos/MyApp.unsigned-app.tar.gz ]
}

@test "bundle-tauri does not package .app on linux" {
  # linux: no macos dir, no packaging, no forced `app` target.
  run env PLATFORM=linux BUNDLES=deb bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri bundle --no-sign --bundles deb' "$NPX_LOG"
  ! grep -q 'app,deb' "$NPX_LOG"
}

# --- reseal-mac-dmg.sh ----------------------------------------------------

@test "reseal-mac-dmg derives volname and calls hdiutil from the signed app" {
  cat > stub/hdiutil <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$HDIUTIL_LOG"
# emulate dmg creation at the -ov ... <last arg> path
out="${@: -1}"
echo "dmg" > "$out"
EOF
  chmod +x stub/hdiutil
  export HDIUTIL_LOG="$TMP/hdiutil.log"
  : > "$HDIUTIL_LOG"
  mkdir -p "MyApp.app/Contents"

  run env APP_PATH="$TMP/MyApp.app" DMG_OUT="$TMP/out.dmg" bash "$BIN/reseal-mac-dmg.sh"
  [ "$status" -eq 0 ]
  [ -f "$TMP/out.dmg" ]
  # volname defaults to the .app basename without .app
  grep -q -- '-volname MyApp' "$HDIUTIL_LOG"
  grep -q -- '-format UDZO' "$HDIUTIL_LOG"
}

@test "reseal-mac-dmg fails when the app path is missing" {
  run env APP_PATH="$TMP/nope.app" DMG_OUT="$TMP/out.dmg" bash "$BIN/reseal-mac-dmg.sh"
  [ "$status" -ne 0 ]
}

# --- unpack-unsigned-app.sh -----------------------------------------------

@test "unpack-unsigned-app extracts the single .app and reports its path" {
  mkdir -p art/MyApp.app/Contents
  echo x > art/MyApp.app/Contents/exe
  tar -C art -czf art/MyApp.unsigned-app.tar.gz MyApp.app
  rm -rf art/MyApp.app
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"

  run env ARTIFACT_DIR="$TMP/art" bash "$BIN/unpack-unsigned-app.sh"
  [ "$status" -eq 0 ]
  grep -q '^app=.*/MyApp.app$' "$GITHUB_OUTPUT"
  app=$(sed -n 's/^app=//p' "$GITHUB_OUTPUT")
  [ -d "$app" ]
}

@test "unpack-unsigned-app fails on zero payloads" {
  mkdir -p art
  run env ARTIFACT_DIR="$TMP/art" bash "$BIN/unpack-unsigned-app.sh"
  [ "$status" -ne 0 ]
}

@test "unpack-unsigned-app fails on multiple payloads" {
  mkdir -p art/a.app art/b.app
  tar -C art -czf art/a.unsigned-app.tar.gz a.app
  tar -C art -czf art/b.unsigned-app.tar.gz b.app
  run env ARTIFACT_DIR="$TMP/art" bash "$BIN/unpack-unsigned-app.sh"
  [ "$status" -ne 0 ]
}

@test "unpack-unsigned-app fails when one tarball holds multiple .app bundles" {
  mkdir -p art/A.app art/B.app
  # a single tarball carrying two .app dirs — must not silently sign one.
  tar -C art -czf art/combined.unsigned-app.tar.gz A.app B.app
  rm -rf art/A.app art/B.app
  run env ARTIFACT_DIR="$TMP/art" bash "$BIN/unpack-unsigned-app.sh"
  [ "$status" -ne 0 ]
}

@test "unpack-unsigned-app works under bash 3.2 (no mapfile/readarray call)" {
  # Guard the macOS-runner bash version: the script must not INVOKE mapfile /
  # readarray (bash 4+). Match a command invocation (start of a line, after
  # optional indent), not a mention in a comment.
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/unpack-unsigned-app.sh"
}

# --- enumerate-macho.sh ---------------------------------------------------
# Synthetic Mach-O files (make_macho — valid magic so `file` detects them on
# any host); a plist + a text file stand in for non-code resources to skip.
# Covers: top-level extras, RECURSION into helper .app/.appex/.xpc (their extra
# Mach-O), .framework treated as opaque (root only), inner-out ordering.

@test "enumerate-macho lists top extras, recurses into helper bundles, treats frameworks as opaque, excludes the top .app" {
  app="$TMP/Phos.app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Frameworks"
  make_macho "$app/Contents/MacOS/Phos"            # top main executable
  make_macho "$app/Contents/MacOS/gen_fixtures"    # top extra executable (phos bug)
  make_macho "$app/Contents/Frameworks/libfoo.dylib"
  echo "<plist/>" > "$app/Contents/Info.plist"     # non-Mach-O, skipped
  # helper .app WITH an extra executable + loose dylib inside it (electron shape)
  make_macho "$app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper"        # helper main
  make_macho "$app/Contents/Frameworks/Helper.app/Contents/MacOS/helper_tool"   # helper EXTRA
  make_macho "$app/Contents/Frameworks/Helper.app/Contents/Frameworks/libhelp.dylib"
  # framework with internal Mach-O — opaque: root only
  make_macho "$app/Contents/Frameworks/Bar.framework/Versions/A/Bar"
  make_macho "$app/Contents/Frameworks/Bar.framework/Versions/A/Libraries/libbar.dylib"

  run env APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -eq 0 ]
  # top-level extras
  echo "$output" | grep -q 'Contents/MacOS/gen_fixtures$'
  echo "$output" | grep -q 'Contents/MacOS/Phos$'
  echo "$output" | grep -q 'Contents/Frameworks/libfoo.dylib$'
  # nested code bundle ROOTS are signed
  echo "$output" | grep -q 'Frameworks/Helper.app$'
  echo "$output" | grep -q 'Frameworks/Bar.framework$'
  # RECURSION (the fix): the helper's EXTRA Mach-O is enumerated too
  echo "$output" | grep -q 'Helper.app/Contents/MacOS/helper_tool$'
  echo "$output" | grep -q 'Helper.app/Contents/MacOS/Helper$'
  echo "$output" | grep -q 'Helper.app/Contents/Frameworks/libhelp.dylib$'
  # FRAMEWORK stays opaque: its internals are NOT listed (only the root)
  ! echo "$output" | grep -q 'Bar.framework/Versions/A/Bar$'
  ! echo "$output" | grep -q 'Bar.framework/Versions/A/Libraries/libbar.dylib$'
  # inner-out ordering: the helper's inner code precedes the Helper.app root
  inner_line=$(echo "$output" | grep -n 'Helper.app/Contents/MacOS/helper_tool$' | cut -d: -f1)
  root_line=$(echo "$output" | grep -n 'Frameworks/Helper.app$' | cut -d: -f1)
  [ "$inner_line" -lt "$root_line" ]
  # the top .app is EXCLUDED (caller appends it last)
  ! echo "$output" | grep -qx "$app"
  # the non-Mach-O resource is skipped
  ! echo "$output" | grep -q 'Info.plist$'
}

@test "enumerate-macho emits only the main executable for an .app with no nested code" {
  app="$TMP/Plain.app"
  make_macho "$app/Contents/MacOS/Plain"
  mkdir -p "$app/Contents/Resources"; echo x > "$app/Contents/Resources/data.txt"

  run env APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -eq 0 ]
  # only the main executable; no bundles, no resources
  [ "$(echo "$output" | grep -c .)" -eq 1 ]
  echo "$output" | grep -q 'Contents/MacOS/Plain$'
}

@test "enumerate-macho preserves paths containing spaces" {
  # App bundles legitimately have spaces; an unquoted array expansion would
  # split "gen fixtures" into two lines / two bogus paths.
  app="$TMP/Spaced Dir/My App.app"
  make_macho "$app/Contents/MacOS/My App"
  make_macho "$app/Contents/MacOS/gen fixtures"
  make_macho "$app/Contents/Frameworks/Some Helper.app/Contents/MacOS/Some Helper"

  run env APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -eq 0 ]
  # each emitted line is an existing path (no split-induced bogus entries)
  while IFS= read -r line; do
    [ -e "$line" ] || { echo "non-existent (split?) path: $line"; false; }
  done <<< "$output"
  echo "$output" | grep -qF 'Contents/MacOS/gen fixtures'
  echo "$output" | grep -qF 'Frameworks/Some Helper.app'
  # the nested helper's inner binary IS listed (recursion), spaces intact
  echo "$output" | grep -qF 'Some Helper.app/Contents/MacOS/Some Helper'
}

@test "enumerate-macho fails when APP_PATH is missing" {
  run env APP_PATH="$TMP/nope.app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -ne 0 ]
}

@test "enumerate-macho fails loudly when file(1) is absent (not silently empty)" {
  app="$TMP/Phos.app"
  make_macho "$app/Contents/MacOS/gen_fixtures"
  # PATH with the tools the script needs, but NOT `file` — symlink each.
  bindir="$TMP/nofilebin"; mkdir -p "$bindir"
  for t in bash find tr wc sort cut grep basename dirname env printf; do
    p="$(command -v "$t" 2>/dev/null)" && ln -sf "$p" "$bindir/$t"
  done
  run env -i PATH="$bindir" APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'file(1) is required'
}

@test "enumerate-macho does not invoke mapfile/readarray (bash 3.2 runner)" {
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/enumerate-macho.sh"
}

# --- sign-mac skip guard (passwordless .p12) ------------------------------
# sign-mac is a composite action; its keychain/codesign path is mac-only and
# can't run hermetically here. These tests pin the SKIP-DECISION contract: the
# guard must key on the CERT only, never the password — a passwordless .p12
# (empty password) is valid and must still sign (the phos post-hoc blocker).

SIGN_MAC="${BATS_TEST_DIRNAME}/../../.github/actions/sign-mac/action.yml"

# Extract the action's ACTUAL skip-guard line (the `if [ -z ... ]; then` just
# above the skip warning) and run it against a given cert/password pair, so the
# behavioral tests exercise the real predicate rather than a copy of it. The
# warning text is unique in the file, but be robust to more than one match:
# take the FIRST, and parse the line number with parameter expansion (not
# `cut`, which on a multi-line grep would feed several numbers to the
# arithmetic below).
sign_mac_guard_line() {
  local hit line
  hit=$(grep -n 'No signing certificate configured' "$SIGN_MAC" | head -n 1)
  line=${hit%%:*}                              # leading line number only
  sed -n "$((line - 1))p" "$SIGN_MAC"          # the `if [ -z ... ]; then`
}

# Decide skip/sign by evaluating the EXTRACTED guard with the given env. We
# eval the real condition between `if` and `; then` — a copy here would defeat
# the point (the regression test below separately pins what the guard names).
sign_mac_decides() {
  local guard cond
  guard=$(sign_mac_guard_line)
  cond=${guard#*if }; cond=${cond%%; then*}    # strip `if ` ... `; then`
  CERT_P12_BASE64="$1" CERT_PASSWORD="$2" \
    bash -c "if $cond; then echo skip; else echo sign; fi"
}

@test "sign-mac signs when cert present + password EMPTY (passwordless .p12)" {
  [ "$(sign_mac_decides "BASE64CERT" "")" = "sign" ]
}

@test "sign-mac signs when cert present + password set" {
  [ "$(sign_mac_decides "BASE64CERT" "hunter2")" = "sign" ]
}

@test "sign-mac skips when cert is empty" {
  [ "$(sign_mac_decides "" "")" = "skip" ]
  [ "$(sign_mac_decides "" "hunter2")" = "skip" ]
}

@test "sign-mac skip guard names the cert but NOT the password (regression)" {
  # Re-introducing `|| [ -z "$CERT_PASSWORD" ]` would silently skip codesign
  # for passwordless certs again — assert the guard references the cert var and
  # does not reference the password var.
  cond=$(sign_mac_guard_line)
  echo "guard condition: $cond"
  echo "$cond" | grep -q 'CERT_P12_BASE64'
  ! echo "$cond" | grep -q 'CERT_PASSWORD'
}

# --- sign-mac keychain uniqueness + cleanup (repeated invocation) ----------
# The post-hoc signer calls sign-mac twice in one job (Sign .app, Sign .dmg).
# A fixed keychain name made the 2nd `security create-keychain` collide
# (exit 48). These pin the format/decision contract (mac-only keychain ops
# can't run hermetically).

@test "sign-mac keychain name is parameterized, not a fixed literal (collision regression)" {
  # The KEYCHAIN assignment must include a per-call unique token, never a bare
  # constant path. Re-introducing a fixed `signing.keychain-db` would collide
  # on the 2nd call in a job.
  assign=$(grep -E '^[[:space:]]*KEYCHAIN=' "$SIGN_MAC")
  echo "KEYCHAIN assignment: $assign"
  [ -n "$assign" ]
  # references a uniqueness source ($$, $RANDOM, openssl rand, or mktemp)
  echo "$assign" | grep -Eq '\$\$|\$RANDOM|openssl rand|mktemp|\$\{UNIQ\}|\$UNIQ'
  # must NOT be the old fixed name
  ! echo "$assign" | grep -q 'signing\.keychain-db"$'
}

@test "sign-mac unique token is computed once and reused for keychain + cert" {
  # Both the keychain and the decoded cert use the same per-call token so a
  # repeated invocation never clobbers either.
  grep -Eq '^[[:space:]]*UNIQ=' "$SIGN_MAC"
  grep -Eq '^[[:space:]]*KEYCHAIN=.*\$\{?UNIQ' "$SIGN_MAC"
  grep -Eq '^[[:space:]]*CERT_P12=.*\$\{?UNIQ' "$SIGN_MAC"
}

@test "sign-mac cleans up its keychain on exit (trap + delete-keychain)" {
  grep -q 'trap cleanup EXIT' "$SIGN_MAC"
  grep -q 'security delete-keychain' "$SIGN_MAC"
}

@test "sign-mac no longer writes the cert to a fixed cert.p12 path" {
  # The decoded cert path must be per-call unique, not the old fixed
  # $RUNNER_TEMP/cert.p12 that a concurrent/repeated call could clobber.
  ! grep -q 'RUNNER_TEMP/cert\.p12"' "$SIGN_MAC"
}

# --- stage-release-assets.sh (WS5 #816) -----------------------------------
# Flattens per-platform bundle subdirs into the release asset dir,
# sign-mode-agnostically: post-hoc ships the SIGNED mac dmg (drops the unsigned
# bundle-mac + its reseal payload); inline ships bundle-mac; both ship
# linux/win. `find`/`cp` are real, no stubs.

# Build a downloads/ tree with one subdir per artifact. Also clears out/ so a
# prior test's staged assets can't leak into this one (test independence).
stage_fixture() {
  rm -rf downloads out
  mkdir -p downloads/bundle-mac downloads/bundle-mac-signed \
           downloads/bundle-linux downloads/bundle-windows
  echo unsigned-dmg   > downloads/bundle-mac/App_1.0.0.dmg
  echo updater        > downloads/bundle-mac/App.app.tar.gz
  echo reseal-payload > downloads/bundle-mac/App.unsigned-app.tar.gz
  echo signed-dmg     > downloads/bundle-mac-signed/App_1.0.0.dmg
  echo deb            > downloads/bundle-linux/App_1.0.0_amd64.deb
  echo msi            > downloads/bundle-windows/App_1.0.0.msi
}

@test "stage-release-assets post-hoc ships the SIGNED mac dmg + linux/win, not the unsigned mac" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # the shipped dmg is the SIGNED one
  [ "$(cat out/App_1.0.0.dmg)" = "signed-dmg" ]
  # the unsigned reseal payload + updater bundle are NOT shipped
  [ ! -e out/App.unsigned-app.tar.gz ]
  [ ! -e out/App.app.tar.gz ]
  # linux + windows ship
  [ -e out/App_1.0.0_amd64.deb ]
  [ -e out/App_1.0.0.msi ]
}

@test "stage-release-assets inline ships bundle-mac (inline-signed) + linux/win, skips -signed" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=inline BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # inline ships the in-build-signed mac dmg (the bundle-mac one)
  [ "$(cat out/App_1.0.0.dmg)" = "unsigned-dmg" ]
  [ -e out/App_1.0.0_amd64.deb ]
  [ -e out/App_1.0.0.msi ]
}

@test "stage-release-assets post-hoc never emits two dmgs with the same name (collision)" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # exactly one App_1.0.0.dmg in the asset dir
  [ "$(find out -name 'App_1.0.0.dmg' | wc -l | tr -d ' ')" -eq 1 ]
}

@test "stage-release-assets ships linux/win only when mac isn't built" {
  rm -rf downloads out; mkdir -p downloads/bundle-linux downloads/bundle-windows
  echo deb > downloads/bundle-linux/App.deb
  echo msi > downloads/bundle-windows/App.msi
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=false \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  [ -e out/App.deb ]
  [ -e out/App.msi ]
}

@test "stage-release-assets fails when nothing was staged" {
  rm -rf downloads out; mkdir -p downloads
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=inline BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
}

@test "stage-release-assets post-hoc HARD-FAILS when the signed mac bundle is missing" {
  # Only the unsigned bundle-mac present (sign job produced nothing) + linux —
  # must refuse rather than silently ship a mac-less release.
  rm -rf downloads out
  mkdir -p downloads/bundle-mac downloads/bundle-linux
  echo unsigned-dmg > downloads/bundle-mac/App.dmg
  echo deb          > downloads/bundle-linux/App.deb
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'bundle-mac-signed'
}

@test "stage-release-assets post-hoc HARD-FAILS when the signed mac bundle is empty" {
  rm -rf downloads out
  mkdir -p downloads/bundle-mac-signed downloads/bundle-linux
  echo deb > downloads/bundle-linux/App.deb   # signed dir exists but is EMPTY
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
}

@test "stage-release-assets inline HARD-FAILS when the mac bundle is missing" {
  rm -rf downloads out
  mkdir -p downloads/bundle-linux
  echo deb > downloads/bundle-linux/App.deb
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=inline BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'bundle-mac'
}

@test "stage-release-assets clears stale assets in the output dir (idempotent)" {
  stage_fixture
  mkdir -p out; echo stale > out/STALE_LEFTOVER.dmg
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGN_MODE=post-hoc BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # the stale asset from a prior run must not survive into the release
  [ ! -e out/STALE_LEFTOVER.dmg ]
  [ "$(cat out/App_1.0.0.dmg)" = "signed-dmg" ]
}
