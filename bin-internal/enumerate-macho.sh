#!/usr/bin/env bash
# Enumerate the nested signable code inside a macOS .app bundle, inner-first,
# for post-hoc codesigning (WS4).
#
# WHY: `codesign --options runtime --timestamp App.app` signs ONLY the bundle's
# MAIN executable. Any OTHER Mach-O code in the bundle — extra executables (e.g.
# phos's Contents/MacOS/gen_fixtures), loose dylibs, and nested code bundles
# (.framework/.app/.appex/.xpc helpers, as electron's Helper.app) — is left
# unsigned/unhardened, and Apple's notary service REJECTS the archive
# ("signature of the binary is invalid / no secure timestamp / hardened runtime
# not enabled"). The inline `tauri build` path signs nested code internally; the
# post-hoc signer must replicate that. We do NOT use `codesign --deep` (Apple
# discourages it for distribution/notarization — it mis-applies entitlements);
# instead we enumerate and sign inner-first, then the outer .app last (the
# caller appends the .app).
#
# Prints the nested paths ONLY (one per line), EXCLUDING the top-level .app —
# the workflow passes them to sign-mac ahead of the .app (sign-mac signs in
# order, outer-last). Ordering: loose Mach-O files first, then nested code
# bundles deepest-first; either way every nested item precedes the outer .app.
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

# (a) Nested CODE BUNDLES (framework / helper app / appex / xpc), excluding the
#     top app. Signing a bundle codesigns its own main executable, so we sign
#     each bundle once at its root — deepest-first so an inner helper is signed
#     before an outer one that embeds it.
bundles=()
while IFS= read -r d; do
  [ "${d}" = "${APP_PATH}" ] && continue
  bundles+=("$(rel_depth "${d}")	${d}")
done < <(find "${APP_PATH}" -type d \
           \( -name '*.framework' -o -name '*.app' -o -name '*.appex' -o -name '*.xpc' \) 2>/dev/null)

# (b) Loose Mach-O FILES not inside any nested code bundle (those are covered by
#     signing the bundle in (a)). Detect Mach-O by content. A file is "loose" if
#     its path below APP_PATH contains no nested bundle suffix before it.
loose=()
while IFS= read -r f; do
  rel="${f#"${APP_PATH}"/}"
  case "${rel}" in
    *.framework/*|*.app/*|*.appex/*|*.xpc/*) continue ;;   # inside a nested bundle
  esac
  if file -b "${f}" 2>/dev/null | grep -q 'Mach-O'; then
    loose+=("${f}")
  fi
done < <(find "${APP_PATH}" -type f 2>/dev/null)

# Emit: loose files first, then nested bundles deepest-first. Every line is a
# nested item; the outer .app is appended by the caller (signed last).
# Guard each emit on a non-empty count, then expand with FULL quotes so paths
# containing spaces survive (app bundles legitimately have spaces); the count
# guard also avoids bash 3.2's unbound-error on "${empty[@]}" under `set -u`.
if [ "${#loose[@]}" -gt 0 ]; then
  printf '%s\n' "${loose[@]}"
fi

if [ "${#bundles[@]}" -gt 0 ]; then
  printf '%s\n' "${bundles[@]}" | sort -rn -k1,1 | cut -f2-
fi
