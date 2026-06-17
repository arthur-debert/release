#!/usr/bin/env bats

# Pipeline suite (#740 items 3 + 4) for the review-audit extract stage.
#
# Complements denoise.bats (the denoise drift gate) and audit_config.py's inline
# selftest (the pure-helper smoke) by pinning the INTEGRATION points the selftest
# can't reach:
#   * `extract.py --sample N` -> stratified_sample (the very-large-repo lever),
#   * extract.metrics_row folding issue-level bot comments into the "first
#     feedback" clock, so a bot that ONLY posts issue comments (no top-level
#     review, no inline thread) is no longer undercounted, AND the metric fields
#     it emits are the renamed ones summarize.py reads.
# Pure stdlib Python, no network, no gh: extract.py import is side-effect-free.

SCRIPTS="$BATS_TEST_DIRNAME/../../skills/review-audit/scripts"

@test "stratified_sample: N picks span history (endpoints kept, exact count)" {
  run python3 - "$SCRIPTS" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from audit_config import stratified_sample
assert stratified_sample([1, 2, 3], 5) == [1, 2, 3]      # n>=len -> unchanged
assert stratified_sample([1, 2, 3], 0) == [1, 2, 3]      # n<=0  -> unchanged
assert stratified_sample(list(range(100)), 1) == [50]    # n==1  -> middle
s = stratified_sample(list(range(100)), 7)
assert len(s) == 7 and s[0] == 0 and s[-1] == 99, s      # endpoints + count
assert s == sorted(s) and len(set(s)) == 7, s            # strictly increasing
print('ok')
PY
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "extract --sample: fails fast when slim/ holds PRs outside the sample" {
  # Pre-populate the output dir with a slim file for a PR that won't be in the
  # sample, then sample 2 of an explicit 5-PR target set (so no network: the
  # guard fires before the fetch loop). The stray file must abort the run.
  local dir="$BATS_TEST_TMPDIR/audit"
  mkdir -p "$dir/slim"
  echo '{}' >"$dir/slim/pr-99.json"
  run python3 "$SCRIPTS/extract.py" --repo o/r --dir "$dir" --sample 2 1 2 3 4 5
  [ "$status" -ne 0 ]
  [[ "$output" == *"outside the sampled set"* ]]
}

@test "metrics_row: issue-comment-only feedback is counted (not invisible)" {
  run python3 - "$SCRIPTS" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import extract
# A PR whose ONLY bot feedback is an issue-level comment at 01:00 — no review,
# no inline thread. created 00:00, merged 02:00, a commit lands at 01:30.
slim = dict(number=7, title='t', author='a', url='u',
            created_at='2026-01-01T00:00:00Z', ready_at=None,
            merged_at='2026-01-01T02:00:00Z',
            additions=1, deletions=0, changed_files=1, n_commits=2,
            commit_dates=['2026-01-01T00:30:00Z', '2026-01-01T01:30:00Z'],
            reviewers=['coderabbit'], reviews=[], threads=[],
            issue_comments=[{'reviewer': 'coderabbit',
                             'ts': '2026-01-01T01:00:00Z', 'body': 'x'}])
row = extract.metrics_row(slim)
# first feedback = the issue comment (60 min after creation); one commit (01:30)
# lands after it. Keying off reviews[] alone (the old behavior) gave None / 0.
assert row['first_feedback_wait_min'] == 60.0, row['first_feedback_wait_min']
assert row['commits_after_first_feedback'] == 1, row['commits_after_first_feedback']
print('ok')
PY
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "metrics_row: emits the renamed fields, not the old first_review names" {
  run python3 - "$SCRIPTS" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import extract
slim = dict(number=1, title='t', author='a', url='u',
            created_at='2026-01-01T00:00:00Z', ready_at=None,
            merged_at='2026-01-01T01:00:00Z',
            additions=0, deletions=0, changed_files=0, n_commits=0,
            commit_dates=[], reviewers=[], reviews=[], threads=[],
            issue_comments=[])
row = extract.metrics_row(slim)
assert 'first_feedback_wait_min' in row
assert 'commits_after_first_feedback' in row
assert 'first_review_wait_min' not in row          # renamed; summarize.py reads new
assert 'commits_after_first_review' not in row
print('ok')
PY
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}
