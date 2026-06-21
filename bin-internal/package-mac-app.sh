#!/usr/bin/env bash
# Package the bundled macOS .app into a tarball for the post-hoc reseal payload.
#
# Split out of bundle-tauri.sh (#835) so it runs as its own step after the mac
# bundle. The signer reseals the .dmg from the inner .app, but artifact upload
# does NOT preserve a .app bundle's symlinks / exec bits — a tarball round-trips
# cleanly. The suffix is `.unsigned-app.tar.gz` (NOT `.app.tar.gz`) so it never
# collides with — or gets mistaken for — tauri's own `<app>.app.tar.gz` updater
# bundle, which lives in the same dir.
#
# (The guarantee that the .app EXISTS to package — `app` forced into the mac
# target list even for a dmg-only request — is enforced upstream by
# resolve-tauri-bundles.sh + the always-run mac bundle step.)
#
# Env vars:
#   TAURI_DIR    path to Tauri project root (default: ".")

set -euo pipefail

TAURI_DIR="${TAURI_DIR:-.}"

cd "${TAURI_DIR}"

macos_dir="src-tauri/target/release/bundle/macos"
if [ ! -d "${macos_dir}" ]; then
  echo "::error::no macOS bundle dir (${macos_dir}) — the mac .app bundle step must run first"
  exit 1
fi

# Enforce EXACTLY ONE .app. The reusable signer's unpack-unsigned-app.sh expects
# exactly one *.unsigned-app.tar.gz payload (one .app per build); producing zero
# or multiple here would only surface as a confusing failure later in the sign
# workflow. Count first, fail loud on 0 or >1, then package the single app.
apps=()
while IFS= read -r app; do
  apps+=("${app}")
done < <(find "${macos_dir}" -maxdepth 1 -type d -name '*.app')

if [ "${#apps[@]}" -eq 0 ]; then
  echo "::error::no .app found under ${macos_dir} to package for reseal"
  exit 1
fi
if [ "${#apps[@]}" -gt 1 ]; then
  # Build the name list with a line-based loop so .app names containing spaces
  # are preserved (xargs/paste would split on whitespace and garble them).
  names=""
  for a in "${apps[@]}"; do
    bn=$(basename "${a}")
    names="${names:+${names}, }${bn}"
  done
  echo "::error::expected exactly one .app under ${macos_dir}, found ${#apps[@]}: [${names}]; the signer expects a single .app per build"
  exit 1
fi

app="${apps[0]}"
name=$(basename "${app}" .app)
tar -C "${macos_dir}" -czf "${macos_dir}/${name}.unsigned-app.tar.gz" "$(basename "${app}")"
echo "packaged ${app} -> ${macos_dir}/${name}.unsigned-app.tar.gz"
