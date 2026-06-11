#!/usr/bin/env bats
# setup-dev-env.sh §0.1/§0.2 — boot order + pre-commit hook wiring (release#567).
#
# The first-commit-of-session boot hole: `.release/` is ephemeral (gitignored,
# WS4), so every session starts without the gate config until `release-core
# init` composes it. The fix under test:
#   * the pull-model boot (§0.1, install-release-core → init) runs BEFORE the
#     hook wiring (§0.2), so the very first session of a fresh clone wires a
#     hook that has a materialized gate to run;
#   * a consumer (no root lefthook.yml) gets the binary hook wired EVEN when
#     init failed and `.release/lefthook.yml` is absent — its commits then hit
#     `release-core gate --hook`'s fail-loud unmaterialized-config error
#     instead of running with no hook at all;
#   * release-self / not-yet-migrated repos (root lefthook.yml, no managed
#     config) keep the stock `lefthook install` wiring.
#
# Fully offline: stub gate tools report EXACTLY the pinned versions (so the §0
# reconcile no-ops without npm/pip), and stub install-release-core /
# release-core / lefthook record their invocations in an order log.

ROOT="$BATS_TEST_DIRNAME/../.."
SETUP="$ROOT/templates/commons/bin/setup-dev-env.sh"
VERSIONS="$ROOT/templates/commons/bin/gate-tool-versions.sh"

setup() {
  WORK="$(mktemp -d)"
  STUB="$WORK/stub"
  REPO="$WORK/repo"
  mkdir -p "$STUB" "$REPO"
  cd "$REPO"
  git init -q .

  export ORDER_LOG="$WORK/order.log"
  : > "$ORDER_LOG"

  # The bash that runs the script — resolved BEFORE PATH is restricted.
  BASH_BIN="$(command -v bash)"
  # The restricted PATH the script runs under: stubs + the system dirs that
  # carry git/grep/head/mktemp.
  RUN_PATH="$STUB:/usr/bin:/bin"

  # Stub every gate tool at its pin so the §0 reconcile is a no-op (no real
  # npm/pip/curl on PATH, and none needed).
  # shellcheck source=/dev/null
  . "$VERSIONS"
  _mk_versioned() { # <name> <version>
    cat > "$STUB/$1" <<EOF
#!/bin/sh
[ "\$1" = "--version" ] && { echo "$1 $2"; exit 0; }
exit 0
EOF
    chmod +x "$STUB/$1"
  }
  _mk_versioned prettier "$PRETTIER_VERSION"
  _mk_versioned markdownlint "$MARKDOWNLINT_CLI_VERSION"
  _mk_versioned ruff "$RUFF_VERSION"
  _mk_versioned yamllint "$YAMLLINT_VERSION"
  _mk_versioned shellcheck "$SHELLCHECK_VERSION"
  _mk_versioned actionlint "$ACTIONLINT_VERSION"

  # lefthook: pinned version + logs `lefthook install` (the legacy root-config
  # wiring path).
  cat > "$STUB/lefthook" <<EOF
#!/bin/sh
case "\$1" in
  --version) echo "lefthook version ${LEFTHOOK_VERSION}" ;;
  install)   echo "lefthook install" >> "\$ORDER_LOG" ;;
esac
exit 0
EOF
  chmod +x "$STUB/lefthook"

  # install-release-core: logs the boot, and (when MATERIALIZE=1) simulates
  # init composing the ephemeral gate config.
  cat > "$STUB/install-release-core" <<'EOF'
#!/bin/sh
echo "install-release-core" >> "$ORDER_LOG"
if [ "${MATERIALIZE:-0}" = "1" ]; then
  mkdir -p .release
  echo "pre-commit:" > .release/lefthook.yml
fi
exit 0
EOF
  chmod +x "$STUB/install-release-core"

  # release-core: logs every call; `gate --install-hook` writes the binary hook
  # like the real verb does.
  cat > "$STUB/release-core" <<'EOF'
#!/bin/sh
echo "release-core $*" >> "$ORDER_LOG"
if [ "$1" = "gate" ] && [ "$2" = "--install-hook" ]; then
  hooks_dir="$(git rev-parse --git-path hooks)"
  mkdir -p "$hooks_dir"
  printf '#!/bin/sh\nexec release-core gate --hook "$@"\n' > "$hooks_dir/pre-commit"
  chmod +x "$hooks_dir/pre-commit"
fi
exit 0
EOF
  chmod +x "$STUB/release-core"
}

teardown() {
  rm -rf "$WORK"
}

_run_setup() {
  run env PATH="$RUN_PATH" CLAUDE_CODE_REMOTE= ORDER_LOG="$ORDER_LOG" \
    MATERIALIZE="${MATERIALIZE:-0}" "$BASH_BIN" "$SETUP"
}

@test "fresh consumer clone: pull-model boot runs BEFORE hook wiring, binary hook lands" {
  MATERIALIZE=1
  _run_setup
  [ "$status" -eq 0 ]
  # Order: the boot (which materializes .release/) precedes the hook install —
  # the release#567 first-session fix.
  grep -n "install-release-core" "$ORDER_LOG"
  grep -n "release-core gate --install-hook" "$ORDER_LOG"
  boot_line="$(grep -n '^install-release-core$' "$ORDER_LOG" | head -1 | cut -d: -f1)"
  wire_line="$(grep -n '^release-core gate --install-hook$' "$ORDER_LOG" | head -1 | cut -d: -f1)"
  [ "$boot_line" -lt "$wire_line" ]
  # The binary hook is wired.
  hook="$REPO/.git/hooks/pre-commit"
  [ -x "$hook" ]
  grep -q "release-core gate --hook" "$hook"
}

@test "consumer with a FAILED init (no .release/lefthook.yml) still gets the binary hook" {
  # No root lefthook.yml + no managed config: the consumer shape with init not
  # (yet) run. The hook must be wired anyway so commits hit the gate's
  # fail-loud unmaterialized-config error — never run hookless/ungated.
  MATERIALIZE=0
  _run_setup
  [ "$status" -eq 0 ]
  [ ! -f .release/lefthook.yml ]
  grep -q '^release-core gate --install-hook$' "$ORDER_LOG"
  [ -x "$REPO/.git/hooks/pre-commit" ]
}

@test "release-self shape (root lefthook.yml) keeps stock lefthook install, not the binary hook" {
  echo "pre-commit:" > lefthook.yml
  MATERIALIZE=0
  _run_setup
  [ "$status" -eq 0 ]
  grep -q '^lefthook install$' "$ORDER_LOG"
  ! grep -q 'gate --install-hook' "$ORDER_LOG"
}

@test "managed config present AND a root lefthook.yml: the binary hook wins" {
  # A not-yet-cleaned consumer carrying both: the managed .release/ gate is
  # authoritative (same precedence as before release#567).
  echo "pre-commit:" > lefthook.yml
  MATERIALIZE=1
  _run_setup
  [ "$status" -eq 0 ]
  grep -q '^release-core gate --install-hook$' "$ORDER_LOG"
  ! grep -q '^lefthook install$' "$ORDER_LOG"
}
