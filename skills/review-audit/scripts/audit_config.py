#!/usr/bin/env python3
"""Shared config + helpers for the review-audit pipeline (repo-agnostic).

This is the ONE place that knows about specific review bots and how to
de-noise their markdown. It is the main maintenance burden: Copilot /
CodeRabbit / Gemini change their output formats over time, so the login
map and the denoise regexes here ROT. When `summarize.py` starts showing
a reviewer with zero findings that you know reviewed, or the slim files
still carry collapsible `<details>` walls, refresh this file (and add a
fixture to `tests/` so the gate flags the next drift).

Everything repo-specific (owner/name, output dir, the PR list, era
boundaries) is passed in by the caller — nothing here is hardcoded to a
single repo. Configure via:

  * CLI: most scripts take `--repo OWNER/NAME` and `--dir PATH`.
  * env: `REVIEW_AUDIT_REPO=owner/name`, `REVIEW_AUDIT_DIR=/path`.
  * a JSON overlay file (`--config audit.json`) to override BOTS /
    denoise without editing this module — see `load_overlay`.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --- bot login -> canonical reviewer name -----------------------------------
# Maps every login a bot can post under (top-level review author AND the
# inline-comment display login, with and without the `[bot]` suffix) to a
# single canonical name. Add a new bot by adding its logins here.
#
# NB: coding agents (copilot-swe-agent, claude[bot]) are PR authors / fixers,
# NOT reviewers — deliberately absent so they are never counted as reviews.
BOTS = {
    "copilot-pull-request-reviewer": "copilot",
    "copilot-pull-request-reviewer[bot]": "copilot",
    "copilot": "copilot",
    "gemini-code-assist": "gemini",
    "gemini-code-assist[bot]": "gemini",
    "coderabbitai": "coderabbit",
    "coderabbitai[bot]": "coderabbit",
}


def role(login, bots=None):
    """Canonical reviewer name for a login, or None if not a tracked bot."""
    return (bots or BOTS).get((login or "").lower(), None)


def thread_actioned(t):
    """Mechanical 'the author did something about this thread' signal.

    Counts the REST-derived signals (author replied / position dropped) AND
    the GraphQL flags enrich.py adds (`resolved` / `gh_outdated`). All reads
    use `.get()` so a stage-1 (pre-enrich) slim file stays valid — it just
    sees fewer signals. Used by both extract.py's metrics row and
    summarize.py so the two never disagree on action-rate.
    """
    return bool(t.get("author_replied") or t.get("outdated")
                or t.get("resolved") or t.get("gh_outdated"))


def first_feedback_ts(slim):
    """Earliest moment ANY tracked bot left feedback on the PR, or None.

    The 'first feedback' clock for latency + churn metrics. It spans ALL three
    bot channels — a top-level review (reviews[].submitted_at), an inline
    thread (threads[].created_at), and an issue-level comment
    (issue_comments[].ts) — because some bots leave feedback ONLY as issue
    comments (a summary-only reviewer) and never submit a top-level review.
    Keying off reviews[] alone (the old behavior) made such a bot's first
    feedback vanish, undercounting first_feedback_wait_min /
    commits_after_first_feedback. Timestamps are same-format ISO-8601 UTC, so
    the lexicographic min is the chronological earliest.
    """
    stamps = ([r.get("submitted_at") for r in slim.get("reviews") or []]
              + [t.get("created_at") for t in slim.get("threads") or []]
              + [c.get("ts") for c in slim.get("issue_comments") or []])
    stamps = [s for s in stamps if s]
    return min(stamps) if stamps else None


def stratified_sample(targets, n):
    """Pick `n` PRs evenly spaced across the sorted `targets` list.

    For very large repos the full judging tier is ~linear in PRs (~1.6M tokens
    / ~120 PRs), so an audit may need a subset. The sample is spread evenly
    across the PR-number timeline, which is a proxy for the review-config ERA:
    reviewer changes are chronological (none -> one bot -> +a second), so even
    spacing keeps every era represented instead of over-sampling the most
    recent one. Deterministic (reproducible audits) and always includes the
    first and last PR so both history extremes are kept. `targets` is assumed
    sorted ascending; returns it unchanged when n<=0 or n>=len(targets).
    """
    targets = list(targets)
    if n <= 0 or n >= len(targets):
        return targets
    if n == 1:
        return [targets[len(targets) // 2]]
    # Even spacing, endpoints inclusive: step > 1 here (n < len), so the rounded
    # indices are strictly increasing — no collisions, exactly n picks.
    step = (len(targets) - 1) / (n - 1)
    return [targets[round(i * step)] for i in range(n)]


# --- bot markdown de-noising ------------------------------------------------
# Strips CodeRabbit's collapsible <details> walls, badges, boilerplate, etc.
# (~38x shrink on CodeRabbit) while KEEPING the outcome markers the judges
# need (e.g. "Addressed in <sha>"). These patterns rot — see module docstring.
DETAILS = re.compile(r"<details>.*?</details>", re.S | re.I)
HTMLCOMMENT = re.compile(r"<!--.*?-->", re.S)
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
BADGE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
# NB: `.` is DOTALL here (?s). The two intentionally multi-line alternatives
# (<summary>...</summary>, "In the recent ... coderabbit") stay non-greedy so
# they match a bounded span. EVERY OTHER alternative is line-oriented and MUST
# use `[^\n]*`, not `.*`: a greedy DOTALL `.*` runs to end-of-string and would
# swallow the "Addressed in <sha>" outcome markers the pipeline depends on
# keeping. Under-removing a stray tip line is fine; eating outcome markers is not.
BOILER = re.compile(
    r"(?is)(<summary>.*?</summary>|"
    r"\bIn the recent.*?coderabbit\b|"
    r"Tip\s*<[^\n]*|"
    r"@coderabbitai (ignore|pause|resume|full review)|"
    r"You can (disable|trigger)[^\n]*review[^\n]*|"
    r"This (review|comment) was generated[^\n]*)")
HR = re.compile(r"\n-{3,}\n")
MULTINL = re.compile(r"\n{3,}")


def clean(md):
    """De-noise a bot markdown blob into compact agent input."""
    if not md:
        return ""
    md = DETAILS.sub("", md)
    md = HTMLCOMMENT.sub("", md)
    md = BADGE.sub("", md)
    md = IMG.sub("", md)
    md = BOILER.sub("", md)
    md = HR.sub("\n", md)
    md = MULTINL.sub("\n\n", md)
    return md.strip()


def load_overlay(path):
    """Merge a JSON overlay onto the module config (BOTS / denoise tweaks).

    The overlay is for adding a bot or a boilerplate pattern in a specific
    repo without forking this file. Shape:
      {"bots": {"login": "name", ...},
       "boiler_extra": ["regex", ...]}
    """
    global BOILER
    if not path:
        return BOTS
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    bots = dict(BOTS)
    bots.update({k.lower(): v for k, v in data.get("bots", {}).items()})
    for pat in data.get("boiler_extra", []):
        BOILER = re.compile(BOILER.pattern + "|" + pat, BOILER.flags)
    return bots


# --- repo / dir resolution --------------------------------------------------
def resolve_repo(arg=None):
    """OWNER, REPO from --repo arg, env, or the current git remote."""
    spec = arg or os.environ.get("REVIEW_AUDIT_REPO")
    if not spec:
        try:
            out = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner",
                 "--jq", ".nameWithOwner"],
                capture_output=True, text=True, check=True)
            spec = out.stdout.strip()
        except Exception:
            pass
    if not spec or "/" not in spec:
        sys.exit("review-audit: need a repo — pass --repo OWNER/NAME, set "
                 "REVIEW_AUDIT_REPO, or run inside the repo's checkout.")
    owner, name = spec.split("/", 1)
    return owner, name


def resolve_dir(arg=None):
    """Output dir (raw/ slim/ verdicts/ live here). Default: ./analysis/reviews."""
    d = arg or os.environ.get("REVIEW_AUDIT_DIR") or "analysis/reviews"
    p = Path(d).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- gh REST helper (shared) ------------------------------------------------
def gh(path, paginate=False):
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json", path]
    if paginate:
        cmd += ["--paginate"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {out.stderr.strip()[:300]}")
    txt = out.stdout.strip()
    if not txt:
        return []
    if paginate:
        merged = []
        dec = json.JSONDecoder()
        i = 0
        while i < len(txt):
            while i < len(txt) and txt[i].isspace():
                i += 1
            if i >= len(txt):
                break
            obj, j = dec.raw_decode(txt, i)
            merged += obj if isinstance(obj, list) else [obj]
            i = j
        return merged
    return json.loads(txt)


def rate_resource(resource):
    """(remaining, reset) for a rate_limit resource ('core' / 'graphql').

    Surfaces gh failures (missing/auth/network) with an actionable message
    instead of crashing on a JSON decode of empty/error stdout.
    """
    out = subprocess.run(["gh", "api", "rate_limit"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        err = (out.stderr or "").strip()[:300] or "no output"
        sys.exit(f"review-audit: `gh api rate_limit` failed ({err}). "
                 "Check `gh auth status` and network connectivity.")
    r = json.loads(out.stdout)
    res = r["resources"][resource]
    return res["remaining"], res["reset"]


def _selftest():
    """Minimal denoise guard: the FULL fixture suite is #740, but lock the one
    invariant that matters most — clean() must strip boilerplate WITHOUT eating
    the `Addressed in <sha>` outcome markers (the whole reason we keep them).
    Run as: `python3 audit_config.py`.
    """
    # A CodeRabbit-style blob: a Tip line, then the outcome marker AFTER it.
    blob = (
        "Tip <kbd>Ctrl</kbd> to do a thing you can ignore\n"
        "You can disable this kind of review in settings\n"
        "✅ Addressed in abc1234\n"
        "This comment was generated by a bot\n"
    )
    out = clean(blob)
    assert "Addressed in abc1234" in out, (
        f"clean() ATE the outcome marker — greedy boilerplate regex: {out!r}")
    assert "Tip <kbd>" not in out, f"clean() left the Tip line: {out!r}"
    assert "disable this kind of review" not in out, (
        f"clean() left the disable-review line: {out!r}")
    # The marker must survive even when boilerplate sits on BOTH sides of it.
    assert "Addressed in deadbee" in clean(
        "This review was generated\n✅ Addressed in deadbee\nTip <x>")

    # first_feedback_ts (#740): earliest across reviews / threads / issue
    # comments — an issue-comment-only bot must NOT register as silent.
    assert first_feedback_ts({}) is None
    assert first_feedback_ts(
        {"issue_comments": [{"ts": "2026-01-02T00:00:00Z"}]}
    ) == "2026-01-02T00:00:00Z", "issue-comment-only feedback went missing"
    assert first_feedback_ts({
        "reviews": [{"submitted_at": "2026-01-03T00:00:00Z"}],
        "threads": [{"created_at": "2026-01-02T00:00:00Z"}],
        "issue_comments": [{"ts": "2026-01-01T00:00:00Z"}],
    }) == "2026-01-01T00:00:00Z", "did not take the earliest channel"

    # stratified_sample (#740): even, deterministic, endpoints kept, exact count.
    assert stratified_sample([1, 2, 3], 5) == [1, 2, 3]   # n>=len -> unchanged
    assert stratified_sample([1, 2, 3], 0) == [1, 2, 3]   # n<=0  -> unchanged
    assert stratified_sample(list(range(100)), 1) == [50]  # n==1  -> middle
    s = stratified_sample(list(range(100)), 7)
    assert len(s) == 7 and s[0] == 0 and s[-1] == 99, f"endpoints/count: {s}"
    assert s == sorted(s) and len(set(s)) == 7, f"not strictly increasing: {s}"

    print("audit_config selftest OK: denoise + first_feedback_ts + "
          "stratified_sample")


if __name__ == "__main__":
    _selftest()
