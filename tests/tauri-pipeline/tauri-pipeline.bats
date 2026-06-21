#!/usr/bin/env bats

# Unit suite for the decomposed Tauri pipeline shell logic (#811/#815/#817/#835):
#   - build-frontend-tauri.sh  frontend half: runs beforeBuildCommand (#835)
#   - build-tauri.sh   rust-compile half (`tauri build --no-bundle`, with
#                      beforeBuildCommand disabled so the frontend isn't re-run)
#   - resolve-tauri-bundles.sh  per-format target resolution + mac app-forcing
#   - bundle-tauri.sh  single-format unsigned bundle
#   - package-mac-app.sh  mac .app → *.unsigned-app.tar.gz reseal payload (no
#                      collision with tauri's own .app.tar.gz updater bundle)
#   - reseal-mac-dmg.sh  volname derivation + hdiutil invocation (stubbed)
#   - unpack-unsigned-app.sh  one-app extraction + zero/multi errors
#   - enumerate-macho.sh  nested signable Mach-O enumeration (inner-first)
#   - stage-release-assets.sh  sign-agnostic release asset selection (WS5)
#   - tauri-app.yml    pipeline wiring invariants (no sign-mode; one
#                      decomposed path; sign is optional)
#
# Hermetic: stubs `npx` / `hdiutil` on PATH; `tar` / `find` / `file` are real.

BIN="${BATS_TEST_DIRNAME}/../../bin-internal"
WORKFLOW="${BATS_TEST_DIRNAME}/../../.github/workflows/tauri-app.yml"

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

# --- build-frontend-tauri.sh (frontend half) ------------------------------
# The frontend build (tauri's beforeBuildCommand) is its own step (#835) so its
# time is attributed separately from the rust compile. It reads
# build.beforeBuildCommand from tauri.conf.json and runs it verbatim; absent /
# empty = no-op.

write_conf() {
  # write_conf <beforeBuildCommand-json> — writes a minimal tauri.conf.json.
  mkdir -p src-tauri
  printf '%s\n' "{\"build\":{$1}}" > src-tauri/tauri.conf.json
}

@test "build-frontend runs the consumer's beforeBuildCommand" {
  write_conf '"beforeBuildCommand":"echo FE-RAN > fe.marker"'
  run bash "$BIN/build-frontend-tauri.sh"
  [ "$status" -eq 0 ]
  [ -f fe.marker ]
  grep -q FE-RAN fe.marker
}

@test "build-frontend is a no-op when beforeBuildCommand is empty" {
  write_conf '"beforeBuildCommand":""'
  run bash "$BIN/build-frontend-tauri.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'skipping frontend build'
}

@test "build-frontend runs the hook via cmd.exe on Windows (RUNNER_OS)" {
  # Tauri runs beforeBuildCommand through cmd.exe on Windows; stub it on PATH
  # and assert this script dispatches to it (not sh) when RUNNER_OS=Windows.
  write_conf '"beforeBuildCommand":"whatever build"'
  cat > stub/cmd.exe <<'EOF'
#!/usr/bin/env bash
echo "CMD-RAN $*" >> "$NPX_LOG"
EOF
  chmod +x stub/cmd.exe
  run env RUNNER_OS=Windows bash "$BIN/build-frontend-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'CMD-RAN /c whatever build' "$NPX_LOG"
}

@test "build-frontend is a no-op when beforeBuildCommand is absent" {
  write_conf '"frontendDist":"../build"'
  run bash "$BIN/build-frontend-tauri.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q 'skipping frontend build'
}

# --- build-tauri.sh (rust-compile half) -----------------------------------
# The build job only ever COMPILES — the fused compile+bundle+sign `tauri
# build` (old inline sign-mode) was removed. build-tauri.sh runs `tauri build
# --no-bundle` with beforeBuildCommand DISABLED (the frontend ran in its own
# prior step, #835) so it isn't re-run; bundling is a separate job.

@test "build-tauri runs tauri build --no-bundle (compile only)" {
  run bash "$BIN/build-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri build --no-bundle' "$NPX_LOG"
  # never bundles in the build job
  ! grep -q 'tauri bundle' "$NPX_LOG"
}

@test "build-tauri disables beforeBuildCommand so the frontend isn't re-run" {
  run bash "$BIN/build-tauri.sh"
  [ "$status" -eq 0 ]
  # the config override blanks beforeBuildCommand for the rust compile
  grep -q -- '-c {"build":{"beforeBuildCommand":""}}' "$NPX_LOG"
}

