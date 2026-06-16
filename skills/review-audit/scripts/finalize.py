#!/usr/bin/env python3
"""Stage 3 — merge each thread's diff_hunk (the code the comment is about)
from the raw archive into the already-enriched slim files, WITHOUT clobbering
the GraphQL resolved/outdated flags. Truncates hunks to keep slim compact.
Run AFTER enrich.py. Repo-agnostic.

Usage:
  finalize.py [--dir PATH]
"""
import argparse
import json

from audit_config import resolve_dir

HUNK_MAX = 700


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir")
    a = ap.parse_args()
    out = resolve_dir(a.dir)
    raw, slim = out / "raw", out / "slim"

    n_hunks = 0
    for p in sorted(slim.glob("pr-*.json"),
                    key=lambda x: int(x.stem.split("-")[1])):
        rec = json.loads(p.read_text())
        if not rec.get("threads"):
            continue
        archive = json.loads((raw / p.name).read_text())
        hunks = {c["id"]: c.get("diff_hunk", "") for c in archive["inline"]}
        for t in rec["threads"]:
            h = hunks.get(t.get("root_id"), "")
            if h:
                t["diff_hunk"] = h[-HUNK_MAX:]
                n_hunks += 1
        p.write_text(json.dumps(rec, indent=1))
    print(f"added diff_hunk to {n_hunks} threads")


if __name__ == "__main__":
    main()
