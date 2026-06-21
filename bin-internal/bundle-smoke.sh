#!/usr/bin/env bash
# Bundle integration SMOKE TEST: run the REAL package-job bundle path against a
# pre-built tauri binary and assert each requested format produced a valid,
# correctly-named bundle (#841).
#
# This is the gap the tests/tauri-pipeline BATS suite can't cover: it stubs
# `npx`/`hdiutil`, so it never runs a real `tauri bundle` — every real-tauri
# behavior (linuxdeploy GTK deps, per-format output, reseal) is invisible until a
# ~15-min RC. This runs the actual bundler on a restored compile artifact, so a
# bundling/dep-set regression (e.g. the slim-deps AppImage break: linuxdeploy
# needs the GTK-stack pkg-config `.pc` files) FAILS here, locally or in CI.
#
# It REUSES the package job's own scripts — it does NOT reimplement bundling:
#   resolve-tauri-bundles.sh  -> which formats to build
#   bundle-tauri.sh           -> `tauri bundle` per format
#   collect-tauri-bundles.sh  -> gather the distributables
#   assert-tauri-bundle-binary.sh -> the #817 main-binary identity guard
# then adds per-format existence + non-trivial-size + name assertions.
#
# PRECONDITION: CHECKOUT_DIR is a consumer checkout with the compile artifact
# already restored (see resolve-compile-artifact.sh) and JS deps installed (npx
# must resolve the tauri CLI). The local/CI wrappers handle that.
#
# Env vars:
#   CHECKOUT_DIR  consumer checkout (restored binary + frontendDist). REQUIRED.
#   PLATFORM      linux | mac        (default: linux)
#   FORMATS       space/comma list to build+assert (default per platform:
#                 linux="deb appimage", mac="dmg app")
#   TAURI_DIR     Tauri project root, repo-root-relative (default: ".")
#   MIN_BYTES     minimum size for a produced bundle to count as real
#                 (default: 1048576 = 1 MiB — a real app bundle is far bigger;
#                 catches a 0-byte/stub artifact)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CHECKOUT_DIR="${CHECKOUT_DIR:?CHECKOUT_DIR required}"
PLATFORM="${PLATFORM:-linux}"
TAURI_DIR="${TAURI_DIR:-.}"
MIN_BYTES="${MIN_BYTES:-1048576}"

case "${PLATFORM}" in
  linux) default_formats="deb appimage" ;;
  mac)   default_formats="dmg app" ;;
  *)     echo "::error::unsupported PLATFORM '${PLATFORM}' (smoke test covers linux | mac)"; exit 1 ;;
esac
FORMATS="${FORMATS:-${default_formats}}"
FORMATS="${FORMATS//,/ }"

command -v jq >/dev/null 2>&1 || { echo "::error::jq not found on PATH (needed to read tauri.conf for the name assertions)"; exit 1; }

[ -d "${CHECKOUT_DIR}" ] || { echo "::error::CHECKOUT_DIR not found: ${CHECKOUT_DIR}"; exit 1; }

# Portable file size in bytes (GNU stat -c vs BSD/mac stat -f).
filesize() { stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0; }

# The glob each requested format's distributable matches under the collected dir.
bundle_glob() {
  case "$1" in
    deb)      echo '*.deb' ;;
    appimage) echo '*.AppImage' ;;
    rpm)      echo '*.rpm' ;;
    dmg)      echo '*.dmg' ;;
    app)      echo '*.app.tar.gz' ;;   # the updater tarball of the .app
    *)        echo '' ;;
  esac
}

cd "${CHECKOUT_DIR}"
proj="${TAURI_DIR%/}"
[ -f "${proj}/src-tauri/tauri.conf.json" ] || {
  echo "::error::no src-tauri/tauri.conf.json under ${CHECKOUT_DIR}/${proj} — is the consumer checkout restored?"; exit 1; }
[ -d "${proj}/src-tauri/target/release" ] || {
  echo "::error::no src-tauri/target/release under ${CHECKOUT_DIR}/${proj} — restore the compile artifact first (resolve-compile-artifact.sh)"; exit 1; }

echo "=== bundle smoke: ${PLATFORM} formats=[${FORMATS}] in ${CHECKOUT_DIR}/${proj} ==="

# 1. Resolve the per-format booleans the package job uses (also validates FORMATS
#    against the platform's known set, fail-loud).
echo ">>> resolve-tauri-bundles.sh"
# env -u GITHUB_OUTPUT: we only want resolve's validation side effect here, not
# its per-format booleans written into the job's real $GITHUB_OUTPUT.
env -u GITHUB_OUTPUT BUNDLES="${FORMATS}" TAURI_DIR="${proj}" PLATFORM="${PLATFORM}" \
  bash "${HERE}/resolve-tauri-bundles.sh" >/dev/null

# 2. Bundle each requested format with the SAME script the package job calls.
for fmt in ${FORMATS}; do
  echo ">>> bundle-tauri.sh ${fmt}"
  BUNDLES="${fmt}" TAURI_DIR="${proj}" bash "${HERE}/bundle-tauri.sh"
