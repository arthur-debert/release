#!/usr/bin/env bash
# Integrity guard: assert the bundled app's MAIN binary is the EXPECTED one,
# and FAIL LOUD otherwise (epic #811 / #817 WS6a).
#
# Why this exists: a src-tauri crate with multiple `[[bin]]` targets and NO
# declared main (no Cargo `default-run`, no tauri.conf `mainBinaryName`) makes
# `tauri bundle` pick the alphabetically-FIRST binary as the app's main
# executable. phos shipped a `.app`/`.deb` whose CFBundleExecutable was a 2.9MB
# dev tool (`gen_fixtures`) instead of the real `phos` binary — and it signed +
# notarized + stapled cleanly (signing ≠ integrity), so it shipped silently for
# a whole epic. This guard turns that into a hard, early bundle-job failure.
#
# Expected-name resolution (first non-empty wins):
#   1. tauri.conf.json  .mainBinaryName
#   2. tauri.conf.json  .productName
#   3. the cargo package name (src-tauri/Cargo.toml  [package] name)
#
# Checks (fail-loud, mac + linux):
#   mac    — the bundled <App>.app's Contents/MacOS/<exe> AND its
#            Info.plist CFBundleExecutable equal the expected name.
#   linux  — a binary named <expected> exists among the collected payload
#            (the .deb/.AppImage carry the executable under that name).
#
# Env vars:
#   TAURI_DIR   path to Tauri project root (default: ".")
#   PLATFORM    mac | linux | windows (windows: skipped — covered elsewhere)

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
PLATFORM="${PLATFORM:?PLATFORM required}"

cd "${TAURI_DIR}"

conf="src-tauri/tauri.conf.json"
cargo_toml="src-tauri/Cargo.toml"

# --- resolve the expected main binary name --------------------------------
expected=""
if [ -f "${conf}" ]; then
  expected=$(jq -r '.mainBinaryName // .productName // empty' "${conf}")
fi
if [ -z "${expected}" ] && [ -f "${cargo_toml}" ]; then
  # [package] name = "..." — first such line under any table is the package
  # name (Cargo requires it before any [[bin]] table). Tolerant of quotes +
  # spacing; stop at the first match.
  expected=$(grep -m1 -E '^[[:space:]]*name[[:space:]]*=' "${cargo_toml}" \
               | sed -E 's/^[^=]*=[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/')
fi

if [ -z "${expected}" ]; then
  echo "::error::could not resolve the expected main binary name (no mainBinaryName/productName in ${conf}, no package name in ${cargo_toml})"
  exit 1
fi
echo "expected main binary: '${expected}'"

bundle_root="src-tauri/target/release/bundle"
[ -d "${bundle_root}" ] || { echo "::error::bundle root not found: ${bundle_root}"; exit 1; }

fail=0

case "${PLATFORM}" in
  mac)
    # Each bundled .app must carry the expected main executable, both as the
    # file under Contents/MacOS/ and as CFBundleExecutable in Info.plist.
    found_app=0
    while IFS= read -r app; do
      found_app=1
      echo "checking ${app}"

      # CFBundleExecutable — prefer PlistBuddy (mac runner), fall back to plutil.
      plist="${app}/Contents/Info.plist"
      cfexe=""
      if [ -f "${plist}" ]; then
        if [ -x /usr/libexec/PlistBuddy ]; then
          cfexe=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "${plist}" 2>/dev/null || true)
        elif command -v plutil >/dev/null 2>&1; then
          cfexe=$(plutil -extract CFBundleExecutable raw -o - "${plist}" 2>/dev/null || true)
        else
          # No mac plist tooling (e.g. a Linux test host): parse the XML plist
          # — the <string> value immediately after the CFBundleExecutable key.
          cfexe=$(grep -A1 '<key>CFBundleExecutable</key>' "${plist}" 2>/dev/null \
                    | grep -o '<string>[^<]*</string>' \
                    | sed -E 's/<\/?string>//g' | head -n1 || true)
        fi
      fi
      if [ -z "${cfexe}" ]; then
        echo "::error::could not read CFBundleExecutable from ${plist}"
        fail=1
      elif [ "${cfexe}" != "${expected}" ]; then
        echo "::error::wrong main binary in ${app}: CFBundleExecutable='${cfexe}', expected '${expected}'"
        fail=1
      fi

      # The actual executable file under Contents/MacOS/.
      if [ ! -f "${app}/Contents/MacOS/${expected}" ]; then
        present=$(find "${app}/Contents/MacOS" -maxdepth 1 -type f -exec basename {} \; 2>/dev/null | paste -sd, - || true)
        echo "::error::wrong main binary in ${app}: Contents/MacOS/${expected} missing; present: [${present}]"
        fail=1
      fi
    done < <(find "${bundle_root}/macos" -maxdepth 1 -type d -name '*.app' 2>/dev/null)

    if [ "${found_app}" -eq 0 ]; then
      echo "::error::no .app bundle found under ${bundle_root}/macos to verify"
      fail=1
    fi
    ;;

  linux)
    # The expected binary must appear among the collected Linux payload — the
    # .deb stages it at usr/bin/<expected>, the AppImage at usr/bin/<expected>.
    # tauri unpacks these under bundle/<deb|appimage>/.../data/usr/bin/.
    if find "${bundle_root}" -type f -name "${expected}" | grep -q .; then
      echo "found expected linux binary '${expected}'"
    else
      present=$(find "${bundle_root}" -type f -path '*/usr/bin/*' -exec basename {} \; 2>/dev/null | sort -u | paste -sd, - || true)
      echo "::error::expected linux binary '${expected}' not found in ${bundle_root}; usr/bin payload: [${present}]"
      fail=1
    fi
    ;;

  windows)
    echo "windows: main-binary integrity guard not applicable here; skipped"
    ;;

  *)
    echo "::error::unknown PLATFORM: ${PLATFORM}"
    exit 1
    ;;
esac

if [ "${fail}" -ne 0 ]; then
  echo "::error::bundle integrity check FAILED — the packaged app does not carry the expected main binary '${expected}'."
  exit 1
fi
echo "bundle integrity OK: main binary is '${expected}'"
