#!/usr/bin/env python3
"""Stage 2 — enrich slim threads with true GraphQL reviewThread flags.

Adds isResolved / isOutdated / resolvedBy per thread (REST can't give
resolution state), matched back by root-comment databaseId. Bounded,
budget-aware, idempotent. Repo-agnostic.

Usage:
  enrich.py [--repo OWNER/NAME] [--dir PATH]
"""
import argparse
import json
import subprocess
import time

from audit_config import rate_resource, resolve_dir, resolve_repo

Q_TMPL = """query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){
 reviewThreads(first:100){nodes{isResolved isOutdated resolvedBy{login}
   comments(first:1){nodes{databaseId}}}}}}}"""


def gql(query, n):
    out = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}", "-F", f"n={n}"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    data = json.loads(out.stdout)
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"])[:200])
    nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    m = {}
    for t in nodes:
        cs = t["comments"]["nodes"]
        if not cs:
            continue
        m[cs[0]["databaseId"]] = dict(
            resolved=t["isResolved"], gh_outdated=t["isOutdated"],
            resolved_by=(t["resolvedBy"] or {}).get("login"))
    return m


def gql_remaining():
    return rate_resource("graphql")


def wait_for_budget(minimum=200):
    rem, reset = gql_remaining()
    if rem < minimum:
        wait = max(0, reset - int(time.time())) + 5
        print(f"graphql budget {rem} < {minimum}; waiting {wait}s", flush=True)
        time.sleep(wait)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo")
    ap.add_argument("--dir")
    a = ap.parse_args()
    owner, repo = resolve_repo(a.repo)
    out = resolve_dir(a.dir)
    slim = out / "slim"
    query = Q_TMPL % (owner, repo)

    targets = sorted(
        int(p.stem.split("-")[1]) for p in slim.glob("pr-*.json")
        if json.loads(p.read_text()).get("threads"))
    print(f"enriching {len(targets)} PRs with threads", flush=True)
    wait_for_budget()
    done = enriched = 0
    for i, n in enumerate(targets):
        if i % 25 == 0:
            wait_for_budget()
        try:
            flags = gql(query, n)
        except RuntimeError as e:
            print(f"#{n} ERR {e}", flush=True)
            time.sleep(1)
            continue
        f = slim / f"pr-{n}.json"
        rec = json.loads(f.read_text())
        hit = 0
        for t in rec["threads"]:
            fl = flags.get(t.get("root_id"))
            if fl:
                t.update(fl)
                hit += 1
        f.write_text(json.dumps(rec, indent=1))
        done += 1
        enriched += hit
        time.sleep(0.15)
    print(f"done: {done} PRs, {enriched} threads got resolved/outdated flags",
          flush=True)


if __name__ == "__main__":
    main()
