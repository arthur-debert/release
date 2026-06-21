#!/usr/bin/env bash
# Install the SLIM Linux dependency set the Tauri *package* job needs.
#
# The package job does NOT compile — it runs `tauri bundle` on a pre-built
# binary downloaded as an artifact from the build job. So it needs only the
# packaging/runtime deps, never the compile toolchain:
#   - no `-dev` headers (libwebkit2gtk-4.1-DEV, libgtk-3-DEV, …): those are for
#     compiling/linking the Rust app, which already happened in build.
#   - no `mold` linker: nothing links in this job.
#
# What `tauri bundle` actually shells out to on Linux:
#   .deb       -> `dpkg-deb` (preinstalled on the ubuntu runner) + `patchelf`.
#   .AppImage  -> `patchelf` + the RUNTIME GUI libraries linuxdeploy copies into
#                 the image: the webkit2gtk + gtk runtime, the gstreamer runtime
#                 (webkit pulls it in), and librsvg2 for icon rasterization.
#                 The appimage tooling itself is downloaded by tauri at bundle
#                 time, so it is not installed here.
#
# Keep this separate from install-tauri-linux-deps.sh: that script is the full
# COMPILE set shared by the prepare (clippy needs glib dev headers) and build
# jobs, both of which legitimately need `-dev` + `mold`. Slimming it would
# break those; slimming here keeps the package env honest — provision the
# smallest system for the job.

set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  libwebkit2gtk-4.1-0 \
  libgtk-3-0 \
  libgstreamer1.0-0 \
  libgstreamer-plugins-base1.0-0 \
  librsvg2-2 \
  patchelf
