#!/usr/bin/env bats

# Denoise fixture suite (#740 item 1) — the drift gate for the bot-markdown
# de-noiser in skills/review-audit/scripts/audit_config.py:clean().
#
# WHY this exists: the denoise regexes (DETAILS / BADGE / IMG / BOILER ...) are
# tuned to CodeRabbit / Copilot / Gemini output. The day a bot changes its
# format, the regexes silently rot — either stop shrinking (the judge drowns in
# <details> walls) or, far worse, start eating the `Addressed in <sha>` outcome
# markers the whole pipeline depends on. A bad audit report is the only place
# that drift surfaces today. These fixtures + assertions turn it into a red CI.
#
# Each fixture under fixtures/ is a realistic bot blob: collapsible walls,
# badges, images and boilerplate AROUND an outcome marker. clean() must shrink
# the blob substantially AND keep the marker.

FIX="$BATS_TEST_DIRNAME/fixtures"
CLEAN="python3 $BATS_TEST_DIRNAME/clean_fixture.py"

# assert_denoise <fixture> <min_shrink_ratio> <marker>
#   raw / cleaned >= ratio, marker retained, no <details>/badge residue.
assert_denoise() {
  local fixture="$FIX/$1" ratio="$2" marker="$3"
  [ -f "$fixture" ]
  local raw cleaned raw_len clean_len
  raw_len=$(wc -c <"$fixture")
  cleaned=$($CLEAN "$fixture")
  # Measure cleaned in BYTES too (wc -c), not characters (${#cleaned}): the
  # fixtures carry a multibyte marker (✅), so a char count would disagree with
  # raw_len's byte count and skew the ratio by locale. Compare via
  # multiplication to avoid integer-division truncation.
  clean_len=$(printf '%s' "$cleaned" | wc -c)

  # (a) shrinks substantially
  [ "$clean_len" -gt 0 ]
  [ "$raw_len" -ge "$((ratio * clean_len))" ]
  # (b) outcome marker survives — the whole point
  [[ "$cleaned" == *"$marker"* ]]
  # (c) boilerplate is actually gone
  [[ "$cleaned" != *"<details>"* ]]
  [[ "$cleaned" != *"<summary>"* ]]
  [[ "$cleaned" != *"img.shields.io"* ]]
  [[ "$cleaned" != *"This review was generated"* ]]
}

@test "coderabbit: <details> walls shrink >=8x, 'Addressed in abc1234' kept" {
  assert_denoise coderabbit.md 8 "Addressed in abc1234"
}

@test "copilot: boilerplate shrinks >=2x, 'Addressed in deadbee' kept" {
  assert_denoise copilot.md 2 "Addressed in deadbee"
}

@test "gemini: boilerplate shrinks >=2x, 'Addressed in c0ffee0' kept" {
  assert_denoise gemini.md 2 "Addressed in c0ffee0"
}

@test "every fixture carries an outcome marker to guard (not vacuous)" {
  # If a fixture loses its marker, the per-bot asserts above could pass
  # vacuously on a marker that was never there. Guard the inputs.
  grep -q "Addressed in " "$FIX/coderabbit.md"
  grep -q "Addressed in " "$FIX/copilot.md"
  grep -q "Addressed in " "$FIX/gemini.md"
}

@test "the marker assertion is real — clean() DOES drop a marker it can reach" {
  # Non-vacuity proof: a marker buried INSIDE a <details> wall is removed by
  # clean(). So the positive asserts pass because the fixtures place markers
  # OUTSIDE the stripped regions, not because clean() can never remove a marker.
  local blob out
  blob=$'before\n<details>\n<summary>x</summary>\n✅ Addressed in beefbee\n</details>\nafter'
  out=$(printf '%s' "$blob" | python3 -c '
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("'"$BATS_TEST_DIRNAME"'").resolve().parents[1] / "skills" / "review-audit" / "scripts"))
from audit_config import clean
sys.stdout.write(clean(sys.stdin.read()))')
  [[ "$out" != *"Addressed in beefbee"* ]]
}
