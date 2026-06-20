#!/usr/bin/env bats

# Unit suite for the WS3+WS4 post-hoc signing shell logic (#815):
#   - build-tauri.sh   BUILD_PHASE dispatch (compile vs all)
#   - bundle-tauri.sh  unsigned bundle + mac .app packaging (no collision
#                      with tauri's own .app.tar.gz updater bundle; forces
#                      `app` into the mac targets)
#   - reseal-mac-dmg.sh  volname derivation + hdiutil invocation (stubbed)
#   - unpack-unsigned-app.sh  one-app extraction + zero/multi errors
#   - enumerate-macho.sh  nested signable Mach-O enumeration (inner-first)
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
# Real Mach-O files (copies of /bin/echo) so `file` detects them; a plist and
# a text file stand in for non-code resources that must be skipped.

@test "enumerate-macho lists nested executables, dylibs, and helper bundles, excluding the top .app" {
  app="$TMP/Phos.app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Frameworks"
  cp /bin/echo "$app/Contents/MacOS/Phos"            # main executable
  cp /bin/echo "$app/Contents/MacOS/gen_fixtures"    # the bug: extra executable
  cp /bin/echo "$app/Contents/Frameworks/libfoo.dylib"
  echo "<plist/>" > "$app/Contents/Info.plist"       # non-Mach-O, skipped
  mkdir -p "$app/Contents/Frameworks/Helper.app/Contents/MacOS"
  cp /bin/echo "$app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper"
  mkdir -p "$app/Contents/Frameworks/Bar.framework/Versions/A"
  cp /bin/echo "$app/Contents/Frameworks/Bar.framework/Versions/A/Bar"

  run env APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -eq 0 ]
  # the extra executable that the flat sign missed
  echo "$output" | grep -q 'Contents/MacOS/gen_fixtures$'
  echo "$output" | grep -q 'Contents/MacOS/Phos$'
  echo "$output" | grep -q 'Contents/Frameworks/libfoo.dylib$'
  # nested code bundles signed at their root
  echo "$output" | grep -q 'Frameworks/Helper.app$'
  echo "$output" | grep -q 'Frameworks/Bar.framework$'
  # the top .app is EXCLUDED (caller appends it last)
  ! echo "$output" | grep -qx "$app"
  # Mach-O INSIDE nested bundles is NOT listed separately (covered by the bundle)
  ! echo "$output" | grep -q 'Helper.app/Contents/MacOS/Helper$'
  ! echo "$output" | grep -q 'Bar.framework/Versions/A/Bar$'
  # the non-Mach-O resource is skipped
  ! echo "$output" | grep -q 'Info.plist$'
}

@test "enumerate-macho emits nothing for an .app with no nested code beyond the main exe" {
  app="$TMP/Plain.app"
  mkdir -p "$app/Contents/MacOS"
  cp /bin/echo "$app/Contents/MacOS/Plain"
  echo "x" > "$app/Contents/Resources/data.txt" 2>/dev/null || { mkdir -p "$app/Contents/Resources"; echo x > "$app/Contents/Resources/data.txt"; }

  run env APP_PATH="$app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -eq 0 ]
  # only the main executable; no bundles, no resources
  [ "$(echo "$output" | grep -c .)" -eq 1 ]
  echo "$output" | grep -q 'Contents/MacOS/Plain$'
}

@test "enumerate-macho fails when APP_PATH is missing" {
  run env APP_PATH="$TMP/nope.app" bash "$BIN/enumerate-macho.sh"
  [ "$status" -ne 0 ]
}

@test "enumerate-macho does not invoke mapfile/readarray (bash 3.2 runner)" {
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/enumerate-macho.sh"
}
