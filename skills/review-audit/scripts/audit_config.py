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


# --- bot markdown de-noising ------------------------------------------------
# Strips CodeRabbit's collapsible <details> walls, badges, boilerplate, etc.
# (~38x shrink on CodeRabbit) while KEEPING the outcome markers the judges
# need (e.g. "Addressed in <sha>"). These patterns rot — see module docstring.
DETAILS = re.compile(r"<details>.*?</details>", re.S | re.I)
HTMLCOMMENT = re.compile(r"<!--.*?-->", re.S)
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
BADGE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
BOILER = re.compile(
    r"(?is)(<summary>.*?</summary>|"
    r"\bIn the recent.*?coderabbit\b|"
    r"Tip\s*<.*|"
    r"@coderabbitai (ignore|pause|resume|full review)|"
    r"You can (disable|trigger).*review.*|"
    r"This (review|comment) was generated.*)")
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
    data = json.loads(open(path).read())
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
    import pathlib
    d = arg or os.environ.get("REVIEW_AUDIT_DIR") or "analysis/reviews"
    p = pathlib.Path(d).resolve()
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
