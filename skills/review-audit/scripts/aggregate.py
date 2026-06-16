#!/usr/bin/env python3
"""Stage 6 — join per-PR LLM verdicts (verdicts/) with mechanical metrics into
the synthesis tables + summary.json (the stable machine-readable contract for
downstream feedback-loop automation). Repo-agnostic: the primary reviewer and
any secondaries are DERIVED from the data, not hardcoded.

Run AFTER the stage-5 Workflow has written verdicts/pr-*.json.

Usage:
  aggregate.py [--dir PATH] [--primary NAME]
    --primary  the strong/first reviewer to measure secondaries against
               (default: the reviewer with the most findings)
"""
import argparse
import collections
import json
import statistics as st

from audit_config import resolve_dir


def pct(num, den):
    return f"{100*num/den:.0f}%" if den else "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir")
    ap.add_argument("--primary", help="primary reviewer to compare against")
    a = ap.parse_args()
    out = resolve_dir(a.dir)
    vdir = out / "verdicts"

    metrics = {}
    for line in (out / "metrics.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            metrics[row["number"]] = row
    verdicts = {}
    for p in vdir.glob("pr-*.json"):
        try:
            d = json.loads(p.read_text())
            verdicts[d["number"]] = d
        except Exception as e:
            print("BAD", p.name, e)
    print(f"verdicts: {len(verdicts)} / metrics: {len(metrics)}\n")

    allf = []
    for n, d in verdicts.items():
        for finding in d.get("findings", []):
            finding["_pr"] = n
            allf.append(finding)

    by_r = collections.defaultdict(list)
    for finding in allf:
        by_r[finding["reviewer"]].append(finding)

    primary = a.primary or (max(by_r, key=lambda r: len(by_r[r])) if by_r else None)

    print("=" * 92)
    print("PER-REVIEWER FINDING QUALITY")
    print("=" * 92)
    print(f"{'reviewer':11s} {'finds':>5} {'mean_v':>6} {'useful>=2':>9} "
          f"{'noise=0':>7} {'halluc':>6} {'acted':>6} {'unique':>6} "
          f"{'crit(bl+imp)':>11}")
    for r, fs in sorted(by_r.items()):
        n = len(fs)
        mean_v = st.mean(x["value"] for x in fs)
        useful = sum(1 for x in fs if x["value"] >= 2)
        noise = sum(1 for x in fs if x["value"] == 0)
        halluc = sum(1 for x in fs if x["true_positive"] == "no")
        acted = sum(1 for x in fs if x["actioned"] == "changed")
        uniq = sum(1 for x in fs if x["novelty"] == "unique")
        crit = sum(1 for x in fs if x["severity"] in ("blocker", "important"))
        print(f"{r:11s} {n:5d} {mean_v:6.2f} {pct(useful, n):>9} "
              f"{pct(noise, n):>7} {pct(halluc, n):>6} {pct(acted, n):>6} "
              f"{pct(uniq, n):>6} {pct(crit, n):>11}")

    print("\n" + "=" * 92)
    print("CONVERGENCE (per reviewer x PR)")
    print("=" * 92)
    conv = collections.defaultdict(collections.Counter)
    netv = collections.defaultdict(list)
    for d in verdicts.values():
        for pr in d.get("per_reviewer", []):
            conv[pr["reviewer"]][pr["convergence"]] += 1
            netv[pr["reviewer"]].append(pr["net_value"])
    for r in sorted(conv):
        c = conv[r]
        nv = f"{st.mean(netv[r]):.2f}" if netv[r] else "-"
        print(f"{r:11s} {dict(c)}  mean_net_value/PR={nv}")

    print("\n" + "=" * 92)
    print("CATEGORY MIX per reviewer (share of findings)")
    print("=" * 92)
    for r, fs in sorted(by_r.items()):
        cat = collections.Counter(x["category"] for x in fs)
        tot = len(fs)
        top = ", ".join(f"{k} {pct(v, tot)}" for k, v in cat.most_common(6))
        print(f"{r:11s} {top}")

    # secondaries = every reviewer that isn't the primary
    print("\n" + "=" * 92)
    print(f"SECOND-REVIEWER MARGINAL VALUE (vs primary '{primary}': "
          "unique useful findings each secondary added)")
    print("=" * 92)
    for second in sorted(by_r):
        if second == primary:
            continue
        fs = by_r[second]
        uniq_useful = [x for x in fs
                       if x["novelty"] == "unique" and x["value"] >= 2]
        dup = sum(1 for x in fs if x["novelty"] == "caught_elsewhere")
        prs = len({x["_pr"] for x in fs})
        ratio = f"{len(uniq_useful)/prs:.2f}" if prs else "-"
        print(f"{second:11s}: {len(fs)} findings over {prs} PRs | "
              f"unique&useful={len(uniq_useful)} | "
              f"redundant(caught_elsewhere)={dup} | "
              f"unique-useful/PR={ratio}")

    print("\n" + "=" * 92)
    print("HIGH-VALUE CATCHES (value=3, critical, true-positive)")
    print("=" * 92)
    saves = [x for x in allf if x["value"] == 3
             and x["severity"] in ("blocker", "important")
             and x["true_positive"] == "yes"]
    saves.sort(key=lambda x: x["_pr"])
    for x in saves:
        print(f"  #{x['_pr']} [{x['reviewer']}/{x['category']}] {x.get('gist', '')}")
    print(f"  total high-value catches: {len(saves)}")

    print("\n" + "=" * 92)
    print("WORST NOISE (false positives / value 0)")
    print("=" * 92)
    fp = [x for x in allf if x["true_positive"] == "no"
          or x["category"] == "false_positive"]
    by_rev_fp = collections.Counter(x["reviewer"] for x in fp)
    print("false-positive count by reviewer:", dict(by_rev_fp))
    for x in sorted(fp, key=lambda x: x["_pr"])[:12]:
        print(f"  #{x['_pr']} [{x['reviewer']}] {x.get('gist', '')}")

    summary = dict(
        repo_verdicts=len(verdicts),
        primary=primary,
        per_reviewer={r: dict(
            findings=len(fs),
            mean_value=round(st.mean(x["value"] for x in fs), 2),
            useful_pct=round(100 * sum(x["value"] >= 2 for x in fs) / len(fs), 1),
            noise_pct=round(100 * sum(x["value"] == 0 for x in fs) / len(fs), 1),
            halluc_pct=round(
                100 * sum(x["true_positive"] == "no" for x in fs) / len(fs), 1),
            acted_pct=round(
                100 * sum(x["actioned"] == "changed" for x in fs) / len(fs), 1),
            unique_pct=round(
                100 * sum(x["novelty"] == "unique" for x in fs) / len(fs), 1),
            convergence=dict(conv[r]),
            mean_net_value=round(st.mean(netv[r]), 2) if netv[r] else None)
            for r, fs in by_r.items()},
        high_value_catches=len(saves),
        false_positives=dict(by_rev_fp))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\nwrote summary.json")


if __name__ == "__main__":
    main()
