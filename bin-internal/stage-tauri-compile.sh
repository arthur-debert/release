#!/usr/bin/env bash
# Stage the compile-phase outputs that the (separate-runner) package job needs
# to run `tauri bundle` WITHOUT a recompile (epic #811 WS6a).
#
# `tauri bundle` packages a pre-built binary but does NOT re-run the frontend
# build (tauri.conf's beforeBuildCommand) — so the package runner must carry
# over two build OUTPUTS that a fresh source checkout lacks:
#   1. the compiled binary at  <tauri-dir>/src-tauri/target/release/<bin>
#   2. the built frontend assets at the tauri.conf `frontendDist` dir
# Everything else the bundler needs (tauri.conf.json, icons/, project source)
# is in the consumer checkout the package job does itself.
#
# This stages BOTH into a flat artifact dir, preserving each path RELATIVE TO
# THE REPO ROOT, so restore-tauri-compile.sh can copy the tree straight back
# into place. The heavy intermediate compile dirs (deps/, build/, incremental/,
# .fingerprint/) are excluded — they are not bundle inputs and dominate the
# multi-GB target/ tree the whole job split exists to avoid shipping.
#
# Env vars:
#   TAURI_DIR    path to Tauri project root, repo-root-relative (default: ".")
#   OUT_DIR      artifact staging dir (default: "$GITHUB_WORKSPACE/compile-<PLATFORM>")
#   PLATFORM     mac | linux | windows (only used to default OUT_DIR)

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"
REPO_ROOT="${REPO_ROOT:-${GITHUB_WORKSPACE:-$(pwd)}}"
OUT_DIR="${OUT_DIR:-${REPO_ROOT}/compile-${PLATFORM:?PLATFORM or OUT_DIR required}}"

cd "${REPO_ROOT}"

# `tauri-dir`-relative path to the src-tauri release output and the config.
src_tauri="${TAURI_DIR%/}/src-tauri"
release_dir="${src_tauri}/target/release"
conf="${src_tauri}/tauri.conf.json"

[ -d "${release_dir}" ] || { echo "::error::compile output not found: ${release_dir}"; exit 1; }
[ -f "${conf}" ]        || { echo "::error::tauri.conf.json not found: ${conf}"; exit 1; }

mkdir -p "${OUT_DIR}"

# 1. The compiled binary + any sibling top-level release outputs. The binary
#    sits at the TOP LEVEL of target/release/; the costly intermediates are in
#    subdirs we skip. Copy maxdepth-1 regular files (the binary, plus tauri's
#    small top-level products) — never the deps/build/incremental/.fingerprint
#    subtrees, and never the bundle/ dir (absent at compile time anyway).
stage() {
  # stage <src-relpath> — copies a repo-root-relative file/dir into OUT_DIR at
  # the same relative path (so restore is a flat copy-back).
  local rel="$1" dst
  dst="${OUT_DIR}/${rel}"
  mkdir -p "$(dirname "${dst}")"
  cp -R "${rel}" "${dst}"
}

staged=0
while IFS= read -r f; do
  stage "${f}"
  staged=$((staged + 1))
done < <(find "${release_dir}" -maxdepth 1 -type f)

[ "${staged}" -gt 0 ] || { echo "::error::no compiled binary found under ${release_dir}"; exit 1; }

# 2. The built frontendDist. Read it from tauri.conf.json; the value is relative
#    to the config dir (src-tauri/). Skip when it is a dev-server URL
#    (http(s)://…) — there is no built dir to carry in that case.
frontend_dist=$(jq -r '.build.frontendDist // .build.distDir // empty' "${conf}")
if [ -n "${frontend_dist}" ] && [[ "${frontend_dist}" != http://* ]] && [[ "${frontend_dist}" != https://* ]]; then
  # frontendDist is resolved relative to src-tauri/. CANONICALIZE both the repo
  # root and the resolved dist path (physical pwd, symlinks resolved) and stage
  # it ONLY when it is a real path INSIDE the repo. A frontendDist that escapes
  # the workspace — a `../../outside` value or a symlink pointing out — would
  # otherwise drag arbitrary external data into the uploaded artifact; that is a
  # misconfig, so fail LOUD rather than silently staging it or the checkout.
  dist_raw="${src_tauri}/${frontend_dist}"
  canon_root="$(cd "${REPO_ROOT}" && pwd -P)"

  if [ ! -e "${dist_raw}" ]; then
    echo "::error::frontendDist '${frontend_dist}' resolves to a missing path (${dist_raw}); build it before staging"
    exit 1
  fi
  # Reject a frontendDist that is itself a symlink — cp -R would dereference it
  # and could pull in data from outside the workspace.
  if [ -L "${dist_raw}" ]; then
    echo "::error::frontendDist '${frontend_dist}' is a symlink (${dist_raw}); refusing to dereference it into the artifact"
    exit 1
  fi
  # Canonical real path (resolves any symlinked parents too) for the containment
  # check. For a dir, cd into it; for a file, canonicalize its parent.
  if [ -d "${dist_raw}" ]; then
    dist_canon="$(cd "${dist_raw}" && pwd -P)"
  else
    dist_canon="$(cd "$(dirname "${dist_raw}")" && pwd -P)/$(basename "${dist_raw}")"
  fi
  # Must be the repo root itself or strictly under it.
  if [ "${dist_canon}" != "${canon_root}" ] && [ "${dist_canon#"${canon_root}"/}" = "${dist_canon}" ]; then
    echo "::error::frontendDist '${frontend_dist}' resolves outside the repo root (${dist_canon} not under ${canon_root}); refusing to stage external data"
    exit 1
  fi
  # Stage at the repo-root-relative path so restore lands it in place.
  rel="${dist_canon#"${canon_root}"/}"
  stage "${rel}"
  echo "staged frontendDist: ${rel}"
fi

echo "Staged compile outputs into ${OUT_DIR}:"
find "${OUT_DIR}" -maxdepth 4 -type f | sed 's/^/  /'