@test "build-tauri never produces a bundle (no fused inline path)" {
  # Even with BUNDLES set (a stale caller env), the build job compiles only —
  # the script ignores BUNDLES and never fuses bundling/signing in.
  run env BUNDLES=dmg bash "$BIN/build-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri build --no-bundle' "$NPX_LOG"
  ! grep -q 'tauri bundle' "$NPX_LOG"
  ! grep -q -- '--bundles' "$NPX_LOG"
}

# --- resolve-tauri-bundles.sh (per-format target resolution) --------------
# Resolves the effective format list + emits per-format booleans (#835).
# Centralizes the mac app-forcing rule.

resolve_out() {
  # resolve_out <format> — value of the named output after running resolve.
  grep -E "^$1=" "$GITHUB_OUTPUT" | tail -n1 | cut -d= -f2
}

@test "resolve linux deb,appimage,rpm from BUNDLES=all" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=linux BUNDLES=all bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out deb)" = true ]
  [ "$(resolve_out appimage)" = true ]
  [ "$(resolve_out rpm)" = true ]
  [ "$(resolve_out dmg)" = false ]
}

@test "resolve honors an explicit single linux format" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=linux BUNDLES=deb bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out deb)" = true ]
  [ "$(resolve_out appimage)" = false ]
  [ "$(resolve_out rpm)" = false ]
}

@test "resolve forces app on mac even for a dmg-only request" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=mac BUNDLES=dmg bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out app)" = true ]
  [ "$(resolve_out dmg)" = true ]
}

@test "resolve forces app on mac when BUNDLES is empty (tauri.conf default)" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  # no tauri.conf .bundle.targets → falls through to "all" → app,dmg
  mkdir -p src-tauri; echo '{"build":{}}' > src-tauri/tauri.conf.json
  run env PLATFORM=mac bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out app)" = true ]
  [ "$(resolve_out dmg)" = true ]
}

@test "resolve reads tauri.conf .bundle.targets array when BUNDLES empty" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  mkdir -p src-tauri
  echo '{"bundle":{"targets":["deb","appimage"]}}' > src-tauri/tauri.conf.json
  run env PLATFORM=linux bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out deb)" = true ]
  [ "$(resolve_out appimage)" = true ]
  [ "$(resolve_out rpm)" = false ]
}

@test "resolve windows nsis,msi from BUNDLES=all" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=windows BUNDLES=all bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out nsis)" = true ]
  [ "$(resolve_out msi)" = true ]
  [ "$(resolve_out deb)" = false ]
}

@test "resolve windows defaults to nsis,msi when BUNDLES empty (tauri.conf default)" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  mkdir -p src-tauri; echo '{"build":{}}' > src-tauri/tauri.conf.json
  run env PLATFORM=windows bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out nsis)" = true ]
  [ "$(resolve_out msi)" = true ]
}

@test "resolve honors an explicit single windows format" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=windows BUNDLES=msi bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -eq 0 ]
  [ "$(resolve_out msi)" = true ]
  [ "$(resolve_out nsis)" = false ]
}

@test "resolve FAILS LOUD on an unknown PLATFORM (no silent empty resolution)" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=solaris BUNDLES=all bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'unknown PLATFORM'
}

@test "resolve FAILS LOUD on an unknown bundle target (no silent drop)" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=linux BUNDLES=foo bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "unknown bundle target 'foo'"
}

@test "resolve rejects a target valid for another platform (dmg on linux)" {
  export GITHUB_OUTPUT="$TMP/out.txt"; : > "$GITHUB_OUTPUT"
  run env PLATFORM=linux BUNDLES=dmg bash "$BIN/resolve-tauri-bundles.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "unknown bundle target 'dmg'"
}

# --- bundle-tauri.sh (single-format unsigned bundle) ----------------------

@test "bundle-tauri bundles the requested single format unsigned" {
  run env BUNDLES=deb bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  grep -q 'tauri bundle --no-sign --bundles deb' "$NPX_LOG"
}

@test "bundle-tauri requests app,dmg verbatim for the coupled mac step" {
  run env BUNDLES=app,dmg bash "$BIN/bundle-tauri.sh"
  [ "$status" -eq 0 ]
  # no forcing in this script anymore — it bundles exactly what it's given.
  grep -q 'tauri bundle --no-sign --bundles app,dmg' "$NPX_LOG"
}

# --- package-mac-app.sh (reseal payload tarball) --------------------------

@test "package-mac-app tars the .app as *.unsigned-app.tar.gz, leaving the updater bundle" {
  # the npx stub produces MyApp.app + the MyApp.app.tar.gz updater bundle.
  env BUNDLES=app,dmg bash "$BIN/bundle-tauri.sh"
  run bash "$BIN/package-mac-app.sh"
  [ "$status" -eq 0 ]
  [ -f src-tauri/target/release/bundle/macos/MyApp.unsigned-app.tar.gz ]
  # tauri's own updater bundle is untouched (no collision).
  run cat src-tauri/target/release/bundle/macos/MyApp.app.tar.gz
  [ "$output" = "updater" ]
}

