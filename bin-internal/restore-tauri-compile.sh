#!/usr/bin/env bash
# Restore the compile-phase outputs (staged by stage-tauri-compile.sh and
# downloaded as the `compile-<platform>` artifact) into the package job's
# fresh consumer checkout, so `tauri bundle` finds the pre-built binary +
# frontendDist and packages WITHOUT a recompile (epic #811 WS6a).
#
# The artifact mirrors each output at its REPO-ROOT-RELATIVE path, so restore
# is a straight copy of the artifact tree over the checkout. Executable bits on
# the compiled binary must survive (artifact upload preserves mode for plain
# files; we re-assert +x on the target/release top-level files defensively so a
# bundler that re-signs/execs the binary isn't blocked).
#
# Env vars:
#   ARTIFACT_DIR  downloaded compile artifact dir (default: "compile")
#   REPO_ROOT     consumer checkout root (default: $GITHUB_WORKSPACE or cwd)
#   TAURI_DIR     Tauri project root, repo-root-relative (default: ".")

set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:-compile}"
REPO_ROOT="${REPO_ROOT:-${GITHUB_WORKSPACE:-$(pwd)}}"
TAURI_DIR="${TAURI_DIR:-.}"

[ -d "${ARTIFACT_DIR}" ] || { echo "::error::compile artifact dir not found: ${ARTIFACT_DIR}"; exit 1; }

# Copy the staged tree back over the checkout, preserving the relative layout.
# cp -R of the artifact's top-level entries into REPO_ROOT reproduces each
# repo-root-relative path the stager recorded.
shopt -s dotglob nullglob
entries=("${ARTIFACT_DIR}"/*)
[ "${#entries[@]}" -gt 0 ] || { echo "::error::compile artifact dir is empty: ${ARTIFACT_DIR}"; exit 1; }
cp -R "${entries[@]}" "${REPO_ROOT}/"
shopt -u dotglob nullglob

# Re-assert +x on the top-level target/release outputs (the compiled binary).
release_dir="${REPO_ROOT}/${TAURI_DIR%/}/src-tauri/target/release"
if [ -d "${release_dir}" ]; then
  while IFS= read -r f; do
    chmod +x "${f}"
  done < <(find "${release_dir}" -maxdepth 1 -type f)
fi

echo "Restored compile outputs into ${REPO_ROOT} (tauri-dir: ${TAURI_DIR})."
if [ -d "${release_dir}" ]; then
  echo "target/release top level:"
  find "${release_dir}" -maxdepth 1 -type f | sed 's/^/  /'
fi
