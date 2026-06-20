#!/usr/bin/env bash
# Install the system libraries Tauri 2.x needs on Linux.
# Called from both the prepare job (pre-commit gate needs glib-2.0
# for cargo clippy) and the build job.

set -euo pipefail

# `mold` is the fast Linux linker. It is installed unconditionally so a consumer
# that commits a mold-selecting `.cargo/config.toml`
# (`[target.x86_64-unknown-linux-gnu] rustflags = ["-C",
# "link-arg=-fuse-ld=mold"]`) builds in the release Linux lane without the link
# step erroring on a missing linker. mold is a small package and a no-op for
# consumers that don't select it (an installed-but-unused linker), so it is
# carried here rather than behind a per-consumer input. The reason to carry it
# is build ROBUSTNESS for the committed config, not CI speed — the linker only
# replaces the final link, negligible on a cold compile-dominated CI build
# (measured in phos-editor/app#318).
sudo apt-get update
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libsoup-3.0-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  mold \
  patchelf
