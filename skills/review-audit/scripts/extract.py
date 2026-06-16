#!/usr/bin/env python3
"""Stage 1 — review-audit data extraction (REST, resumable, rate-aware).

Repo-agnostic. For every merged PR it writes, under the output dir:
  raw/pr-NNN.json    full-fidelity archive (never fed to agents)
  slim/pr-NNN.json   de-noised, compact (the agent's input)
and rewrites metrics.jsonl (mechanical signals, one row per PR).

Uses `gh api` for auth. Bounded, sleeps between calls, pauses if core
budget is low. Resumable: PRs whose slim file already exists are skipped.

Usage:
  extract.py [--repo OWNER/NAME] [--dir PATH] [--config audit.json] [PR ...]
  (no PR args -> all merged PRs)
"""
import argparse
import json
import sys
import time
from datetime import datetime

from audit_config import (
    clean,
    gh,
    load_overlay,
    rate_resource,
    resolve_dir,
    resolve_repo,
    role,
    thread_actioned,
)

SLEEP = 0.25


def rate_remaining():
    return rate_resource("core")


def fetch_pr(owner, repo, n):
    base = f"/repos/{owner}/{repo}"
    pr = gh(f"{base}/pulls/{n}")
    commits = gh(f"{base}/pulls/{n}/commits", paginate=True)
    reviews = gh(f"{base}/pulls/{n}/reviews", paginate=True)
    inline = gh(f"{base}/pulls/{n}/comments", paginate=True)
    issue_comments = gh(f"{base}/issues/{n}/comments", paginate=True)
    try:
        timeline = gh(f"{base}/issues/{n}/timeline", paginate=True)
    except RuntimeError:
        timeline = []
    return dict(pr=pr, commits=commits, reviews=reviews, inline=inline,
                issue_comments=issue_comments, timeline=timeline)


def build_slim(n, raw, bots):
    pr = raw["pr"]
    author = (pr.get("user") or {}).get("login", "")
    created = pr.get("created_at")
    merged = pr.get("merged_at")

    commit_dates = sorted(
        c["commit"]["committer"]["date"] for c in raw["commits"]
        if c.get("commit"))

    ready_at = None
    for ev in raw["timeline"]:
        if ev.get("event") == "ready_for_review" and not ready_at:
            ready_at = ev.get("created_at")
            break

    reviews_by_bot = {}
    for rv in raw["reviews"]:
        rl = role((rv.get("user") or {}).get("login"), bots)
        if not rl:
            continue
        reviews_by_bot.setdefault(rl, []).append(rv)
    for rl in reviews_by_bot:
        reviews_by_bot[rl].sort(key=lambda r: r.get("submitted_at") or "")

    review_id_round = {}
    slim_reviews = []
    for rl, rvs in reviews_by_bot.items():
        for idx, rv in enumerate(rvs, 1):
            review_id_round[rv["id"]] = (rl, idx)
            slim_reviews.append(dict(
                reviewer=rl, round=idx, state=rv.get("state"),
                submitted_at=rv.get("submitted_at"),
                commit_id=(rv.get("commit_id") or "")[:10],
                body=clean(rv.get("body", ""))))
    slim_reviews.sort(key=lambda r: r["submitted_at"] or "")

    by_id = {c["id"]: c for c in raw["inline"]}
    threads = {}
    for c in raw["inline"]:
        rid = c.get("in_reply_to_id")
        root = c
        seen = set()
        while rid and rid in by_id and rid not in seen:
            seen.add(rid)
            root = by_id[rid]
            rid = root.get("in_reply_to_id")
        threads.setdefault(root["id"], []).append(c)

    slim_threads = []
    for comments in threads.values():
        comments.sort(key=lambda c: c.get("created_at") or "")
        root = comments[0]
        rl = role((root.get("user") or {}).get("login"), bots)
        if not rl:
            continue
        prr = root.get("pull_request_review_id")
        rnd = review_id_round.get(prr, (rl, None))[1]
        outdated = root.get("position") is None
        author_replied = any(
            (c.get("user") or {}).get("login") == author for c in comments[1:])
        bot_followup_rounds = sorted({
            review_id_round.get(c.get("pull_request_review_id"), (None, None))[1]
            for c in comments
            if role((c.get("user") or {}).get("login"), bots) == rl
            and review_id_round.get(c.get("pull_request_review_id"))
        } - {None})
        code_changed_after = any(d > (root.get("created_at") or "")
                                 for d in commit_dates)
        slim_threads.append(dict(
            root_id=root["id"],
            reviewer=rl, round=rnd, path=root.get("path"),
            line=root.get("line") or root.get("original_line"),
            created_at=root.get("created_at"),
            outdated=outdated, author_replied=author_replied,
            code_changed_after=code_changed_after,
            n_replies=len(comments) - 1,
            bot_followup_rounds=bot_followup_rounds,
            comments=[dict(
                who=("author" if (c.get("user") or {}).get("login") == author
                     else role((c.get("user") or {}).get("login"), bots)
                     or "human"),
                ts=c.get("created_at"), body=clean(c.get("body", "")))
                for c in comments]))

    slim_issue = []
    for c in raw["issue_comments"]:
        rl = role((c.get("user") or {}).get("login"), bots)
        if not rl:
            continue
        slim_issue.append(dict(reviewer=rl, ts=c.get("created_at"),
                               body=clean(c.get("body", ""))))

    return dict(
        number=n, title=pr.get("title"), author=author,
        url=pr.get("html_url"),
        created_at=created, ready_at=ready_at, merged_at=merged,
        additions=pr.get("additions"), deletions=pr.get("deletions"),
        changed_files=pr.get("changed_files"),
        n_commits=len(raw["commits"]), commit_dates=commit_dates,
        body=clean(pr.get("body", "")),
        reviewers=sorted(reviews_by_bot.keys()
                         | {t["reviewer"] for t in slim_threads}
                         | {x["reviewer"] for x in slim_issue}),
        reviews=slim_reviews, threads=slim_threads, issue_comments=slim_issue)


