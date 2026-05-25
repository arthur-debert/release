#!/usr/bin/env bash
# Auto-detect the JS package manager and install dependencies.
# Portable across pnpm / npm / yarn consumers.

set -euo pipefail

if [ -f pnpm-lock.yaml ]; then
  pnpm install --frozen-lockfile
elif [ -f package-lock.json ]; then
  npm ci
elif [ -f yarn.lock ]; then
  yarn install --frozen-lockfile
else
  npm install
fi