@test "package-mac-app fails loud when no macos bundle dir exists" {
  run bash "$BIN/package-mac-app.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'no macOS bundle dir'
}

@test "package-mac-app fails loud when the dir has zero .app bundles" {
  # dir exists (e.g. an empty bundle output) but no .app — the signer needs one.
  mkdir -p src-tauri/target/release/bundle/macos
  run bash "$BIN/package-mac-app.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'no .app found'
}

@test "package-mac-app fails loud when there is MORE than one .app (signer expects exactly one)" {
  d=src-tauri/target/release/bundle/macos
  # Use a name WITH A SPACE to assert the error list preserves it (not garbled
  # by whitespace-splitting).
  mkdir -p "$d/My App.app/Contents" "$d/B.app/Contents"
  echo bin > "$d/My App.app/Contents/exe"
  echo bin > "$d/B.app/Contents/exe"
  run bash "$BIN/package-mac-app.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'expected exactly one .app'
  # the space-containing name is reported intact (not split into "My"/"App.app")
  echo "$output" | grep -q 'My App.app'
  # and it must NOT have produced any payload
  [ ! -f "$d/My App.unsigned-app.tar.gz" ]
  [ ! -f "$d/B.unsigned-app.tar.gz" ]
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
# sign-agnostically: SIGNED=true ships the SIGNED mac dmg (drops the unsigned
# bundle-mac + its reseal payload); SIGNED=false ships the unsigned bundle-mac;
# both ship linux/win. `find`/`cp` are real, no stubs.

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

@test "stage-release-assets signed ships the SIGNED mac dmg + linux/win, not the unsigned mac" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=true \
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

@test "stage-release-assets unsigned ships bundle-mac + linux/win, skips -signed" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=false BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # the unsigned path ships the unsigned mac dmg (the bundle-mac one)
  [ "$(cat out/App_1.0.0.dmg)" = "unsigned-dmg" ]
  [ -e out/App_1.0.0_amd64.deb ]
  [ -e out/App_1.0.0.msi ]
}

@test "stage-release-assets signed never emits two dmgs with the same name (collision)" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # exactly one App_1.0.0.dmg in the asset dir
  [ "$(find out -name 'App_1.0.0.dmg' | wc -l | tr -d ' ')" -eq 1 ]
}

@test "stage-release-assets ships linux/win only when mac isn't built" {
  rm -rf downloads out; mkdir -p downloads/bundle-linux downloads/bundle-windows
  echo deb > downloads/bundle-linux/App.deb
  echo msi > downloads/bundle-windows/App.msi
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=false \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  [ -e out/App.deb ]
  [ -e out/App.msi ]
}

@test "stage-release-assets fails when nothing was staged" {
  rm -rf downloads out; mkdir -p downloads
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=false BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
}

@test "stage-release-assets signed HARD-FAILS when the signed mac bundle is missing" {
  # Only the unsigned bundle-mac present (sign job produced nothing) + linux —
  # must refuse rather than silently ship a mac-less release.
  rm -rf downloads out
  mkdir -p downloads/bundle-mac downloads/bundle-linux
  echo unsigned-dmg > downloads/bundle-mac/App.dmg
  echo deb          > downloads/bundle-linux/App.deb
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'bundle-mac-signed'
}

@test "stage-release-assets signed HARD-FAILS when the signed mac bundle is empty" {
  rm -rf downloads out
  mkdir -p downloads/bundle-mac-signed downloads/bundle-linux
  echo deb > downloads/bundle-linux/App.deb   # signed dir exists but is EMPTY
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
}

@test "stage-release-assets unsigned HARD-FAILS when the mac bundle is missing" {
  rm -rf downloads out
  mkdir -p downloads/bundle-linux
  echo deb > downloads/bundle-linux/App.deb
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=false BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'bundle-mac'
}

@test "stage-release-assets clears stale assets in the output dir (idempotent)" {
  stage_fixture
  mkdir -p out; echo stale > out/STALE_LEFTOVER.dmg
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=true BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -eq 0 ]
  # the stale asset from a prior run must not survive into the release
  [ ! -e out/STALE_LEFTOVER.dmg ]
  [ "$(cat out/App_1.0.0.dmg)" = "signed-dmg" ]
}

@test "stage-release-assets fails fast on a non-bool SIGNED (typo guard)" {
  # A `yes` typo must NOT fall through to the unsigned path and ship the
  # unsigned mac when the signed one was the one to ship.
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=yes BUILD_MAC=true \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'unknown SIGNED'
  # nothing was staged
  [ ! -e out/App_1.0.0.dmg ]
}

