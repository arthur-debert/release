#!/usr/bin/env bash
# Enumerate the nested signable code inside a macOS .app bundle, inner-first,
# for post-hoc codesigning (WS4).
#
# WHY: `codesign --options runtime --timestamp App.app` signs ONLY that bundle's
# MAIN executable. Every OTHER piece of Mach-O code must be signed explicitly,
# inner-first, or Apple's notary service REJECTS the archive ("signature of the
# binary is invalid / no secure timestamp / hardened runtime not enabled"):
#   - extra executables beside the main one (e.g. phos's Contents/MacOS/
#     gen_fixtures) and loose dylibs;
#   - code bundles nested in the app — and, recursively, the EXTRA Mach-O inside
#     THEM. Signing a nested `Helper.app` (electron's
#     `*.app/Contents/Frameworks/*Helper*.app`) only signs that helper's main
#     executable; its own extra executables / loose dylibs are still missed, so
#     we must recurse INTO `.app`/`.appex`/`.xpc` bundles too.
# The inline `tauri build` path signs all of this internally; the post-hoc
# signer must replicate it. We do NOT use `codesign --deep` (Apple discourages
# it for distribution/notarization — it mis-applies entitlements); instead we
# enumerate and the caller signs each path with `--options runtime --timestamp`,
# strictly inner-out.
#
# Bundle handling:
#   - `.app` / `.appex` / `.xpc`  — RECURSE into: emit their inner extra Mach-O,
#     then the bundle root. Signing the bundle root (re)signs its own main
#     executable; because the root sorts SHALLOWER than its contents, it is
#     signed AFTER them — the correct inner-out order, main-exe re-sign last.
#   - `.framework`                — OPAQUE: sign the framework ROOT only, never
#     individual Mach-O inside it (the framework is the signing unit).
#
# Prints the nested paths ONLY (one per line), inner-first (deepest first),
# EXCLUDING the top-level .app — the caller appends the .app and signs it last.
# Consumer-agnostic: Mach-O is detected by content (`file`), not by name.
#
# Env vars:
#   APP_PATH   path to the unsigned .app bundle

set -euo pipefail

APP_PATH="${APP_PATH:?APP_PATH required}"
[ -d "${APP_PATH}" ] || { echo "::error::APP_PATH not a directory: ${APP_PATH}" >&2; exit 1; }
APP_PATH="${APP_PATH%/}"   # strip trailing slash so the top-app compare is exact

# Fail loud if `file` is absent. Mach-O detection runs through `file ... | grep`,
# whose pipeline evaluates FALSE when `file` is missing — which would silently
# skip ALL nested code and reproduce the very bug this script fixes (nested
# Mach-O reaching the notary unsigned), only without a diagnostic. Hard-require
# the tool instead.
command -v file >/dev/null 2>&1 || { echo "::error::file(1) is required to detect Mach-O binaries but was not found on PATH" >&2; exit 1; }

# rel-depth: count the '/' separators in the path below APP_PATH (a proxy for
# nesting depth, for the deepest-first sort).
rel_depth() {
  local rel="${1#"${APP_PATH}"/}"
  printf '%s' "${rel}" | tr -cd '/' | wc -c | tr -d ' '
}

# Collect signable paths as "<depth>\t<path>" lines, then sort deepest-first so
# every item precedes anything that encloses it (inner-out). A bundle root is
# shallower than its own contents, so it is emitted AFTER them — exactly the
# codesign ordering.
collect() {
  # (a) Nested CODE BUNDLE ROOTS (framework / app / appex / xpc), excluding the
  #     top app. Each is signed once at its root; for .app/.appex/.xpc the root
  #     sign covers only the MAIN executable — the extras come from (b).
  while IFS= read -r d; do
    [ "${d}" = "${APP_PATH}" ] && continue
    printf '%s\t%s\n' "$(rel_depth "${d}")" "${d}"
  done < <(find "${APP_PATH}" -type d \
             \( -name '*.framework' -o -name '*.app' -o -name '*.appex' -o -name '*.xpc' \) 2>/dev/null)

  # (b) Mach-O FILES, EXCLUDING anything inside a `.framework` (opaque — the
  #     framework root in (a) is the signing unit). Files inside helper
  #     `.app`/`.appex`/`.xpc` ARE included — that's the recursion: a helper's
  #     extra executables/dylibs must be signed too. The main executable of a
  #     code bundle is also matched here, but it sorts deeper than its bundle
  #     root, so the root re-signs it last (harmless, correct order).
  while IFS= read -r f; do
    rel="${f#"${APP_PATH}"/}"
    case "${rel}" in
      *.framework/*) continue ;;   # opaque framework internals — skip
    esac
    if file -b "${f}" 2>/dev/null | grep -q 'Mach-O'; then
      printf '%s\t%s\n' "$(rel_depth "${f}")" "${f}"
    fi
  done < <(find "${APP_PATH}" -type f 2>/dev/null)
}

# Deepest-first (inner-out); strip the depth key. A stable sort keeps the
# emission deterministic among equal-depth siblings.
collect | sort -s -rn -k1,1 | cut -f2-
