#!/usr/bin/env bash
# scripts/setup-dev-env.sh — per-session dev-environment setup, invoked by
# the SessionStart hook in .claude/settings.json.
#
# Source of truth: arthur-debert/release templates/setup-dev-env.sh.
# To re-sync, copy this file verbatim over the consumer's
# scripts/setup-dev-env.sh. (The gh-repo-setup skill does not currently
# route this top-level template; it only handles per-stack trees under
# templates/<stack>/.)
# Repos that need project-specific extras (Xvfb daemon, pinned-binary
# fetch, extra rustup targets, etc.) append them below the marker at the
# bottom — anything above it is rsync'd from the template.
#
# Cloud-only: local sessions exit early (devs already have their env).
# Detects stack by filesystem signals — handles rust, node, ruby, python,
# and consumers with no project deps (just lefthook / hand-rolled hook
# wiring).
#
# Idempotent — safe to re-run. Errors are best-effort: a failure in one
# step does not abort the rest (transient registry hiccups shouldn't
# block the lefthook install).

set -euo pipefail

# Cloud-only gate. Local sessions already have their env set up.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

# --- 1. Universal git hygiene --------------------------------------------
# Cloud clones are shallow; restore submodule content and release tags.
# Submodule update is a no-op when in sync; tag fetch is one round-trip.

if [ -f .gitmodules ]; then
  git submodule update --init --recursive --quiet || true
fi
git fetch --tags --quiet origin || true

# --- 2. Project dep cache ------------------------------------------------
# Pick the right tool based on lockfile / manifest. Per stack, idempotent.