@test "stage-release-assets fails fast on a non-bool BUILD_MAC" {
  stage_fixture
  run env DOWNLOAD_DIR=downloads ASSETS_DIR=out SIGNED=false BUILD_MAC=yes \
    bash "$BIN/stage-release-assets.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'unknown BUILD_MAC'
}

# --- stage-tauri-compile.sh / restore-tauri-compile.sh (WS6a #817) ---------
# The compile job stages the pre-built binary + built frontendDist into a flat
# artifact that mirrors repo-root-relative paths; the package job restores it
# over a fresh checkout so `tauri bundle` packages with no recompile. `jq`,
# `cp`, `find` are real; no stubs.

# Build a synthetic compiled tree: a tauri.conf.json pointing frontendDist at
# `../dist`, a compiled binary + heavy intermediate dirs under target/release,
# and a built frontend dir. $1 = frontendDist value (default ../dist).
compile_fixture() {
  local fd="${1:-../dist}"
  rm -rf repo
  mkdir -p repo/src-tauri/target/release/deps \
           repo/src-tauri/target/release/build \
           repo/src-tauri/target/release/incremental \
           repo/src-tauri/icons
  printf '{"build":{"frontendDist":"%s"}}' "$fd" > repo/src-tauri/tauri.conf.json
  # compiled binary at the TOP LEVEL of target/release
  printf '#!/bin/sh\necho hi\n' > repo/src-tauri/target/release/myapp
  chmod +x repo/src-tauri/target/release/myapp
  # heavy intermediates that must NOT be staged
  echo junk > repo/src-tauri/target/release/deps/libfoo.rlib
  echo junk > repo/src-tauri/target/release/build/marker
  echo junk > repo/src-tauri/target/release/incremental/x
  # the built frontend dir (frontendDist resolves relative to src-tauri/)
  mkdir -p repo/dist
  echo '<html>' > repo/dist/index.html
}

@test "stage-tauri-compile stages the binary + frontendDist, excludes heavy dirs" {
  compile_fixture
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -eq 0 ]
  # the compiled binary is staged at its repo-root-relative path
  [ -f "$TMP/compile-mac/src-tauri/target/release/myapp" ]
  # the built frontendDist is staged
  [ -f "$TMP/compile-mac/dist/index.html" ]
  # heavy intermediates are NOT staged
  [ ! -e "$TMP/compile-mac/src-tauri/target/release/deps" ]
  [ ! -e "$TMP/compile-mac/src-tauri/target/release/build" ]
  [ ! -e "$TMP/compile-mac/src-tauri/target/release/incremental" ]
}

@test "stage-tauri-compile fails when there is no compiled binary" {
  compile_fixture
  rm -f repo/src-tauri/target/release/myapp
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
}

@test "stage-tauri-compile fails when the release dir is missing" {
  compile_fixture
  rm -rf repo/src-tauri/target
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
}

@test "stage-tauri-compile skips a dev-server frontendDist (URL) without failing" {
  compile_fixture 'http://localhost:1420'
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -eq 0 ]
  # binary still staged, no bogus URL path
  [ -f "$TMP/compile-mac/src-tauri/target/release/myapp" ]
  [ ! -e "$TMP/compile-mac/dist" ]
}

@test "stage-tauri-compile FAILS LOUD and stages nothing external when frontendDist escapes the repo" {
  # frontendDist resolving to ../../outside must NOT drag arbitrary external
  # data into the artifact — fail hard instead (Copilot regression).
  compile_fixture '../../outside'
  # the "outside" dir lives above REPO_ROOT, with secret-looking content
  mkdir -p outside; echo SECRET > outside/leak.txt
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'strictly inside the repo root'
  # nothing external leaked into the artifact
  [ ! -e "$TMP/compile-mac/outside" ]
  run sh -c "find '$TMP/compile-mac' -name leak.txt | wc -l"
  [ "$(echo "$output" | tr -d ' ')" = "0" ]
}

@test "stage-tauri-compile FAILS LOUD when frontendDist is a symlink (no deref into the artifact)" {
  compile_fixture '../dist'
  # replace the real dist dir with a symlink pointing outside the repo
  rm -rf repo/dist
  mkdir -p outside-target; echo SECRET > outside-target/leak.txt
  ln -s "$TMP/outside-target" repo/dist
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'is a symlink'
  run sh -c "find '$TMP/compile-mac' -name leak.txt | wc -l"
  [ "$(echo "$output" | tr -d ' ')" = "0" ]
}

@test "stage-tauri-compile FAILS LOUD when frontendDist resolves to a missing path" {
  compile_fixture '../dist'
  rm -rf repo/dist
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'missing path'
}

