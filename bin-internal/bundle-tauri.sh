#!/usr/bin/env bash
# Bundle a pre-compiled Tauri app UNSIGNED (the package job, WS4).
#
# Runs after build-tauri.sh compiles (`tauri build --no-bundle`).
# `tauri bundle --no-sign` packages the already-built binary without
# signing — that's what keeps the bundle unsigned so the reusable
# sign-notarize-mac workflow can codesign + notarize it later. Re-running
# `tauri bundle` to re-seal a SIGNED app would strip the signature (the WS0
# spike caveat); the signer reseals with hdiutil instead, never this script.
#
# For macOS the signer reseals the .dmg from the inner .app, so this script
# also tars the produced `.app` next to the .dmg — artifact upload doesn't
# preserve a .app bundle's symlinks / exec bits, but a tarball round-trips
# cleanly. `app` is forced into the mac bundle targets so the .app exists.
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")
#   BUNDLES      bundle targets (passed to --bundles; empty = tauri.conf default)
#   PLATFORM     mac | linux | windows

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
PLATFORM="${PLATFORM:?PLATFORM required}"

cd "${TAURI_DIR}"

bundles="${BUNDLES:-}"

# macOS reseal needs the standalone .app. Ensure `app` is in the target list
# so `tauri bundle` produces it (a dmg-only bundle builds the .app then DELETES
# it, leaving no payload to reseal → post-hoc signing later fails "no
# *.unsigned-app.tar.gz found"). This must hold even when BUNDLES is empty (the
# tauri.conf default target list): an empty `--bundles` lets tauri.conf pick the
# targets, which may be dmg-only, so we make the mac target list explicit and
# always include `app`. A consumer that only asked for `dmg` still gets the
# .app we reseal from.
if [ "${PLATFORM}" = "mac" ]; then
  case ",${bundles}," in
    *,app,*) : ;;                       # already requested
    ,,)      bundles="app,dmg" ;;       # empty (tauri.conf default) → app + dmg
    *)       bundles="app,${bundles}" ;;
  esac
fi

extra=()
if [ -n "${bundles}" ]; then
  extra=(--bundles "${bundles}")
fi

npx tauri bundle --no-sign "${extra[@]+"${extra[@]}"}"

# Package the macOS .app into a tarball alongside the .dmg so it survives
# artifact upload intact for the post-hoc reseal. The suffix is
# `.unsigned-app.tar.gz` (NOT `.app.tar.gz`) so it never collides with — or
# gets mistaken for — tauri's own `<app>.app.tar.gz` updater bundle, which
# lives in the same dir.
if [ "${PLATFORM}" = "mac" ]; then
  macos_dir="src-tauri/target/release/bundle/macos"
  if [ -d "${macos_dir}" ]; then
    while IFS= read -r app; do
      name=$(basename "${app}" .app)
      tar -C "${macos_dir}" -czf "${macos_dir}/${name}.unsigned-app.tar.gz" "$(basename "${app}")"
      echo "packaged ${app} -> ${macos_dir}/${name}.unsigned-app.tar.gz"
    done < <(find "${macos_dir}" -maxdepth 1 -type d -name '*.app')
  fi
fi
