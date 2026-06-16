#!/usr/bin/env python3
"""Stage 4 — mechanical headline metrics with ZERO agents (the cheap tier).

Latency by era, per-reviewer volume + action-rate, the rounds-vs-PR-number
curve that dates re-request onset, and review-induced churn. Repo-agnostic:
eras are DERIVED from the reviewer sets actually present (no hardcoded bot
names), so it works for any repo's history.

Usage:
  summarize.py [--dir PATH] [--rounds-of NAME]
    --rounds-of  reviewer whose round-vs-PR curve to print (default: the
                 reviewer with the most multi-round PRs)
"""
import argparse
import collections
import json
import statistics as st

from audit_config import resolve_dir


def bucket(r):
    """Era label = the set of reviewers on the PR (derived, not hardcoded)."""
    rs = sorted(set(r["reviewers"]))
    return "none" if not rs else "+".join(rs)


def f(v):
    return "-" if v is None else v


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    # inclusive rank: q=0 -> first, q=1 -> last; p90 of 10 items is index 8.
    return round(xs[int(q * (len(xs) - 1))], 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir")
    ap.add_argument("--rounds-of", help="reviewer for the rounds-vs-PR curve")
    a = ap.parse_args()
    out = resolve_dir(a.dir)
    rows = [json.loads(line)
            for line in (out / "metrics.jsonl").read_text().splitlines()
            if line.strip()]

    groups = collections.defaultdict(list)
    for r in rows:
        groups[bucket(r)].append(r)

    print("=" * 78)
    print("LATENCY BY ERA (minutes)   n | ttm med | ttm p90 | "
          "ready->merge med | 1st-review-wait med")
    print("=" * 78)
    for k in sorted(groups):
        g = groups[k]
        print(f"{k:18s} n={len(g):3d} | "
              f"ttm {str(f(med([x['ttm_min'] for x in g]))):>7} | "
              f"p90 {str(f(pctile([x['ttm_min'] for x in g], .9))):>7} | "
              f"r->m {str(f(med([x['ready_to_merge_min'] for x in g]))):>7} | "
              f"wait {str(f(med([x['first_review_wait_min'] for x in g]))):>6}")

    print("\n" + "=" * 78)
    print("PER-REVIEWER VOLUME & ACTION RATE")
    print("=" * 78)
    rev = collections.defaultdict(
        lambda: dict(prs=0, threads=0, actioned=0, rounds=[]))
    for r in rows:
        for name, d in r["per_reviewer"].items():
            rev[name]["prs"] += 1
            rev[name]["threads"] += d["threads"]
            rev[name]["actioned"] += d["actioned"]
            rev[name]["rounds"].append(d["rounds"])
    for name, d in sorted(rev.items()):
        ar = f"{100*d['actioned']/d['threads']:.0f}%" if d["threads"] else "n/a"
        print(f"{name:11s} PRs={d['prs']:3d} threads={d['threads']:4d} "
              f"({d['threads']/d['prs']:.1f}/PR) action-rate={ar:>4} "
              f"max-rounds={max(d['rounds'])} med-rounds={f(med(d['rounds']))}")

    # rounds-vs-PR-number curve for one reviewer (re-request onset)
    target = a.rounds_of
    if not target and rev:
        target = max(rev, key=lambda nm: sum(1 for x in rev[nm]["rounds"]
                                             if x > 1))
    if target:
        print("\n" + "=" * 78)
        print(f"{target.upper()} ROUNDS vs PR NUMBER (onset of re-request-on-push)")
        print("=" * 78)
        curve = sorted(
            ((r["number"], r["per_reviewer"].get(target, {}).get("rounds", 0))
             for r in rows if target in r["reviewers"]), key=lambda x: x[0])
        win = collections.defaultdict(list)
        for n, rounds in curve:
            win[(n // 20) * 20].append(rounds)
        for lo in sorted(win):
            rs = win[lo]
            print(f"PR #{lo:4d}-{lo+19:4d}: {target} PRs={len(rs):2d} "
                  f"avg-rounds={sum(rs)/len(rs):.2f} max={max(rs)} "
                  f"multi-round={sum(1 for x in rs if x > 1)}")

    print("\n" + "=" * 78)
    print("CHURN: commits landing AFTER first review (review-induced rework)")
    print("=" * 78)
    for k in sorted(groups):
        g = groups[k]
        vals = [x["commits_after_first_review"] for x in g
                if x.get("commits_after_first_review") is not None]
        share = sum(1 for v in vals if v > 0) / len(vals) if vals else 0
        print(f"{k:18s} median-commits-after={f(med(vals))} "
              f"share-with-rework={share*100:.0f}%")


if __name__ == "__main__":
    main()