@test "stage-tauri-compile FAILS LOUD when frontendDist resolves to the repo root itself" {
  # frontendDist '..' from src-tauri/ is the repo root — must be STRICTLY
  # inside, never equal to the root (would cp -R the whole repo into the
  # artifact). Fail loud.
  compile_fixture '..'
  run env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q 'strictly inside the repo root'
  # nothing got staged outside src-tauri target/release
  [ ! -e "$TMP/compile-mac/src-tauri/tauri.conf.json" ]
}

@test "restore-tauri-compile copies the staged tree back over a fresh checkout" {
  compile_fixture
  env REPO_ROOT="$TMP/repo" TAURI_DIR=. PLATFORM=mac OUT_DIR="$TMP/compile-mac" \
    bash "$BIN/stage-tauri-compile.sh"
  # fresh checkout: source only, no build outputs
  rm -rf fresh
  mkdir -p fresh/src-tauri/icons
  cp repo/src-tauri/tauri.conf.json fresh/src-tauri/tauri.conf.json

  run env ARTIFACT_DIR="$TMP/compile-mac" REPO_ROOT="$TMP/fresh" TAURI_DIR=. \
    bash "$BIN/restore-tauri-compile.sh"
  [ "$status" -eq 0 ]
  # binary restored at the expected bundle-input path, and executable
  [ -f "$TMP/fresh/src-tauri/target/release/myapp" ]
  [ -x "$TMP/fresh/src-tauri/target/release/myapp" ]
  # frontendDist restored
  [ -f "$TMP/fresh/dist/index.html" ]
}

@test "restore-tauri-compile fails on a missing artifact dir" {
  run env ARTIFACT_DIR="$TMP/nope" REPO_ROOT="$TMP/fresh" TAURI_DIR=. \
    bash "$BIN/restore-tauri-compile.sh"
  [ "$status" -ne 0 ]
}

@test "restore-tauri-compile fails on an empty artifact dir" {
  mkdir -p "$TMP/empty-art" "$TMP/fresh"
  run env ARTIFACT_DIR="$TMP/empty-art" REPO_ROOT="$TMP/fresh" TAURI_DIR=. \
    bash "$BIN/restore-tauri-compile.sh"
  [ "$status" -ne 0 ]
}

@test "stage+restore round-trips a monorepo tauri-dir (tauri-dir != '.')" {
  # frontendDist is relative to <tauri-dir>/src-tauri; verify the staged
  # relative paths and restore land under the same tauri-dir on the other side.
  rm -rf repo
  mkdir -p repo/app/src-tauri/target/release/deps repo/app/dist
  printf '{"build":{"frontendDist":"../dist"}}' > repo/app/src-tauri/tauri.conf.json
  printf 'bin\n' > repo/app/src-tauri/target/release/myapp
  chmod +x repo/app/src-tauri/target/release/myapp
  echo junk > repo/app/src-tauri/target/release/deps/x.rlib
  echo '<html>' > repo/app/dist/index.html

  env REPO_ROOT="$TMP/repo" TAURI_DIR=app PLATFORM=linux OUT_DIR="$TMP/compile-linux" \
    bash "$BIN/stage-tauri-compile.sh"
  [ -f "$TMP/compile-linux/app/src-tauri/target/release/myapp" ]
  [ -f "$TMP/compile-linux/app/dist/index.html" ]
  [ ! -e "$TMP/compile-linux/app/src-tauri/target/release/deps" ]

  rm -rf fresh; mkdir -p fresh/app/src-tauri
  cp repo/app/src-tauri/tauri.conf.json fresh/app/src-tauri/tauri.conf.json
  run env ARTIFACT_DIR="$TMP/compile-linux" REPO_ROOT="$TMP/fresh" TAURI_DIR=app \
    bash "$BIN/restore-tauri-compile.sh"
  [ "$status" -eq 0 ]
  [ -x "$TMP/fresh/app/src-tauri/target/release/myapp" ]
  [ -f "$TMP/fresh/app/dist/index.html" ]
}

@test "stage/restore scripts do not invoke mapfile/readarray (bash 3.2 runner)" {
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/stage-tauri-compile.sh"
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/restore-tauri-compile.sh"
}

# --- assert-tauri-bundle-binary.sh (integrity guard, #817) -----------------
# Guards against `tauri bundle` packaging the wrong main binary (the phos
# gen_fixtures incident: a multi-[[bin]] crate with no declared main → the
# alphabetically-first binary becomes CFBundleExecutable, signed + shipped
# silently). The guard builds the EXPECTED SET from all declared identities
# (mainBinaryName, productName, cargo package name) and passes if the bundled
# main binary matches ANY of them — robust to the Phos/phos casing question —
# failing only when it matches none (gen_fixtures). Hermetic on a Linux host:
# the mac plist CFBundleExecutable is read via an XML-grep fallback (no
# PlistBuddy/plutil needed). `jq`/`find` real.

