#!/usr/bin/env python3
"""Stage 7 — answer Q4 precisely: are re-requested (round>=2) reviews
CONVERGING (commenting on code the triggering push changed) or RE-SCANNING
(surfacing fresh issues on code already present in round 1)?

For each round>=2 inline thread of the chosen reviewer, decide whether the
file it targets was changed by commits landing between the previous review
round and this one. File-level granularity. Needs per-commit file lists
(fetched + cached). Repo-agnostic; PR list is DERIVED, not hardcoded.

Run AFTER the stage-5 Workflow (uses verdicts/ for per-finding value, but
degrades gracefully if absent).

Usage:
  converge.py [--repo OWNER/NAME] [--dir PATH] [--reviewer NAME]
    --reviewer  which reviewer's re-request behaviour to classify
                (default: the one with the most multi-round PRs)
"""
import argparse
import collections
import json
import statistics as st
import subprocess

from audit_config import resolve_dir, resolve_repo


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo")
    ap.add_argument("--dir")
    ap.add_argument("--reviewer", help="reviewer to classify (default: auto)")
    a = ap.parse_args()
    owner, repo = resolve_repo(a.repo)
    out = resolve_dir(a.dir)
    raw_dir, slim_dir = out / "raw", out / "slim"
    cache_path = out / "_commit_files.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    verd = {}
    for f in (out / "verdicts").glob("pr-*.json"):
        try:
            d = json.loads(f.read_text())
            verd[d["number"]] = d
        except Exception:
            pass

    # Pick the reviewer (default: most multi-round PRs) and derive its PR list.
    multi = collections.Counter()
    for p in slim_dir.glob("pr-*.json"):
        slim = json.loads(p.read_text())
        rounds = collections.Counter(
            t["reviewer"] for t in slim.get("threads", []) if (t.get("round") or 1) >= 2)
        for rv in rounds:
            multi[rv] += 1
    reviewer = a.reviewer or (multi.most_common(1)[0][0] if multi else None)
    if not reviewer:
        print("no reviewer has round>=2 threads — nothing to classify")
        return
    def eff_round(t):
        """Highest round in which this reviewer commented on the thread.

        A thread's `round` is the round its ROOT was opened in; the same bot
        may follow up in later rounds (`bot_followup_rounds`). The reviewer's
        round>=2 ACTIVITY on a thread is captured by the max of the two — a
        thread opened in round 1 that the bot revisits in round 2 IS a
        round>=2 event, which `round` alone misses.
        """
        rounds = [r for r in (t.get("bot_followup_rounds") or []) if r]
        rounds.append(t.get("round") or 1)
        return max(rounds)

    prs = sorted(
        int(p.stem.split("-")[1]) for p in slim_dir.glob("pr-*.json")
        if any(t["reviewer"] == reviewer and eff_round(t) >= 2
               for t in json.loads(p.read_text()).get("threads", [])))
    print(f"reviewer={reviewer}: {len(prs)} PRs with round>=2 activity\n")

    def files_for(sha):
        if sha in cache:
            return cache[sha]
        o = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/commits/{sha}",
             "--jq", "[.files[].filename]"], capture_output=True, text=True)
        f = json.loads(o.stdout) if o.returncode == 0 and o.stdout.strip() else []
        cache[sha] = f
        return f

    def vfind(pr, path):
        for fnd in verd.get(pr, {}).get("findings", []):
            if (fnd["reviewer"] == reviewer and fnd.get("path") == path
                    and (fnd.get("round") or 1) >= 2):
                return fnd
        return None

    buckets = collections.Counter()
    rows = []
    for n in prs:
        raw = json.loads((raw_dir / f"pr-{n}.json").read_text())
        commits = sorted(
            ((c["sha"], c["commit"]["committer"]["date"]) for c in raw["commits"]),
            key=lambda x: x[1])
        slim = json.loads((slim_dir / f"pr-{n}.json").read_text())
        # Round timing comes ONLY from the canonical per-reviewer rounds the
        # slim files already carry (extract.py groups each bot's reviews and
        # numbers them by submit time). Map round -> submit timestamp; no
        # raw-login heuristic, so a window can't be misaligned by a login that
        # merely shares a prefix or by canonical names that don't.
        round_at = {
            rv["round"]: rv["submitted_at"]
            for rv in slim["reviews"]
            if rv["reviewer"] == reviewer and rv.get("round")
            and rv.get("submitted_at")
        }
        if len(round_at) < 2:
            continue
        first_at = round_at[min(round_at)]
        for t in slim["threads"]:
            if t["reviewer"] != reviewer:
                continue
            r = eff_round(t)          # the reviewer's latest activity round
            if r < 2:
                continue
            opened = t.get("round") or 1
            fnd = vfind(n, t.get("path"))
            val = fnd["value"] if fnd else None
            # A thread OPENED in an earlier round that this reviewer revisits
            # in round r>=2 is a follow-up on its own earlier thread — it is
            # reacting to the author's response, hence CONVERGING by construction.
            if opened < r:
                cls = "followup"
            else:
                # Brand-new thread opened at round r>=2: classify by whether
                # the file it targets was changed in the push that triggered
                # this round (vs. a re-scan of code unchanged since round 1).
                # Window = (prev round's submit, this round's submit]; look the
                # bounds up by round number, falling back to the nearest known
                # earlier round if r-1 wasn't recorded.
                this_t = round_at.get(r) or round_at[max(round_at)]
                earlier = [round_at[k] for k in round_at if k < r]
                prev_t = max(earlier) if earlier else ""
                changed = set()
                for sha, date in commits:
                    if prev_t < date <= this_t:
                        changed |= set(files_for(sha))
                changed_any = set()
                for sha, date in commits:
                    if date > first_at:
                        changed_any |= set(files_for(sha))
                if t.get("path") in changed:
                    cls = "on_changed"
                elif t.get("path") in changed_any:
                    cls = "on_touched_file"
                else:
                    cls = "on_preexisting"
            buckets[cls] += 1
            rows.append((n, r, cls, t.get("path"), t.get("resolved"), val,
                         (fnd.get("gist", "") if fnd else "")))

    cache_path.write_text(json.dumps(cache))

    print("=" * 90)
    print(f"ROUND>=2 {reviewer.upper()} INLINE THREADS — convergence classification")
    print("=" * 90)
    tot = sum(buckets.values())
    if not tot:
        print("  (none)")
        return
    labels = {
        "followup": "follow-up on its own round-1 thread — CONVERGING",
        "on_changed": "new thread on a file the triggering push changed — CONVERGING",
        "on_touched_file": "file touched since r1 but not in this push — partial",
        "on_preexisting": "new thread on code UNCHANGED since round 1 — RE-SCAN",
    }
    for k in ["followup", "on_changed", "on_touched_file", "on_preexisting"]:
        c = buckets[k]
        print(f"  {c:3d} ({100*c/tot:3.0f}%)  {labels[k]}")
    print(f"  ---\n  {tot} total round>=2 {reviewer} inline threads")

    conv = buckets["followup"] + buckets["on_changed"]
    print(f"\n  CONVERGING (reacting to the change): {conv}/{tot} = {100*conv/tot:.0f}%")
    print(f"  RE-SCAN (fresh issues on old code):  "
          f"{buckets['on_preexisting']}/{tot} = {100*buckets['on_preexisting']/tot:.0f}%")

    conv_vals = [r[5] for r in rows if r[2] in ("followup", "on_changed")
                 and r[5] is not None]
    rescan_vals = [r[5] for r in rows if r[2] == "on_preexisting" and r[5] is not None]
    if conv_vals and rescan_vals:
        print(f"\n  mean value — converging={st.mean(conv_vals):.2f} "
              f"(n={len(conv_vals)}) | re-scan={st.mean(rescan_vals):.2f} "
              f"(n={len(rescan_vals)})")

    print("\n" + "=" * 90)
    print("RE-SCAN examples (round>=2 finding on code unchanged since round 1)")
    print("=" * 90)
    for n, r, cls, path, res, val, gist in rows:
        if cls == "on_preexisting":
            print(f"  #{n} r{r} v{val} resolved={res} {path}: {gist[:70]}")
    print("\n" + "=" * 90)
    print("CONVERGING examples (on the pushed change / follow-up)")
    print("=" * 90)
    for n, r, cls, path, _res, val, gist in rows:
        if cls in ("followup", "on_changed"):
            print(f"  #{n} r{r} v{val} [{cls}] {path}: {gist[:60]}")


if __name__ == "__main__":
    main()
