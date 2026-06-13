#!/usr/bin/env bats

# release#531 F1 — the bot gate must not silently skip on a post-WS3 managed
# consumer. Post-WS3 the gate config lives only in the ephemeral .release/
# (no root lefthook.yml); post-WS4 nothing materializes .release/ outside
# rust-cli's arm-gate. run-precommit-gate.sh's detection chain then found no
# config and skipped — a hollow green on every non-rust bot commit. The fix:
# when release-core is on PATH (every release flow installs it), MATERIALIZE
# the managed tree (release-core init --no-commit) and run the real gate
# through the binary (release-core gate --hook). These tests drive the script
# against a logging release-core stub — offline, no real wheel.

load helper

GATE="$BATS_TEST_DIRNAME/../../bin-internal/run-precommit-gate.sh"

# Override helper.bash's setup(): same workspace + hermetic git identity, but
# WITHOUT the release_core venv — these tests stub release-core deliberately.
setup() {
  harness_create_workspace_notrap
  TMPDIR_TEST="$HARNESS_WORKSPACE"
  cd "$HARNESS_WORKSPACE"
  git init -q
  git config user.email "tests@release.invalid"
  git config user.name "Release Tests"
  git config commit.gpgsign false
  git config tag.gpgsign false
  git config init.defaultBranch main
  export STUB="$PWD/stubbin"
  mkdir -p "$STUB"
  export CALLS="$PWD/calls.log"
  : > "$CALLS"
  # A logging release-core stub: records argv; `init` fabricates the managed
  # config exactly like the real materialize would.
  cat > "$STUB/release-core" <<'STUB'
#!/usr/bin/env bash
printf 'release-core %s\n' "$*" >> "$CALLS"
if [ "$1" = "init" ]; then
  mkdir -p .release
  : > .release/lefthook.yml
fi
exit 0
STUB
  chmod +x "$STUB/release-core"
}

_stage_a_file() {
  echo "x" > bumped.txt
  git add bumped.txt
}

@test "managed consumer, no root config: materializes then gates through the binary" {
  _stage_a_file
  PATH="$STUB:$PATH" run bash "$GATE"
  [ "$status" -eq 0 ]
  grep -q 'release-core init --no-commit' "$CALLS"
  grep -q -- 'release-core gate --hook --file bumped.txt' "$CALLS"
}

@test "managed consumer, .release/lefthook.yml already present: the #530 LEFTHOOK_CONFIG path wins (no re-init)" {
  # When arm-gate (or a prior step) already materialized .release/, the script
  # keeps its existing behavior: export LEFTHOOK_CONFIG and run lefthook
  # directly (the rust path, #530). The new release-core branch exists for the
  # previously-hollow case (nothing materialized) only.
  mkdir -p .release && : > .release/lefthook.yml
  cat > "$STUB/lefthook" <<'STUB'
#!/usr/bin/env bash
printf 'lefthook %s [cfg=%s]\n' "$*" "${LEFTHOOK_CONFIG:-}" >> "$CALLS"
exit 0
STUB
  chmod +x "$STUB/lefthook"
  _stage_a_file
  PATH="$STUB:$PATH" run bash "$GATE"
  [ "$status" -eq 0 ]
  ! grep -q 'release-core init' "$CALLS"
  grep -q 'lefthook run pre-commit --file bumped.txt \[cfg=.release/lefthook.yml\]' "$CALLS"
}

# NOTE: the pre-WS3 "root lefthook.yml takes precedence" test was removed in
# #569 B4 along with the root-config fallback it exercised — 0/19 consumers track
# a root config, and the LEFTHOOK_CONFIG path above already covers the managed
# (.release/lefthook.yml) case.

@test "no config and no release-core: loud skip, exit 0" {
  _stage_a_file
  # Constrained PATH: keep the dir of the CURRENT bash (>=4 for mapfile —
  # /bin on Linux, homebrew on macOS where /bin/bash is 3.2) + system dirs,
  # which excludes release-core (~/.local/bin / a venv) on both.
  bash_dir="$(dirname "$(command -v bash)")"
  run env PATH="$bash_dir:/usr/bin:/bin" bash "$GATE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no release-core"* ]]
}

@test "gate failure through the binary is LOUD (non-zero), not a skip" {
  cat > "$STUB/release-core" <<'STUB'
#!/usr/bin/env bash
printf 'release-core %s\n' "$*" >> "$CALLS"
if [ "$1" = "init" ]; then mkdir -p .release; : > .release/lefthook.yml; exit 0; fi
exit 1
STUB
  chmod +x "$STUB/release-core"
  _stage_a_file
  PATH="$STUB:$PATH" run bash "$GATE"
  [ "$status" -ne 0 ]
}