# Build a tauri project tree with a bundled .app whose main binary is $main.
#   $1 = the main executable name actually in the bundle (the "produced" one)
#   $2..$N = key=value config: mainBinaryName=, productName=, cargoName=
# A config key left out is omitted from tauri.conf / Cargo.toml so the
# expected-set membership (any-of) can be exercised.
guard_mac_fixture() {
  local main="$1"; shift
  local mbn="" pn="" cn=""
  local kv
  for kv in "$@"; do
    case "$kv" in
      mainBinaryName=*) mbn="${kv#*=}" ;;
      productName=*)    pn="${kv#*=}" ;;
      cargoName=*)      cn="${kv#*=}" ;;
    esac
  done
  rm -rf proj
  mkdir -p proj/src-tauri
  # tauri.conf.json with only the requested keys
  {
    printf '{'
    local first=1
    if [ -n "$mbn" ]; then printf '"mainBinaryName":"%s"' "$mbn"; first=0; fi
    if [ -n "$pn" ]; then [ "$first" -eq 0 ] && printf ','; printf '"productName":"%s"' "$pn"; first=0; fi
    printf '}'
  } > proj/src-tauri/tauri.conf.json
  if [ -n "$cn" ]; then
    printf '[package]\nname = "%s"\nversion = "0.1.0"\n' "$cn" > proj/src-tauri/Cargo.toml
  fi
  # the bundled .app — its main executable is $main (maybe the WRONG one)
  local appdir="proj/src-tauri/target/release/bundle/macos/App.app/Contents"
  mkdir -p "$appdir/MacOS"
  echo bin > "$appdir/MacOS/$main"
  cat > "$appdir/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key>
  <string>$main</string>
</dict></plist>
EOF
}

@test "assert-bundle-binary passes when mac main binary matches mainBinaryName" {
  guard_mac_fixture phos mainBinaryName=phos productName=Phos cargoName=phos-app
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "bundle integrity OK"
}

@test "assert-bundle-binary passes when mac main binary matches productName, not mainBinaryName (Phos casing)" {
  # The Phos/phos question: tauri named the bundled exe after productName 'Phos'
  # while mainBinaryName is 'phos'. Any-of-set must accept it (not false-fail).
  guard_mac_fixture Phos mainBinaryName=phos productName=Phos cargoName=phos-app
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "bundle integrity OK"
}

@test "assert-bundle-binary passes when mac main binary matches only the cargo package name" {
  # neither mainBinaryName nor productName declared; bundle named after cargo pkg
  guard_mac_fixture phos-app cargoName=phos-app
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "bundle integrity OK"
}

@test "assert-bundle-binary FAILS loud when mac main binary is in NONE of the set (gen_fixtures)" {
  # produced binary = gen_fixtures, which is none of {phos, Phos, phos-app}
  guard_mac_fixture gen_fixtures mainBinaryName=phos productName=Phos cargoName=phos-app
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "wrong main binary"
  echo "$output" | grep -q "gen_fixtures"
}

@test "assert-bundle-binary FAILS when the expected set is empty (no identities declared)" {
  # no mainBinaryName/productName, no Cargo.toml → empty set
  guard_mac_fixture whatever
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "could not resolve any expected main binary"
}

@test "assert-bundle-binary FAILS when CFBundleExecutable is in the set but the file is the wrong name" {
  # Plist says phos (in the set), but the actual executable on disk is gen_fixtures.
  guard_mac_fixture phos mainBinaryName=phos
  d="proj/src-tauri/target/release/bundle/macos/App.app/Contents/MacOS"
  mv "$d/phos" "$d/gen_fixtures"
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "Contents/MacOS/phos missing"
}

@test "assert-bundle-binary passes for a correct linux payload (matches the cargo name)" {
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/deb/App/data/usr/bin
  printf '{"productName":"Phos"}' > proj/src-tauri/tauri.conf.json
  printf '[package]\nname = "phos"\nversion = "0.1.0"\n' > proj/src-tauri/Cargo.toml
  # bundled binary named after the cargo package name, not productName
  echo bin > proj/src-tauri/target/release/bundle/deb/App/data/usr/bin/phos
  run env TAURI_DIR="$TMP/proj" PLATFORM=linux bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "found expected linux binary 'phos'"
}