# Rust: cargo fetch with --locked so we don't silently mutate Cargo.lock.
if [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then
  cargo fetch --locked --quiet || true
fi

# Node (npm/yarn/pnpm). We deliberately do NOT guard on `! -d node_modules`:
# the env-snapshot caches a node_modules paired with a previous branch's
# lockfile, and a feature branch that bumps the lockfile (Playwright is
# the canonical case) drifts silently. Re-installing when already in sync
# is ~2s; chasing a stale lockfile bug is hours. Pay the two seconds.
if [ -f package.json ]; then
  if [ -f package-lock.json ] && command -v npm >/dev/null 2>&1; then
    npm ci 2>/dev/null || npm install
  elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
    yarn install --frozen-lockfile 2>/dev/null || yarn install
  elif [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
    pnpm install --frozen-lockfile 2>/dev/null || pnpm install
  elif command -v npm >/dev/null 2>&1; then
    # No lockfile committed — repos like tree-sitter-lex deliberately
    # gitignore package-lock.json because the npm deps are dev-only
    # tooling (tree-sitter-cli, bats) and a committed lockfile would be
    # noise to bump. Without this branch, node_modules never gets
    # populated and any `npx <tool>` invocation fails.
    #
    # --no-package-lock matches the consumer's intent: they chose not
    # to commit a lockfile, so we shouldn't generate one in their
    # working tree just because we ran install.
    npm install --no-audit --no-fund --no-package-lock 2>/dev/null \
      || npm install --no-package-lock
  fi
fi

# Ruby / Bundler.
if [ -f Gemfile ] && command -v bundle >/dev/null 2>&1; then
  bundle install --quiet || true
fi

# Python / pip + venv. Triggered by any of the conventional manifests
# (pyproject.toml, requirements.txt, setup.py) so legacy projects are
# covered too. Only initialises if .venv missing — pip install is slower
# than node/cargo and the guard wins more than it costs.
if { [ -f pyproject.toml ] || [ -f requirements.txt ] || [ -f setup.py ]; } \
   && [ ! -d .venv ] && command -v python3 >/dev/null 2>&1; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip --quiet || true
  if [ -f pyproject.toml ]; then
    .venv/bin/pip install -e '.[dev]' --quiet 2>/dev/null \
      || .venv/bin/pip install -e . --quiet 2>/dev/null \
      || true
  elif [ -f requirements.txt ]; then
    .venv/bin/pip install -r requirements.txt --quiet || true
  elif [ -f setup.py ]; then
    .venv/bin/pip install -e . --quiet || true
  fi
fi

# --- 2.5. Chromium NSS DB cert import ------------------------------------
# Cloud sessions route HTTPS through an "Anthropic sandbox-egress…CA"
# proxy that re-signs every leaf cert. That CA ships in the system
# OpenSSL bundle (/etc/ssl/certs/ca-certificates.crt), which is why
# curl and Node succeed. But Chromium on Linux ignores the OpenSSL
# bundle and reads its own NSS DB at ~/.pki/nssdb — without the CA
# imported there, every HTTPS resource an Electron / Playwright test
# loads is rejected with ERR_CERT_AUTHORITY_INVALID. The e2e harness's
# runtime-error fixture surfaces that as a `console.error` and the test
# auto-fails.
#
# Fix: scan the system bundle for any cert whose subject matches
# "Anthropic*sandbox-egress*" and import each as a trusted SSL root.
# Idempotent — re-imports by nickname are no-ops once present. Gated on
# `certutil` AND `openssl` existing (the loop forks openssl to extract
# each subject; both binaries are env-level state on cloud sessions but
# may be absent locally).
#
# Fast-path: grep the bundle once for the Anthropic marker before doing
# any per-cert work. On a non-cloud Linux box the bundle has no such
# certs and the loop is gratuitous (forks openssl ~130×); skip it
# entirely in that case.
if [ "$(uname -s)" = "Linux" ] \
   && command -v certutil >/dev/null 2>&1 \
   && command -v openssl >/dev/null 2>&1 \
   && [ -f /etc/ssl/certs/ca-certificates.crt ] \
   && grep -q 'Anthropic' /etc/ssl/certs/ca-certificates.crt 2>/dev/null; then
  _nssdb="${HOME}/.pki/nssdb"
  mkdir -p "${_nssdb}"
  if [ ! -f "${_nssdb}/cert9.db" ]; then
    certutil -d "sql:${_nssdb}" -N --empty-password >/dev/null 2>&1 || true
  fi
  _ca_tmp="$(mktemp -d)"
  awk '
    /-----BEGIN CERTIFICATE-----/ { n++; fn = sandbox_dir "/cert_" n ".pem"; in_cert = 1 }
    in_cert                       { print > fn }
    /-----END CERTIFICATE-----/   { in_cert = 0; close(fn) }
  ' sandbox_dir="${_ca_tmp}" /etc/ssl/certs/ca-certificates.crt
  for _pem in "${_ca_tmp}"/cert_*.pem; do
    [ -f "${_pem}" ] || continue
    _subject="$(openssl x509 -in "${_pem}" -noout -subject 2>/dev/null || true)"
    case "${_subject}" in
      *Anthropic*sandbox-egress*)
        _nick="$(printf '%s' "${_subject}" | sed -nE 's/.*CN *= *([^,]+).*/\1/p')"
        [ -n "${_nick}" ] || continue
        if ! certutil -d "sql:${_nssdb}" -L -n "${_nick}" >/dev/null 2>&1; then
          certutil -d "sql:${_nssdb}" -A -t "C,," -n "${_nick}" -i "${_pem}" >/dev/null 2>&1 || true
        fi
        ;;
    esac
  done
  rm -rf "${_ca_tmp}"
fi

# --- 3. Pre-commit hook wiring -------------------------------------------
# Default: lefthook (binary installed at env-setup time). Fallback for
# repos that ship a hand-rolled scripts/pre-commit instead (zed-lex,
# tree-sitter-lex pattern): symlink it into .git/hooks/.

if [ -f lefthook.yml ] && command -v lefthook >/dev/null 2>&1; then
  if ! lefthook install >/dev/null; then
    echo "warning: lefthook install failed — pre-commit hook NOT wired" >&2
  fi
elif [ -x scripts/pre-commit ]; then
  mkdir -p .git/hooks
  ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
fi

# --- 4. Project-local extras ---------------------------------------------
# Everything above this marker is the canonical cross-repo setup-dev-env.sh
# from arthur-debert/release templates/setup-dev-env.sh. Do NOT modify it
# in-place; consumers append project-specific steps BELOW this marker.
# (See e.g. lex-fmt/lexed for an Xvfb start, lex-fmt/nvim for pinned-bin
# fetches.)
#
# No trailing `exit 0` — bash exits 0 on EOF when `set -euo pipefail`
# succeeded. Adding one here would make appended extras unreachable.
