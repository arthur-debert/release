#!/usr/bin/env bash
# Auto-detect the JS package manager and install dependencies.
# Portable across pnpm / npm / yarn (classic + Berry) consumers.
#
# Env vars:
#   TAURI_DIR   install from this directory (default: ".")

set -euo pipefail

cd "${TAURI_DIR:-.}"

if [ -f pnpm-lock.yaml ]; then
  corepack enable >/dev/null 2>&1 || true
  pnpm install --frozen-lockfile
elif [ -f yarn.lock ]; then
  corepack enable >/dev/null 2>&1 || true
  if yarn --version 2>/dev/null | grep -q '^1\.'; then
    yarn install --frozen-lockfile
  else
    yarn install --immutable
  fi
elif [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