@test "assert-bundle-binary FAILS for a wrong linux payload in NONE of the set (gen_fixtures)" {
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/deb/App/data/usr/bin
  printf '{"mainBinaryName":"phos"}' > proj/src-tauri/tauri.conf.json
  echo bin > proj/src-tauri/target/release/bundle/deb/App/data/usr/bin/gen_fixtures
  run env TAURI_DIR="$TMP/proj" PLATFORM=linux bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "no expected linux binary"
}

@test "assert-bundle-binary FAILS when no .app is present to verify (mac)" {
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/macos
  printf '{"mainBinaryName":"phos"}' > proj/src-tauri/tauri.conf.json
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "no .app bundle found"
}

@test "assert-bundle-binary scopes the cargo name to [package] even with [[bin]] tables around it" {
  # phos shape: [[bin]] tables (gen_fixtures, transport_bench) BEFORE and AFTER
  # [package]. The cargo identity must be the PACKAGE name 'phos', never a
  # [[bin]] name — otherwise the guard would 'expect' gen_fixtures and let the
  # real bug through. No tauri.conf identities, so cargo name is the only one.
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/macos/App.app/Contents/MacOS
  printf '{}' > proj/src-tauri/tauri.conf.json
  cat > proj/src-tauri/Cargo.toml <<'EOF'
[[bin]]
name = "gen_fixtures"
path = "src/bin/gen_fixtures.rs"

[package]
name = "phos"
version = "0.1.0"

[[bin]]
name = "transport_bench"
path = "src/bin/transport_bench.rs"
EOF
  # bundle's main binary is the package name → must PASS
  echo bin > proj/src-tauri/target/release/bundle/macos/App.app/Contents/MacOS/phos
  cat > proj/src-tauri/target/release/bundle/macos/App.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key>
  <string>phos</string>
</dict></plist>
EOF
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "bundle integrity OK"
}

@test "assert-bundle-binary does NOT treat a [[bin]] name as an expected identity (gen_fixtures still fails)" {
  # Same [[bin]] layout, but the bundle's main IS gen_fixtures (the real bug).
  # Since the cargo identity is scoped to [package] (phos), gen_fixtures is in
  # NONE of the set → fail loud.
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/macos/App.app/Contents/MacOS
  printf '{}' > proj/src-tauri/tauri.conf.json
  cat > proj/src-tauri/Cargo.toml <<'EOF'
[package]
name = "phos"
version = "0.1.0"

[[bin]]
name = "gen_fixtures"
path = "src/bin/gen_fixtures.rs"
EOF
  echo bin > proj/src-tauri/target/release/bundle/macos/App.app/Contents/MacOS/gen_fixtures
  cat > proj/src-tauri/target/release/bundle/macos/App.app/Contents/Info.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key>
  <string>gen_fixtures</string>
</dict></plist>
EOF
  run env TAURI_DIR="$TMP/proj" PLATFORM=mac bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "wrong main binary"
}

@test "assert-bundle-binary linux match is scoped to usr/bin (no same-basename false-pass)" {
  # A file named like the expected binary sitting OUTSIDE usr/bin (e.g. a
  # resource) must NOT satisfy the guard — only the real usr/bin payload does.
  rm -rf proj
  mkdir -p proj/src-tauri/target/release/bundle/deb/App/data/usr/share/phos \
           proj/src-tauri/target/release/bundle/deb/App/data/usr/bin
  printf '{"mainBinaryName":"phos"}' > proj/src-tauri/tauri.conf.json
  # decoy: a 'phos'-named file NOT under usr/bin
  echo decoy > proj/src-tauri/target/release/bundle/deb/App/data/usr/share/phos/phos
  # the actual usr/bin payload is the WRONG binary
  echo bin > proj/src-tauri/target/release/bundle/deb/App/data/usr/bin/gen_fixtures
  run env TAURI_DIR="$TMP/proj" PLATFORM=linux bash "$BIN/assert-tauri-bundle-binary.sh"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "no expected linux binary"
}

@test "assert-bundle-binary does not invoke mapfile/readarray (bash 3.2 runner)" {
  ! grep -Eq '^[[:space:]]*(mapfile|readarray)\b' "$BIN/assert-tauri-bundle-binary.sh"
}

# --- tauri-app.yml pipeline wiring (one decomposed path) -------------------
# Static assertions on the reusable workflow: the two-mode wiring is gone and
# the single decomposed path is in place — build compiles only, package always
# runs, sign is gated on `should-sign`, and the unsigned path can skip sign.
# (Grep-level checks; a full workflow eval needs a live dispatch.)

@test "tauri-app.yml has NO sign-mode input (mode removed)" {
  # no `sign-mode:` input key, and no `inputs.sign-mode` reference anywhere.
  ! grep -Eq '^[[:space:]]*sign-mode:' "$WORKFLOW"
  ! grep -q 'inputs.sign-mode' "$WORKFLOW"
}

