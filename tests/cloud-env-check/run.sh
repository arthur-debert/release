#!/usr/bin/env bash
# Run env/setup.sh + a consumer repo's scripts/setup-dev-env.sh + lefthook
# + tests inside the cloud-env-approximating Docker image. Reports which
# step (if any) failed.
#
# Usage:
#   ./run.sh <org>/<repo> [<branch>] [<test-cmd>]
#
# Examples:
#   ./run.sh arthur-debert/dodot
#   ./run.sh arthur-debert/phos-core claude/check-environment-setup-DEWLx
#   ./run.sh lex-fmt/lexed main "pnpm test"
#
# Inputs from environment:
#   GH_TOKEN       — required, used inside the container to clone via https
#   RELEASE_REPO   — path to a local checkout of arthur-debert/release
#                    (defaults to the directory containing this script's
#                    grandparent — i.e. the repo root). env/setup.sh is
#                    bind-mounted into the container from here.
#
# Exit code:
#   0 on full success (env-setup + dev-env + lefthook + tests all green)
#   1 on any step failure (stderr says which)

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <org>/<repo> [<branch>] [<test-cmd>]" >&2
  exit 2
fi

REPO="$1"
BRANCH="${2:-main}"
TEST_CMD="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RELEASE_REPO="${RELEASE_REPO:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
IMAGE_TAG="cloud-env-check:base"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "error: GH_TOKEN not set (needed inside the container to clone repos)" >&2
  exit 2
fi

# Build base image if missing (cached afterwards).
if ! docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
  echo "==> building base image ${IMAGE_TAG} (first run only) ..."
  docker build -t "${IMAGE_TAG}" "${SCRIPT_DIR}"
fi

# Auto-derive test command from stack if not given.
if [ -z "${TEST_CMD}" ]; then
  TEST_CMD='auto'
fi

# Construct the in-container script. We use a heredoc so the host's quoting
# stays simple and the container script can reference $REPO, $BRANCH freely.
CONTAINER_SCRIPT="$(cat <<'INNER'
set -uo pipefail

step() { echo; echo "==> [$1] $2"; }
fail() { echo "✗ FAILED at step: $1" >&2; exit 1; }

# --- Step 1: env/setup.sh ------------------------------------------------
# Setup scripts run as root in real cloud, with HOME=/root. We use sudo -H
# (not sudo -E) so npm/cargo writes its global cache under /root, not under
# the ubuntu user's home — otherwise step 3 (running setup-dev-env.sh as
# ubuntu) can't write to its own .npm cache.
step env "running /mnt/release/env/setup.sh (as root via sudo -H)"
if ! sudo -H bash /mnt/release/env/setup.sh; then
  fail env
fi

# --- Step 2: clone consumer repo -----------------------------------------
# Do NOT echo the clone URL — it contains GH_TOKEN. Print the repo + branch
# only; the token never appears in the log. Fail explicitly if the
# requested branch does not exist (no silent fallback to the default
# branch — testing the wrong revision is worse than failing loudly).
# Once the clone succeeds, drop the credential from git remote.origin.url
# and unset GH_TOKEN inside the container so subsequent steps
# (setup-dev-env.sh, lefthook, tests) cannot exfiltrate it.
step clone "git clone https://github.com/${REPO}.git --branch ${BRANCH}"
mkdir -p /workspace
cd /workspace
CLONE_URL="https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git"
if ! git clone --quiet "${CLONE_URL}" repo --branch "${BRANCH}" 2>/dev/null; then
  fail clone
fi
unset CLONE_URL
cd repo
# Remove the token from the saved remote URL and from the process env so
# nothing downstream can see it.
git remote set-url origin "https://github.com/${REPO}.git"
unset GH_TOKEN

# --- Step 3: scripts/setup-dev-env.sh ------------------------------------
# Run as a child process — sourcing the consumer's script would let any
# `exit 0` in it terminate the harness. The downside is that exports
# from the child (e.g. `export DISPLAY=:99` for the Xvfb wrapper) die
# with the child. We mitigate the DISPLAY case below by reading what
# the script may have written to ~/.bashrc — the canonical Xvfb extras
# also persist DISPLAY there for interactive shells, so picking it up
# here keeps lefthook + tests aligned with real cloud usage. Other
# script-level exports (PATH additions, language-specific vars) won't
# propagate; consumers that need those at lefthook/tests time should
# set them in lefthook.yml or in the test command directly.
if [ -f scripts/setup-dev-env.sh ]; then
  step dev-env "running scripts/setup-dev-env.sh"
  bash scripts/setup-dev-env.sh || fail dev-env
  if grep -qs '^export DISPLAY=:99' "${HOME}/.bashrc" 2>/dev/null; then
    export DISPLAY=:99
  fi
else
  step dev-env "(no scripts/setup-dev-env.sh — skipping)"
fi

# --- Step 4: lefthook pre-commit -----------------------------------------
if [ -f lefthook.yml ] || [ -f lefthook.yaml ] || [ -f .lefthook.yml ]; then
  step lefthook "lefthook run pre-commit --all-files"
  lefthook run pre-commit --all-files || fail lefthook
else
  step lefthook "(no lefthook.yml — skipping)"
fi

# --- Step 5: primary test command ----------------------------------------
TEST_CMD_RESOLVED="${TEST_CMD}"
if [ "${TEST_CMD_RESOLVED}" = "auto" ]; then
  # No `|| true` suppressions — the whole point of the harness is to
  # report the first failing step. If your repo's test command is
  # noisy/flaky, pass an override (3rd arg to ./run.sh) rather than
  # silencing failures here.
  if [ -f Cargo.toml ]; then
    TEST_CMD_RESOLVED="cargo test --no-run --locked"
  elif [ -f pnpm-lock.yaml ]; then
    TEST_CMD_RESOLVED="pnpm -s test"
  elif [ -f yarn.lock ]; then
    TEST_CMD_RESOLVED="yarn test"
  elif [ -f package.json ]; then
    TEST_CMD_RESOLVED="npm test"
  elif [ -f Gemfile ]; then
    TEST_CMD_RESOLVED="bundle exec rake test"
  elif [ -f pyproject.toml ]; then
    TEST_CMD_RESOLVED="python3 -m pytest -q"
  else
    TEST_CMD_RESOLVED=""
  fi
fi

if [ -n "${TEST_CMD_RESOLVED}" ]; then
  step tests "${TEST_CMD_RESOLVED}"
  eval "${TEST_CMD_RESOLVED}" || fail tests
else
  step tests "(no test command detected — skipping)"
fi

echo
echo "✓ ALL GREEN for ${REPO}@${BRANCH}"
INNER
)"

docker run --rm \
  -e GH_TOKEN \
  -e REPO="${REPO}" \
  -e BRANCH="${BRANCH}" \
  -e TEST_CMD="${TEST_CMD}" \
  -v "${RELEASE_REPO}:/mnt/release:ro" \
  "${IMAGE_TAG}" \
  bash -c "${CONTAINER_SCRIPT}"
