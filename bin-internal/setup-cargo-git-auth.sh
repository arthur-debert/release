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
# step that invokes cargo (the prepare gate, the tauri build) so the env it
# writes to $GITHUB_ENV persists into the later cargo step in the same job.
#
# Credentials are injected via git's GIT_CONFIG_COUNT/KEY/VALUE env vars, NOT
# `git config --global`: on SELF-HOSTED runners the home dir (and ~/.gitconfig)
# survives across jobs, so a global write would leave the token in plaintext for
# the next job/user. The env-var form is job-scoped and touches no file on disk.
# git reads these ad-hoc; cargo (git-fetch-with-cli) shells out to git, so it
# picks them up.
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

# cargo must use the git CLI for the insteadOf rewrite to apply (libgit2 ignores
# git's url.insteadOf). Embed the token for github.com clones (covers the private
# dep + any transitive git deps) via job-scoped env, and persist to $GITHUB_ENV
# so the later cargo step sees it. The token came from a secret, so it stays
# masked in logs.
if [[ -n "${GITHUB_ENV:-}" ]]; then
	{
		echo "CARGO_NET_GIT_FETCH_WITH_CLI=true"
		echo "GIT_CONFIG_COUNT=1"
		echo "GIT_CONFIG_KEY_0=url.https://x-access-token:${GH_TOKEN}@github.com/.insteadOf"
		echo "GIT_CONFIG_VALUE_0=https://github.com/"
	} >>"${GITHUB_ENV}"
else
	export CARGO_NET_GIT_FETCH_WITH_CLI=true
	export GIT_CONFIG_COUNT=1
	export GIT_CONFIG_KEY_0="url.https://x-access-token:${GH_TOKEN}@github.com/.insteadOf"
	export GIT_CONFIG_VALUE_0="https://github.com/"
fi

echo "setup-cargo-git-auth: configured git CLI auth for private cargo git deps."
