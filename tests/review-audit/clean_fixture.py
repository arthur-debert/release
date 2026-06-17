#!/usr/bin/env python3
"""Test helper for denoise.bats: run audit_config.clean() on a fixture file.

Imports clean() from the live skill script (no copy — so the test can never
drift from the code it guards) and writes the de-noised result to stdout. The
BATS suite measures the shrink ratio and asserts the outcome markers survive.

Usage: clean_fixture.py <fixture.md>
"""
import pathlib
import sys

SCRIPTS = (pathlib.Path(__file__).resolve().parents[2]
           / "skills" / "review-audit" / "scripts")
sys.path.insert(0, str(SCRIPTS))

from audit_config import clean  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("usage: clean_fixture.py <fixture.md>")

raw = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
sys.stdout.write(clean(raw))
