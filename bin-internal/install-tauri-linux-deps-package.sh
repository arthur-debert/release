#!/usr/bin/env bash
# Install the Linux dependency set the Tauri *package* job needs to BUNDLE a
# pre-built binary (it does NOT compile — the build job already did).
#
# This is slimmer than the full COMPILE set (install-tauri-linux-deps.sh, used by
# prepare+build): it drops the deps needed ONLY to compile/link the Rust app —
# the webkit/soup/appindicator `-dev` headers and the `mold` linker. But it is
# NOT just the runtime `-0`/`-1` libs: AppImage bundling needs more than that.
#
# What `tauri bundle` shells out to on Linux, and what each dep is FOR:
#   .deb       -> `dpkg-deb` (preinstalled on the ubuntu runner) + `patchelf`.
#   .AppImage  -> tauri runs linuxdeploy + its gtk/gstreamer plugins (downloaded
#                 at bundle time). Empirically reproduced (release#841 Docker
#                 harness — bundle the real phos artifact in ubuntu:24.04):
#     * linuxdeploy-plugin-gtk reads GTK-stack pkg-config metadata and ABORTS
#       (`exit 1`) if the `.pc` files are missing — it queries gtk+-3.0,
#       gdk-pixbuf-2.0, gio/gobject-2.0, pango/pangocairo/pangoft2, librsvg-2.0
#       with no fallback. Those `.pc` files live in the `-dev` packages, NOT the
#       `-0` runtime libs. `libgtk-3-dev` + `librsvg2-dev` transitively provide
#       ALL of them (gtk pulls glib/gdk-pixbuf/pango dev + glib's tooling bin),
#       so they are the minimal `.pc` providers. This is the regression the
#       earlier runtime-only slim set caused (#836 -> rc.3 failed here): a
#       `-dev`-less env builds `.deb` fine but `.AppImage` dies in linuxdeploy
#       (whose stderr tauri swallows as a bare "failed to run linuxdeploy").
#     * the GUI runtime `.so`s the app links, which linuxdeploy copies into the
#       image: webkit2gtk, gstreamer (webkit needs it), ayatana appindicator
#       (tray). gtk + rsvg runtimes come transitively via the two `-dev` pkgs.
#     * `librsvg2-common` — the SVG gdk-pixbuf loader the gtk plugin copies so
#       the AppImage can rasterize SVG icons.
#     * `patchelf` — linuxdeploy rewrites rpaths with it.
#   (appimagetool also needs `file(1)`, but the ubuntu runner image ships it.)
#
# Keep this separate from install-tauri-linux-deps.sh: that script is the full
# COMPILE set (adds webkit/soup/appindicator `-dev` + `mold`) shared by prepare
# (clippy needs glib dev headers) and build. Slimming it would break those.

set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  libgtk-3-dev \
  librsvg2-dev \
  libwebkit2gtk-4.1-0 \
  libgstreamer1.0-0 \
  libgstreamer-plugins-base1.0-0 \
  libayatana-appindicator3-1 \
  librsvg2-common \
  patchelf
