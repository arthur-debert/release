#!/usr/bin/env bash
# Install the SLIM Linux dependency set the Tauri *package* job needs.
#
# The package job does NOT compile — it runs `tauri bundle` on a pre-built
# binary downloaded as an artifact from the build job. So it needs only the
# packaging/runtime deps, never the compile toolchain:
#   - no `-dev` headers (e.g. libwebkit2gtk-4.1-dev, libgtk-3-dev — the dev
#     packages, note the lowercase Debian `-dev` suffix): those are for
#     compiling/linking the Rust app, which already happened in build.
#   - no `mold` linker: nothing links in this job.
#
# What `tauri bundle` actually shells out to on Linux:
#   .deb       -> `dpkg-deb` (preinstalled on the ubuntu runner) + `patchelf`.
#   .AppImage  -> `patchelf` + the RUNTIME GUI libraries linuxdeploy copies into
#                 the image: the webkit2gtk + gtk runtime, the gstreamer runtime
#                 (webkit pulls it in), librsvg2 for icon rasterization, and the
#                 ayatana appindicator RUNTIME lib. A tray-enabled tauri binary
#                 links appindicator; linuxdeploy must find the runtime .so
#                 PRESENT to discover/copy it, else the AppImage ships a subtly
#                 broken tray — and may still build green, so an RC wouldn't
#                 catch it. (Runtime `-1`, not the `-dev` header — still no
#                 compile toolchain.)
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
  libayatana-appindicator3-1 \
  patchelf
