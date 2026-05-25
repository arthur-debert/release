#!/usr/bin/env bash
# Install the system libraries Tauri 2.x needs on Linux.
# Called from both the prepare job (pre-commit gate needs glib-2.0
# for cargo clippy) and the build job.

set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libsoup-3.0-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  patchelf