@test "tauri-app.yml defines should-sign once as a preflight output" {
  # the single switch: a preflight job output named should-sign.
  grep -Eq '^[[:space:]]*should-sign:[[:space:]]*\$\{\{ steps.should-sign' "$WORKFLOW"
}

@test "tauri-app.yml package job has no mode gate (always runs)" {
  # The package job must not carry a job-level `if:` (it always runs). Extract
  # the package job block — from the `  package:` line up to (not including) the
  # next top-level job key (2-space indent + name + colon) — and assert it has
  # no `if:` whose indent is the job-property level (4 spaces).
  block=$(sed -n '/^  package:$/,/^  [a-z][a-z-]*:$/p' "$WORKFLOW")
  echo "$block"
  # sanity: we actually captured the package job
  echo "$block" | grep -q 'bundle-tauri.sh'
  # no job-level if: (4-space-indented `if:`) in the package job
  ! echo "$block" | grep -Eq '^    if:'
}

@test "tauri-app.yml build job splits frontend and rust into separate steps (#835)" {
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Build frontend' "$WORKFLOW"
  grep -q 'build-frontend-tauri.sh' "$WORKFLOW"
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Compile \(rust\)' "$WORKFLOW"
  # the old fused step name is gone
  ! grep -q 'Compile (no bundle)' "$WORKFLOW"
}

@test "tauri-app.yml package job has per-format bundle steps (#835)" {
  grep -q 'resolve-tauri-bundles.sh' "$WORKFLOW"
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Bundle \.deb' "$WORKFLOW"
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Bundle \.appimage' "$WORKFLOW"
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Bundle \.rpm' "$WORKFLOW"
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Bundle macOS' "$WORKFLOW"
  grep -q 'package-mac-app.sh' "$WORKFLOW"
  # windows leg still bundles (.msi + nsis), gated on its platform
  grep -Eq '^[[:space:]]*-[[:space:]]*name:[[:space:]]*Bundle Windows' "$WORKFLOW"
  # the old fused single step name is gone
  ! grep -q 'Bundle (unsigned)' "$WORKFLOW"
}

@test "tauri-app.yml package job uses the SLIM linux dep script; build uses the full one (#836)" {
  # The package job bundles a pre-built binary (no compile), so it must install
  # only runtime/packaging deps via install-tauri-linux-deps-package.sh — never
  # the full compile set. The build job compiles, so it keeps the full script.
  pkg=$(sed -n '/^  package:$/,/^  [a-z][a-z-]*:$/p' "$WORKFLOW")
  build=$(sed -n '/^  build:$/,/^  [a-z][a-z-]*:$/p' "$WORKFLOW")
  # package -> slim script only
  echo "$pkg" | grep -q 'install-tauri-linux-deps-package.sh'
  ! echo "$pkg" | grep -Eq 'install-tauri-linux-deps\.sh'
  # build -> full compile script, not the slim one
  echo "$build" | grep -q 'install-tauri-linux-deps.sh'
  ! echo "$build" | grep -q 'install-tauri-linux-deps-package.sh'
}

@test "slim package dep script omits the compile-only deps (no -dev, no mold) (#836)" {
  slim="${BIN}/install-tauri-linux-deps-package.sh"
  [ -x "$slim" ]
  # never the compile toolchain
  ! grep -Eq '\-dev\b' "$slim"
  ! grep -Eq '(^|[[:space:]])mold($|[[:space:]\\])' "$slim"
  # the packaging/runtime deps it DOES need
  grep -q 'patchelf' "$slim"
  grep -q 'libwebkit2gtk-4.1-0' "$slim"
  grep -q 'libgtk-3-0' "$slim"
  grep -q 'librsvg2-2' "$slim"
}

@test "tauri-app.yml sign job is gated on should-sign (optional signing)" {
  grep -q "needs.preflight.outputs.should-sign == 'true'" "$WORKFLOW"
}

@test "tauri-app.yml release accepts a SKIPPED sign (unsigned path still releases)" {
  # the explicit result gate lets the unsigned path (sign skipped) release.
  grep -q "needs.sign.result == 'skipped'" "$WORKFLOW"
  # and package must SUCCEED (it always runs now — not skippable)
  grep -q "needs.package.result == 'success'" "$WORKFLOW"
}

@test "tauri-app.yml release stages assets sign-agnostically via SIGNED" {
  # the release job passes the should-sign value as SIGNED to the stager.
  grep -Eq '^[[:space:]]*SIGNED:[[:space:]]*\$\{\{ needs.preflight.outputs.should-sign' "$WORKFLOW"
}
