# Shared setup for tests/audit/*.bats — exercises the audit shims' CLI contract
# (help / usage / flag-arity / --json shape) fully offline. A stub `gh` on PATH
# answers the API calls the verbs make, so no network is touched.

source "$BATS_TEST_DIRNAME/../../templates/components/bats/lib/bats-harness.bash"

BIN="$BATS_TEST_DIRNAME/../../bin"

setup() {
  harness_create_workspace_notrap
  cd "$HARNESS_WORKSPACE"
}

teardown() {
  cd /
  harness_cleanup
}

# Drop a stub `gh` on PATH that dispatches by sub-args. Each test installs the
# canned responses it needs via files under $PWD/gh-responses; the stub echoes
# the matching file's contents. A b64-content contents response is built with
# _gh_contents.
_install_stub_gh() {
  mkdir -p stub gh-responses
  cat > stub/gh <<'STUB'
#!/usr/bin/env bash
# Minimal gh stub: route `gh api <path>` to a canned file, else empty/success.
# `gh repo view` succeeds. Unknown api paths exit 1 (emulating a 404) so the
# verb's GhError->None path is exercised.
cmd=$1; shift || true
case "$cmd" in
  repo)
    # gh repo view [repo] [--json ...] — succeed; emit nameWithOwner if asked.
    for a in "$@"; do
      if [ "$a" = "nameWithOwner" ]; then echo "o/r"; fi
    done
    exit 0
    ;;
  api)
    # last non-flag arg is the path
    path=""
    for a in "$@"; do
      case "$a" in -*) ;; *) path=$a ;; esac
    done
    key=$(printf '%s' "$path" | sed 's#[/?&=]#_#g')
    f="$GH_RESPONSES/$key"
    if [ -f "$f" ]; then cat "$f"; exit 0; fi
    # default: not found
    echo "gh: Not Found (HTTP 404)" >&2
    exit 1
    ;;
  *) exit 0 ;;
esac
STUB
  chmod +x stub/gh
  export GH_RESPONSES="$PWD/gh-responses"
  export PATH="$PWD/stub:$PATH"
}

# _gh_api <path> <json>  — register a raw JSON body for an api path.
_gh_api() {
  local path=$1 body=$2
  local key
  key=$(printf '%s' "$path" | sed 's#[/?&=]#_#g')
  printf '%s' "$body" > "$GH_RESPONSES/$key"
}

# _gh_contents <path> <text> — register a contents API response whose .content
# is the base64 of <text>.
_gh_contents() {
  local path=$1 text=$2 b64
  b64=$(printf '%s' "$text" | base64 | tr -d '\n')
  _gh_api "repos/o/r/contents/$path" "{\"content\":\"$b64\"}"
}
