#!/usr/bin/env bash
#
# setup-cargo-git-auth.sh — let cargo authenticate PRIVATE git dependencies.
#
# A Rust/Tauri project's Cargo.toml may pin a `git = ` dependency on a PRIVATE
# GitHub repo (e.g. a Tauri app whose src-tauri/ depends on a private engine
# crate). cargo's default libgit2 fetch backend cannot use gh's credential
# helper, so the clone fails with:
#
#   failed to authenticate when downloading repository
#
# Fix: force cargo to fetch via the git CLI, and rewrite the public github.com
# URL to embed the CI token. This must run as its OWN workflow step BEFORE any
# step that invokes cargo (the prepare gate, the tauri build): the
# `CARGO_NET_GIT_FETCH_WITH_CLI` line is appended to $GITHUB_ENV so it persists
# into the later cargo step in the same job, and the `insteadOf` rewrite is a
# global git config that likewise persists.
#
# Generic + safe for every consumer: a NO-OP when GH_TOKEN is absent (a project
# with only public deps needs no auth), and harmless when present but unused
# (the token reads public repos fine). The release path always has GH_TOKEN
# (RELEASE_TOKEN), mirroring what tauri-ci.yml already does for the test path
# via the consumer's pre-test hook.
set -euo pipefail

if [[ -z "${GH_TOKEN:-}" ]]; then
	echo "setup-cargo-git-auth: no GH_TOKEN — skipping (a project with only public cargo git deps needs no auth)."
	exit 0
fi

# Embed the token for github.com clones (covers the private dep + any transitive
# git deps). The runner is ephemeral and the token is masked in logs.
git config --global \
	"url.https://x-access-token:${GH_TOKEN}@github.com/.insteadOf" \
	"https://github.com/"

# cargo must use the git CLI for the insteadOf rewrite to apply (libgit2 ignores
# git's url.insteadOf). Persist to the job env so the later cargo step sees it.
if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CARGO_NET_GIT_FETCH_WITH_CLI=true" >>"${GITHUB_ENV}"
else
	export CARGO_NET_GIT_FETCH_WITH_CLI=true
fi

echo "setup-cargo-git-auth: configured git CLI auth for private cargo git deps."
