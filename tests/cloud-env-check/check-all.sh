#!/usr/bin/env bash
# Run ./run.sh against every consumer repo and report which steps pass.
# Each repo's PR branch (not main) is checked when a dev-env PR exists,
# so we see the latest setup-dev-env.sh attempt against the latest
# env/setup.sh in this repo.
#
# Usage:
#   ./check-all.sh            # all repos
#   ./check-all.sh dodot      # filter by substring

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="${SCRIPT_DIR}/run.sh"

# repo | branch (PR head if open, else main) | optional test-cmd override
# Branches come from the Phase 1A recon report.
TARGETS=(
  "lex-fmt/lex|claude/check-environment-setup-jjUUi|"
  "lex-fmt/lexed|claude/check-environment-setup-RKR0x|"
  "lex-fmt/vscode|claude/test-cloud-environment-0OAmI|"
  "lex-fmt/nvim|claude/check-environment-setup-2Mtis|"
  "lex-fmt/zed-lex|claude/check-environment-setup-dWb0a|"
  "lex-fmt/mkdocs-lex|main|"
  "lex-fmt/tree-sitter-lex|claude/check-environment-setup-LDA4N|"
  "lex-fmt/comms|claude/test-cloud-environment-XGMuX|"
  "arthur-debert/arami-core|claude/check-environment-setup-DEWLx|"
  "arthur-debert/arami-app|claude/check-environment-setup-h5icE|"
  "arthur-debert/dodot|main|"
  "arthur-debert/rustloc|claude/check-environment-setup-rjefX|"
  "arthur-debert/padz|claude/check-environment-setup-5NDhH|"
  "arthur-debert/standout|main|"
  "arthur-debert/clapfig|main|"
  "arthur-debert/burgertocow|main|"
)

FILTER="${1:-}"
LOG_DIR="${SCRIPT_DIR}/.logs"
mkdir -p "${LOG_DIR}"

declare -a PASS=()
declare -a FAIL=()

for entry in "${TARGETS[@]}"; do
  IFS='|' read -r repo branch test_cmd <<<"${entry}"
  if [ -n "${FILTER}" ] && [[ "${repo}" != *"${FILTER}"* ]]; then
    continue
  fi
  slug="${repo//\//__}"
  log="${LOG_DIR}/${slug}.log"
  echo "============================================================"
  echo "running: ${repo}@${branch}"
  echo "log:     ${log}"
  echo "============================================================"
  if "${RUN}" "${repo}" "${branch}" "${test_cmd}" >"${log}" 2>&1; then
    echo "  ✓ pass"
    PASS+=("${repo}")
  else
    failed_step="$(grep -m1 '✗ FAILED at step' "${log}" | sed 's/.*step: //' || echo unknown)"
    echo "  ✗ fail (${failed_step}) — see ${log}"
    FAIL+=("${repo} (${failed_step})")
  fi
done

echo
echo "============================================================"
echo "SUMMARY"
echo "============================================================"
echo "pass (${#PASS[@]}):"
printf '  - %s\n' "${PASS[@]}"
echo "fail (${#FAIL[@]}):"
printf '  - %s\n' "${FAIL[@]}"

[ "${#FAIL[@]}" -eq 0 ]
