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
# Expected-name resolution — the EXPECTED SET is ALL the declared identities
# (empties dropped), and the bundle PASSES if its main binary matches ANY of:
#   1. tauri.conf.json  .mainBinaryName
#   2. tauri.conf.json  .productName
#   3. the cargo package name (src-tauri/Cargo.toml  [package] name)
# It FAILS only when the bundled main matches NONE of them (e.g. gen_fixtures).
# Tauri names the bundled executable after mainBinaryName when set, else after
# productName/package name; consumers also differ on casing (Phos vs phos), so
# accepting any declared identity is robust while still catching the real bug.
#
# Checks (fail-loud, mac + linux):
#   mac    — the bundled <App>.app's Info.plist CFBundleExecutable is in the
#            expected set AND a Contents/MacOS/<that-name> file exists.
#   linux  — some binary named like a member of the expected set exists among
#            the collected payload (the .deb/.AppImage carry it under that name).
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

# --- resolve the expected-name SET ----------------------------------------
# Collect every declared identity; drop empties. The bundle's main binary need
# only match ONE of these to pass.
expected_set=()
add_expected() { [ -n "$1" ] && expected_set+=("$1") || true; }

if [ -f "${conf}" ]; then
  add_expected "$(jq -r '.mainBinaryName // empty' "${conf}")"
  add_expected "$(jq -r '.productName // empty' "${conf}")"
fi
if [ -f "${cargo_toml}" ]; then
  # [package] name = "..." — first such line is the package name (Cargo
  # requires it before any [[bin]] table). Tolerant of quotes + spacing.
  add_expected "$(grep -m1 -E '^[[:space:]]*name[[:space:]]*=' "${cargo_toml}" \
                    | sed -E 's/^[^=]*=[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/')"
fi

if [ "${#expected_set[@]}" -eq 0 ]; then
  echo "::error::could not resolve any expected main binary name (no mainBinaryName/productName in ${conf}, no package name in ${cargo_toml})"
  exit 1
fi

# True if $1 is a member of the expected set.
in_expected_set() {
  local cand="$1" e
  for e in "${expected_set[@]}"; do
    [ "${cand}" = "${e}" ] && return 0
  done
  return 1
}

expected_list="${expected_set[*]}"
echo "expected main binary ∈ { ${expected_list// /, } }"

bundle_root="src-tauri/target/release/bundle"
[ -d "${bundle_root}" ] || { echo "::error::bundle root not found: ${bundle_root}"; exit 1; }

fail=0

case "${PLATFORM}" in
  mac)
    # Each bundled .app's main executable must be one of the expected set, both
    # as CFBundleExecutable in Info.plist and as a real file under MacOS/.
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
      elif ! in_expected_set "${cfexe}"; then
        echo "::error::wrong main binary in ${app}: CFBundleExecutable='${cfexe}', expected one of { ${expected_list// /, } }"
        fail=1
      # CFBundleExecutable is a valid identity — the matching executable file
      # must exist under Contents/MacOS/.
      elif [ ! -f "${app}/Contents/MacOS/${cfexe}" ]; then
        present=$(find "${app}/Contents/MacOS" -maxdepth 1 -type f -exec basename {} \; 2>/dev/null | paste -sd, - || true)
        echo "::error::main binary mismatch in ${app}: Contents/MacOS/${cfexe} missing; present: [${present}]"
        fail=1
      fi
    done < <(find "${bundle_root}/macos" -maxdepth 1 -type d -name '*.app' 2>/dev/null)

    if [ "${found_app}" -eq 0 ]; then
      echo "::error::no .app bundle found under ${bundle_root}/macos to verify"
      fail=1
    fi
    ;;

  linux)
    # Some binary named like a member of the expected set must appear among the
    # collected Linux payload — the .deb/.AppImage stage it at usr/bin/<name>.
    # tauri unpacks these under bundle/<deb|appimage>/.../data/usr/bin/.
    matched=""
    for e in "${expected_set[@]}"; do
      if find "${bundle_root}" -type f -name "${e}" | grep -q .; then
        matched="${e}"
        break
      fi
    done
    if [ -n "${matched}" ]; then
      echo "found expected linux binary '${matched}'"
    else
      present=$(find "${bundle_root}" -type f -path '*/usr/bin/*' -exec basename {} \; 2>/dev/null | sort -u | paste -sd, - || true)
      echo "::error::no expected linux binary { ${expected_list// /, } } found in ${bundle_root}; usr/bin payload: [${present}]"
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
  echo "::error::bundle integrity check FAILED — the packaged app's main binary is not one of the expected names { ${expected_list// /, } }."
  exit 1
fi
echo "bundle integrity OK: main binary ∈ { ${expected_list// /, } }"