def metrics_row(slim):
    def iso(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    created, ready, merged = (iso(slim["created_at"]), iso(slim["ready_at"]),
                              iso(slim["merged_at"]))
    first_review = iso(slim["reviews"][0]["submitted_at"]) if slim["reviews"] else None
    clock = ready or created
    per_reviewer = {}
    for r in slim["reviewers"]:
        rounds = max([x["round"] for x in slim["reviews"]
                      if x["reviewer"] == r and x["round"]] + [0])
        n_threads = sum(1 for t in slim["threads"] if t["reviewer"] == r)
        n_actioned = sum(1 for t in slim["threads"] if t["reviewer"] == r
                         and thread_actioned(t))
        per_reviewer[r] = dict(rounds=rounds, threads=n_threads,
                               actioned=n_actioned)
    return dict(
        number=slim["number"], author=slim["author"],
        created_at=slim["created_at"], merged_at=slim["merged_at"],
        reviewers=slim["reviewers"],
        ttm_min=round((merged - created).total_seconds() / 60, 1)
        if created and merged else None,
        ready_to_merge_min=round((merged - ready).total_seconds() / 60, 1)
        if ready and merged else None,
        first_review_wait_min=round((first_review - clock).total_seconds() / 60, 1)
        if first_review and clock else None,
        n_commits=slim["n_commits"],
        commits_after_first_review=sum(
            1 for d in slim["commit_dates"]
            if first_review and iso(d) and iso(d) > first_review),
        changed_files=slim["changed_files"],
        additions=slim["additions"], deletions=slim["deletions"],
        total_threads=len(slim["threads"]),
        per_reviewer=per_reviewer)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="OWNER/NAME (default: env or current repo)")
    ap.add_argument("--dir", help="output dir (default: ./analysis/reviews)")
    ap.add_argument("--config", help="JSON overlay for BOTS / denoise")
    ap.add_argument("prs", nargs="*", type=int, help="specific PR numbers")
    a = ap.parse_args()

    owner, repo = resolve_repo(a.repo)
    out = resolve_dir(a.dir)
    bots = load_overlay(a.config)
    raw_dir = out / "raw"
    slim_dir = out / "slim"
    raw_dir.mkdir(exist_ok=True)
    slim_dir.mkdir(exist_ok=True)
    metrics = out / "metrics.jsonl"

    targets = a.prs or None
    if targets is None:
        prs = gh(f"/repos/{owner}/{repo}/pulls?state=closed&per_page=100",
                 paginate=True)
        targets = sorted(p["number"] for p in prs if p.get("merged_at"))
    if not targets:
        sys.exit("no merged PRs found")
    print(f"{owner}/{repo}: {len(targets)} PRs ({targets[0]}..{targets[-1]})",
          flush=True)

    done = {int(p.stem.split("-")[1]) for p in slim_dir.glob("pr-*.json")}
    for i, n in enumerate(targets):
        if n in done:
            continue
        if i % 15 == 0:
            rem, reset = rate_remaining()
            if rem < 300:
                wait = max(0, reset - int(time.time())) + 5
                print(f"core budget low ({rem}); sleeping {wait}s", flush=True)
                time.sleep(wait)
        try:
            raw = fetch_pr(owner, repo, n)
        except RuntimeError as e:
            print(f"#{n} ERROR {e}", flush=True)
            continue
        (raw_dir / f"pr-{n}.json").write_text(json.dumps(raw))
        slim = build_slim(n, raw, bots)
        (slim_dir / f"pr-{n}.json").write_text(json.dumps(slim, indent=1))
        row = metrics_row(slim)
        rv = ",".join(slim["reviewers"]) or "-"
        print(f"#{n:4d} files={slim['changed_files']} reviewers={rv} "
              f"threads={len(slim['threads'])} ttm={row['ttm_min']}m",
              flush=True)
        time.sleep(SLEEP)

    allrows = []
    for p in sorted(slim_dir.glob("pr-*.json"),
                    key=lambda x: int(x.stem.split("-")[1])):
        allrows.append(metrics_row(json.loads(p.read_text())))
    metrics.write_text("\n".join(json.dumps(r) for r in allrows))
    print(f"\nwrote {len(allrows)} metric rows -> {metrics}", flush=True)


if __name__ == "__main__":
    main()