done

# 3. Collect the distributables (the package job's collector). It emits `dir=…`
#    to $GITHUB_OUTPUT when that's set (it IS, under CI) and ONLY falls back to
#    stdout when it's unset — so clear it for this sub-call (`env -u`) to read the
#    dir off stdout regardless of environment. (Without this the capture is empty
#    under CI but works locally — the exact local-vs-CI gap this harness exists
#    to close.)
echo ">>> collect-tauri-bundles.sh"
collected=$(env -u GITHUB_OUTPUT TAURI_DIR="${proj}" PLATFORM="${PLATFORM}" \
  bash "${HERE}/collect-tauri-bundles.sh" | sed -n 's/^dir=//p' | tail -n1)
[ -n "${collected}" ] && [ -d "${collected}" ] || {
  echo "::error::collect-tauri-bundles.sh produced no output dir"; exit 1; }

# 4. The #817 main-binary identity guard — bundled main == the expected app, not
#    a stray dev tool (gen_fixtures).
echo ">>> assert-tauri-bundle-binary.sh"
TAURI_DIR="${proj}" PLATFORM="${PLATFORM}" bash "${HERE}/assert-tauri-bundle-binary.sh"

# Expected distributable-name token(s): tauri names bundle files after the app's
# productName / mainBinaryName (e.g. Phos_0.0.1_amd64.deb). Collect the declared
# identities (productName, mainBinaryName, cargo package name) so the per-format
# name check accepts any of them (casing/identity differs across consumers) —
# same identity set as assert-tauri-bundle-binary.sh, applied to the FILENAME.
conf="${proj}/src-tauri/tauri.conf.json"
cargo_toml="${proj}/src-tauri/Cargo.toml"
name_tokens=()
[ -f "${conf}" ] && {
  v=$(jq -r '.productName // empty' "${conf}");    [ -n "${v}" ] && name_tokens+=("${v}")
  v=$(jq -r '.mainBinaryName // empty' "${conf}"); [ -n "${v}" ] && name_tokens+=("${v}")
}
[ -f "${cargo_toml}" ] && {
  v=$(awk 'BEGIN{q="[\042\047]"} /^[[:space:]]*\[/{h=$0;sub(/#.*/,"",h);gsub(/[[:space:]]/,"",h);p=(h=="[package]");next} p&&!d&&$0~("^[[:space:]]*name[[:space:]]*=[[:space:]]*"q){l=$0;sub(/^[^=]*=[[:space:]]*/,"",l);sub(/[[:space:]]*#.*$/,"",l);gsub(q,"",l);sub(/[[:space:]]*$/,"",l);print l;d=1}' "${cargo_toml}")
  [ -n "${v}" ] && name_tokens+=("${v}")
}

# True if basename $1 contains any expected name token (empty set → skip, the
# #817 binary guard already covers identity).
name_ok() {
  local t base
  base="$(basename "$1")"
  [ "${#name_tokens[@]}" -eq 0 ] && return 0
  for t in "${name_tokens[@]}"; do
    case "${base}" in *"${t}"*) return 0 ;; esac
  done
  return 1
}

# 5. Per-format: a non-trivial, correctly-NAMED bundle of the right kind exists
#    in the collected dir. THIS is what would have failed the slim-deps PR —
#    appimage absent.
fail=0
for fmt in ${FORMATS}; do
  glob="$(bundle_glob "${fmt}")"
  if [ -z "${glob}" ]; then
    echo "::error::no distributable glob known for format '${fmt}'"; fail=1; continue
  fi
  # shellcheck disable=SC2231  # intentional glob expansion
  match=""
  for f in "${collected}"/${glob}; do
    [ -e "${f}" ] || continue
    sz=$(filesize "${f}")
    if [ "${sz}" -lt "${MIN_BYTES}" ]; then
      echo "::warning::${fmt}: ${f} is only ${sz} bytes (< ${MIN_BYTES}) — too small to be a real bundle"
      continue
    fi
    if ! name_ok "${f}"; then
      echo "::warning::${fmt}: $(basename "${f}") does not contain any expected app name { ${name_tokens[*]} }"
      continue
    fi
    match="${f}"; break
  done
  if [ -n "${match}" ]; then
    echo "OK ${fmt}: $(basename "${match}") ($(filesize "${match}") bytes)"
  else
    echo "::error::${fmt}: no '${glob}' bundle >= ${MIN_BYTES} bytes named like { ${name_tokens[*]} } in ${collected}"
    fail=1
  fi
done

if [ "${fail}" -ne 0 ]; then
  echo "::error::bundle smoke FAILED for ${PLATFORM} — see per-format errors above."
  echo "collected dir contents:"; ls -la "${collected}" || true
  exit 1
fi
echo "=== bundle smoke PASSED: ${PLATFORM} [${FORMATS}] all produced valid bundles ==="
